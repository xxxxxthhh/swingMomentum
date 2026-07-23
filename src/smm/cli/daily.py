"""M4 `run-daily` orchestration (ADR §1/§2/§3/§6/§7).

One public seam: resolve config/provider/universe/calendar -> fetch/read +
validated bars -> compute features + regime in memory -> scan_session ->
append_transitions (always, even empty) -> write the report bundle -> write
the completion manifest last. `smm ingest`/`smm features` stay diagnostic;
this module is the only thing a real daily task or the N-day replay gate
goes through.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Protocol

import pyarrow as pa
import pyarrow.parquet as pq

from smm.config.loader import LoadedConfig
from smm.core.errors import DataValidationError
from smm.domain.enums import MarketRegime
from smm.features.cross_section import ScoredSymbol
from smm.features.engine import SymbolFeatures
from smm.features.pipeline import run_features
from smm.features.snapshot import snapshot_path, write_snapshot
from smm.paper import (
    CircuitInputs,
    SplitActionHistory,
    circuit_state_identity,
    evaluate_circuit_state,
    rebuild_print_bars,
    render_circuit_state_artifact,
    risk_execution_context_for,
    write_circuit_state_artifact,
)
from smm.report.csv_writer import render_csv
from smm.report.format import dump_json_deterministic
from smm.report.manifest import (
    ExecutionMode,
    build_manifest,
    build_shadow_manifest,
    render_manifest,
)
from smm.report.markdown_writer import render_markdown
from smm.report.rows import BUCKET_ORDER, build_report_rows
from smm.risk import (
    EvaluationFacts,
    TriggerCandidateSource,
    build_candidate_evaluation_inputs,
    evaluate_risk_batch,
    load_shadow_portfolio_snapshot,
    partition_trigger_backlog,
    portfolio_snapshot_artifact_sha256,
    project_risk_decisions_to_transitions,
    write_portfolio_snapshot_artifact,
    write_risk_decisions_artifact,
)
from smm.risk.artifacts import render_risk_decisions_artifact
from smm.scanner.engine import scan_session
from smm.signals.lifecycle import SignalTransition
from smm.signals.store import (
    append_transitions,
    assert_session_continuity,
    latest_sealed_as_of,
    read_batch_seals,
    read_transitions,
)

_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")


class _Provider(Protocol):
    def get_daily_bars(self, symbol: str, start: date, end: date): ...
    def get_calendar(self, start: date, end: date) -> list[date]: ...


def validate_run_daily_mode(
    mode: ExecutionMode | str,
    portfolio_snapshot: Path | str | None,
) -> ExecutionMode:
    """Validate the explicit M7 CLI mode/input coupling before any run writes.

    The snapshot is an operational input only; it never chooses a config or
    changes strategy identity.  Rejecting both invalid directions prevents an
    operator from silently receiving an MVP-A bundle when they intended shadow.
    """

    try:
        selected = ExecutionMode(mode)
    except (TypeError, ValueError) as exc:
        raise DataValidationError(f"unsupported execution mode: {mode!r}") from exc

    if selected is ExecutionMode.PAPER:
        raise DataValidationError("paper mode is not implemented by the shadow-only Slice 1")
    if selected is ExecutionMode.SHADOW:
        if portfolio_snapshot is None:
            raise DataValidationError(
                "shadow mode requires a portfolio snapshot (--portfolio-snapshot)"
            )
        return selected
    if portfolio_snapshot is not None:
        raise DataValidationError("portfolio snapshot requires --mode shadow")
    return selected


def sanitize_path_segment(value: str, *, label: str) -> str:
    """Reject anything that is not a safe single path component.

    M4 ADR §3 requires both `strategy_version` and `config_hash` to be
    sanitized before use as artifact-root directory names -- a `/` or `..`
    in either would escape the intended root rather than just fail to make
    a valid directory name.
    """
    if value in {".", ".."} or not value or not _SAFE_SEGMENT.match(value):
        raise DataValidationError(
            f"{label} is not a safe artifact-root path segment: {value!r}"
        )
    return value


def artifact_root(base: Path, *, strategy_version: str, config_hash: str) -> Path:
    return (
        Path(base)
        / sanitize_path_segment(strategy_version, label="strategy_version")
        / sanitize_path_segment(config_hash, label="config_hash")
    )


def git_commit_sha(cwd: Path | None = None) -> str:
    """The checked-out commit, or the literal ``"unknown"`` outside git.

    Audit-only (ADR §3): never fabricated, never a reason to refuse to run.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=cwd,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    sha = result.stdout.strip()
    return sha if result.returncode == 0 and sha else "unknown"


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class DailyRunResult:
    as_of: date
    regime: MarketRegime
    row_count: int
    bucket_counts: dict[str, int]
    manifest_path: Path
    skipped_as_noop: bool


def run_daily(
    provider: _Provider,
    *,
    session: date,
    symbols: list[str],
    sectors: dict[str, str],
    loaded: LoadedConfig,
    root: Path,
    provider_source: str,
    universe_snapshot_id: str = "",
    mode: ExecutionMode | str = ExecutionMode.MVP_A_SIGNAL,
    portfolio_snapshot: Path | str | None = None,
) -> DailyRunResult:
    """Execute one M4 daily task. ``root`` is the caller-resolved,
    already-sanitized artifact root for this strategy_version/config_hash.
    """
    selected_mode = validate_run_daily_mode(mode, portfolio_snapshot)
    if selected_mode is ExecutionMode.SHADOW:
        return _run_shadow_daily(
            provider,
            session=session,
            symbols=symbols,
            sectors=sectors,
            loaded=loaded,
            root=root,
            provider_source=provider_source,
            universe_snapshot_id=universe_snapshot_id,
            portfolio_snapshot=portfolio_snapshot,
        )

    # Keep the accepted M4 path below byte-for-byte and behaviorally identical
    # when no richer explicit mode is selected.
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    day_dir = root / session.isoformat()
    manifest_file = day_dir / "manifest.json"

    latest_seal = latest_sealed_as_of(root)
    # The calendar must cover the continuity gate's need (back to, and
    # through, the latest seal -- which may be *after* `session` when this
    # call turns out to be an illegal backfill) as well as the scanner's
    # trailing trigger/hard-filter window. Three different reach requirements.
    bars_start = session - timedelta(days=int(loaded.config.features.min_history_bars * 1.6) + 30)
    calendar_start = min(bars_start, (latest_seal or session) - timedelta(days=10))
    calendar_end = max(session, latest_seal) if latest_seal is not None else session
    sessions = provider.get_calendar(calendar_start, calendar_end)
    assert_session_continuity(root, as_of=session, sessions=sessions)

    member_symbols = sorted({s.upper() for s in symbols})
    feature_run = run_features(
        provider, as_of=session, symbols=member_symbols, sectors=sectors, loaded=loaded
    )

    bars_by_symbol = {
        symbol: provider.get_daily_bars(symbol, bars_start, session) for symbol in member_symbols
    }

    prior_transitions = read_transitions(root)
    scan_result = scan_session(
        as_of=session,
        sessions=sessions,
        symbols=member_symbols,
        features=feature_run.features,
        bars_by_symbol=bars_by_symbol,
        loaded=loaded,
        prior_transitions=prior_transitions,
    )

    # Step 2 (§6): seal the batch, even when empty. append_transitions is
    # itself idempotent / fail-closed against a prior sealed batch for this
    # as_of, so a same-input rerun cannot silently diverge here.
    append_transitions(
        root,
        scan_result.transitions,
        as_of=session,
        strategy_version=loaded.version,
        config_hash=loaded.config_hash,
    )
    seal = read_batch_seals(root)[session]
    all_transitions = read_transitions(root)

    rows = build_report_rows(
        as_of=session,
        scan_result=scan_result,
        all_transitions=all_transitions,
        features=feature_run.features,
        cross_section=feature_run.cross_section,
        regime=feature_run.regime,
        strategy_version=loaded.version,
        config_hash=loaded.config_hash,
    )
    csv_text = render_csv(rows)
    markdown_text = render_markdown(
        rows,
        as_of=session,
        strategy_version=loaded.version,
        config_hash=loaded.config_hash,
        regime=feature_run.regime,
    )

    tmp_dir = Path(tempfile.mkdtemp(dir=root, prefix=f".{session.isoformat()}.tmp-"))
    try:
        (tmp_dir / "report.csv").write_text(csv_text, encoding="utf-8")
        (tmp_dir / "report.md").write_text(markdown_text, encoding="utf-8")
        write_snapshot(
            tmp_dir,
            as_of=session,
            cross_section=feature_run.cross_section,
            features=feature_run.features,
            excluded=feature_run.excluded,
            regime=feature_run.regime,
            strategy_version=loaded.version,
            config_hash=loaded.config_hash,
            return_windows=loaded.config.features.return_windows,
            benchmarks={loaded.config.market_regime.benchmark.upper()}
            | {etf.upper() for etf in loaded.config.sector_benchmarks.values()},
        )
        snapshot_bytes = snapshot_path(tmp_dir, session).read_bytes()

        manifest = build_manifest(
            as_of=session,
            strategy_version=loaded.version,
            config_hash=loaded.config_hash,
            regime=feature_run.regime,
            provider_source=provider_source,
            universe_snapshot_id=universe_snapshot_id,
            git_commit=git_commit_sha(),
            transition_batch={
                "as_of": seal.as_of.isoformat(),
                "transition_count": seal.transition_count,
                "batch_digest": seal.batch_digest,
            },
            artifact_hashes={
                "report_csv": _sha256_text(csv_text),
                "report_markdown": _sha256_text(markdown_text),
                "features_snapshot": _sha256_bytes(snapshot_bytes),
            },
        )
        manifest_text = render_manifest(manifest)

        if manifest_file.exists():
            if manifest_file.read_text(encoding="utf-8") == manifest_text:
                return _result(session, feature_run.regime, rows, manifest_file, skipped=True)
            raise DataValidationError(
                f"completed daily task for {session} in {root} already exists with "
                "different content; M4 provides no --force or in-place rewrite"
            )

        if day_dir.exists():
            # Partial bundle from a crashed prior run -- no manifest was ever
            # confirmed there, so it is not "the" result, just regenerable.
            shutil.rmtree(day_dir)
        tmp_dir.rename(day_dir)
        manifest_file.write_text(manifest_text, encoding="utf-8")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return _result(session, feature_run.regime, rows, manifest_file, skipped=False)


def _run_shadow_daily(
    provider: _Provider,
    *,
    session: date,
    symbols: list[str],
    sectors: dict[str, str],
    loaded: LoadedConfig,
    root: Path,
    provider_source: str,
    universe_snapshot_id: str,
    portfolio_snapshot: Path | str | None,
) -> DailyRunResult:
    """Assemble the accepted M7 shadow-only steps 3--7 for one session.

    Shadow deliberately has no Paper ledger.  Its explicitly supplied X
    snapshot provides the portfolio state, while the circuit's absent Paper
    session facts are the neutral zero facts frozen by this bounded path.  No
    source path is persisted: the canonical snapshot artifact is the audit
    fact that later enters the manifest.
    """

    if portfolio_snapshot is None:  # guarded by validate_run_daily_mode
        raise DataValidationError(
            "shadow mode requires a portfolio snapshot (--portfolio-snapshot)"
        )

    root = Path(root)
    day_dir = root / session.isoformat()
    manifest_file = day_dir / "manifest.json"
    snapshot = load_shadow_portfolio_snapshot(
        portfolio_snapshot,
        as_of=session,
        strategy_version=loaded.version,
        config_hash=loaded.config_hash,
    )
    _reject_completed_non_shadow_bundle(manifest_file)

    latest_seal = latest_sealed_as_of(root)
    bars_start = session - timedelta(days=int(loaded.config.features.min_history_bars * 1.6) + 30)
    calendar_start = min(bars_start, (latest_seal or session) - timedelta(days=10))
    calendar_end = max(session, latest_seal) if latest_seal is not None else session
    sessions = provider.get_calendar(calendar_start, calendar_end)
    assert_session_continuity(root, as_of=session, sessions=sessions)

    member_symbols = sorted({symbol.upper() for symbol in symbols})
    feature_run = run_features(
        provider, as_of=session, symbols=member_symbols, sectors=sectors, loaded=loaded
    )
    bars_by_symbol = {
        symbol: provider.get_daily_bars(symbol, bars_start, session) for symbol in member_symbols
    }

    # Preserve the exact start-of-run ledger view.  In particular, an exact
    # rerun must reconstruct X's already-sealed scan and risk batches from
    # facts before X, not accidentally treat X's scanner output as backlog.
    all_persisted_transitions = read_transitions(root)
    run_start_transitions = tuple(
        transition for transition in all_persisted_transitions if transition.as_of < session
    )
    max_age_sessions = loaded.config.risk.trigger_backlog_max_age_sessions
    if type(max_age_sessions) is not int:
        raise DataValidationError("shadow mode requires frozen trigger backlog age configuration")
    backlog = partition_trigger_backlog(
        run_start_transitions,
        evaluation_as_of=session,
        strategy_version=loaded.version,
        config_hash=loaded.config_hash,
        sessions=sessions,
        max_age_sessions=max_age_sessions,
    )

    circuit_state = evaluate_circuit_state(
        CircuitInputs(
            as_of=session,
            strategy_version=loaded.version,
            config_hash=loaded.config_hash,
            realized_loss_r_for_session=Decimal("0"),
            marked_equity=snapshot.account_equity,
            prior_high_water_equity=snapshot.account_equity,
            integrity_halt=False,
        ),
        risk=loaded.config.risk,
    )
    evaluation = EvaluationFacts(
        as_of=session,
        regime=feature_run.regime,
        strategy_version=loaded.version,
        config_hash=loaded.config_hash,
    )
    candidate_sources = tuple(
        _load_trigger_candidate_source(
            provider,
            root=root,
            transition=transition,
            sessions=sessions,
            loaded=loaded,
        )
        for transition in backlog.eligible
    )
    candidate_inputs = build_candidate_evaluation_inputs(
        sources=candidate_sources,
        evaluation=evaluation,
        portfolio=snapshot,
        stop=loaded.config.stop,
        execution=loaded.config.execution,
    )
    risk_decisions = evaluate_risk_batch(
        candidate_inputs.candidates,
        candidate_inputs.portfolio,
        loaded.config.risk,
        execution_context=risk_execution_context_for(circuit_state),
    )
    risk_transitions = project_risk_decisions_to_transitions(
        risk_decisions,
        run_start_transitions,
    )

    # This is intentionally after the risk evaluation.  The current scan sees
    # the same start-of-run state but its newly born X triggers cannot feed X's
    # risk batch (ADR §2/§3 Option 1b).
    scan_result = scan_session(
        as_of=session,
        sessions=sessions,
        symbols=member_symbols,
        features=feature_run.features,
        bars_by_symbol=bars_by_symbol,
        loaded=loaded,
        prior_transitions=run_start_transitions,
    )
    complete_transitions = (
        *backlog.expirations,
        *risk_transitions,
        *scan_result.transitions,
    )

    # All causal inputs and the complete X transition multiset are validated
    # before any write.  A later artifact failure leaves no manifest and can
    # only be recovered by reproducing this exact sealed batch.
    root.mkdir(parents=True, exist_ok=True)
    append_transitions(
        root,
        complete_transitions,
        as_of=session,
        strategy_version=loaded.version,
        config_hash=loaded.config_hash,
    )
    seal = read_batch_seals(root)[session]
    all_transitions = read_transitions(root)
    rows = build_report_rows(
        as_of=session,
        scan_result=scan_result,
        all_transitions=all_transitions,
        features=feature_run.features,
        cross_section=feature_run.cross_section,
        regime=feature_run.regime,
        strategy_version=loaded.version,
        config_hash=loaded.config_hash,
    )
    csv_text = render_csv(rows)
    markdown_text = render_markdown(
        rows,
        as_of=session,
        strategy_version=loaded.version,
        config_hash=loaded.config_hash,
        regime=feature_run.regime,
    )

    tmp_dir = Path(tempfile.mkdtemp(dir=root, prefix=f".{session.isoformat()}.tmp-"))
    try:
        (tmp_dir / "report.csv").write_text(csv_text, encoding="utf-8")
        (tmp_dir / "report.md").write_text(markdown_text, encoding="utf-8")
        write_snapshot(
            tmp_dir,
            as_of=session,
            cross_section=feature_run.cross_section,
            features=feature_run.features,
            excluded=feature_run.excluded,
            regime=feature_run.regime,
            strategy_version=loaded.version,
            config_hash=loaded.config_hash,
            return_windows=loaded.config.features.return_windows,
            benchmarks={loaded.config.market_regime.benchmark.upper()}
            | {etf.upper() for etf in loaded.config.sector_benchmarks.values()},
        )
        snapshot_bytes = snapshot_path(tmp_dir, session).read_bytes()
        circuit_text = render_circuit_state_artifact(circuit_state)
        risk_text = render_risk_decisions_artifact(risk_decisions)
        artifact_hashes = {
            "report_csv": _sha256_text(csv_text),
            "report_markdown": _sha256_text(markdown_text),
            "features_snapshot": _sha256_bytes(snapshot_bytes),
            "portfolio_snapshot": portfolio_snapshot_artifact_sha256(snapshot),
            "circuit_state": _sha256_text(circuit_text),
            "risk_decisions": _sha256_text(risk_text),
        }
        manifest = build_shadow_manifest(
            as_of=session,
            strategy_version=loaded.version,
            config_hash=loaded.config_hash,
            regime=feature_run.regime,
            provider_source=provider_source,
            universe_snapshot_id=universe_snapshot_id,
            git_commit=git_commit_sha(),
            transition_batch={
                "as_of": seal.as_of.isoformat(),
                "transition_count": seal.transition_count,
                "batch_digest": seal.batch_digest,
            },
            artifact_hashes=artifact_hashes,
            circuit_state_identity=circuit_state_identity(circuit_state),
        )
        manifest_text = render_manifest(manifest)

        if manifest_file.exists():
            if manifest_file.read_text(encoding="utf-8") != manifest_text:
                raise DataValidationError(
                    f"completed shadow daily task for {session} in {root} already exists with "
                    "different content; mode and artifact shape are immutable"
                )
            _verify_shadow_artifacts(day_dir, artifact_hashes, session)
            return _result(session, feature_run.regime, rows, manifest_file, skipped=True)

        if day_dir.exists():
            # A prior crash without a completion manifest is regenerable; no
            # sealed success can be overwritten because the manifest is absent.
            shutil.rmtree(day_dir)
        tmp_dir.rename(day_dir)
        write_portfolio_snapshot_artifact(root, snapshot)
        write_circuit_state_artifact(root, circuit_state)
        write_risk_decisions_artifact(root, session, risk_decisions)
        _verify_shadow_artifacts(day_dir, artifact_hashes, session)
        manifest_file.write_text(manifest_text, encoding="utf-8")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return _result(session, feature_run.regime, rows, manifest_file, skipped=False)


def _reject_completed_non_shadow_bundle(manifest_file: Path) -> None:
    """Fail before sealing when an earlier mode completed this same session."""

    if not manifest_file.exists():
        return
    try:
        payload = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DataValidationError("completed session manifest is unreadable") from exc
    if not isinstance(payload, dict) or payload.get("execution_mode") != ExecutionMode.SHADOW.value:
        raise DataValidationError(
            "cannot switch a completed session into shadow mode; manifest mode is immutable"
        )


def _verify_shadow_artifacts(
    day_dir: Path,
    artifact_hashes: dict[str, str],
    session: date,
) -> None:
    filenames = {
        "report_csv": "report.csv",
        "report_markdown": "report.md",
        "features_snapshot": f"features_{session.isoformat()}.parquet",
        "portfolio_snapshot": "portfolio_snapshot.json",
        "circuit_state": "circuit_state.json",
        "risk_decisions": "risk_decisions.json",
    }
    for name, filename in filenames.items():
        target = day_dir / filename
        if not target.is_file() or _sha256_bytes(target.read_bytes()) != artifact_hashes[name]:
            raise DataValidationError(
                f"shadow artifact hash mismatch for {name} in completed session {session}"
            )


def _load_trigger_candidate_source(
    provider: _Provider,
    *,
    root: Path,
    transition: SignalTransition,
    sessions: Sequence[date],
    loaded: LoadedConfig,
) -> TriggerCandidateSource:
    """Retrieve the frozen D-side facts required by the public M7 adapter."""

    print_sessions = _trigger_print_sessions(sessions, transition)
    bars = tuple(
        provider.get_daily_bars(
            transition.symbol,
            transition.watchlist_entry,
            transition.as_of,
        )
    )
    if tuple(bar.date for bar in bars) != print_sessions:
        raise DataValidationError("trigger PrintBar source does not cover every provider session")

    fetch_history = getattr(provider, "fetch_split_action_history", None)
    if not callable(fetch_history):
        raise DataValidationError(
            "provider cannot retrieve verified split action history for shadow"
        )
    history = fetch_history(
        transition.symbol,
        transition.watchlist_entry,
        transition.as_of,
        observation_cutoff=transition.as_of,
        expected_sessions=print_sessions,
    )
    if not isinstance(history, SplitActionHistory):
        raise DataValidationError("provider returned invalid split action history for shadow")
    print_bars = rebuild_print_bars(bars, history=history)
    feature, score = _read_trigger_snapshot_facts(
        root,
        transition=transition,
        return_windows=loaded.config.features.return_windows,
    )
    provenance_text = dump_json_deterministic(history.model_dump(mode="json"))
    return TriggerCandidateSource(
        transition=transition,
        sessions=print_sessions,
        print_bars=print_bars,
        print_provenance_id=f"split-history:{_sha256_text(provenance_text)}",
        trigger_features=feature,
        trigger_score=score,
        feature_strategy_version=transition.strategy_version,
        feature_config_hash=transition.config_hash,
    )


def _trigger_print_sessions(
    sessions: Sequence[date],
    transition: SignalTransition,
) -> tuple[date, ...]:
    ordered = tuple(sessions)
    if ordered != tuple(sorted(set(ordered))):
        raise DataValidationError("provider calendar must be sorted with unique sessions")
    try:
        start = ordered.index(transition.watchlist_entry)
        end = ordered.index(transition.as_of)
    except ValueError as exc:
        raise DataValidationError(
            "provider calendar cannot retrieve trigger print coverage"
        ) from exc
    if start > end:
        raise DataValidationError("trigger watchlist entry follows trigger session")
    return ordered[start : end + 1]


def _read_trigger_snapshot_facts(
    root: Path,
    *,
    transition: SignalTransition,
    return_windows: Sequence[int],
) -> tuple[SymbolFeatures, ScoredSymbol]:
    target = snapshot_path(root / transition.as_of.isoformat(), transition.as_of)
    try:
        schema = pq.read_schema(target)
        rows = pq.read_table(target).to_pylist()
    except (OSError, ValueError, pa.ArrowException) as exc:
        raise DataValidationError("cannot read immutable trigger feature snapshot") from exc
    metadata = schema.metadata or {}
    expected = {
        b"smm_as_of": transition.as_of.isoformat().encode(),
        b"smm_strategy_version": transition.strategy_version.encode(),
        b"smm_config_hash": transition.config_hash.encode(),
    }
    if any(metadata.get(key) != value for key, value in expected.items()):
        raise DataValidationError("trigger feature snapshot identity does not match transition")
    matching_rows = [row for row in rows if row.get("symbol") == transition.symbol]
    if len(matching_rows) != 1:
        raise DataValidationError("trigger feature snapshot lacks one unambiguous symbol row")
    row = matching_rows[0]
    feature = SymbolFeatures(
        symbol=transition.symbol,
        as_of=transition.as_of,
        bar_count=_required_snapshot_int(row, "bar_count"),
        sma_fast=_optional_snapshot_float(row, "sma_fast"),
        sma_slow=_optional_snapshot_float(row, "sma_slow"),
        ema=_optional_snapshot_float(row, "ema"),
        sma_fast_slope=_optional_snapshot_float(row, "sma_fast_slope"),
        sma_slow_slope=_optional_snapshot_float(row, "sma_slow_slope"),
        atr=_optional_snapshot_float(row, "atr"),
        returns={
            window: _optional_snapshot_float(row, f"return_{window}")
            for window in return_windows
        },
        high_52w=_optional_snapshot_float(row, "high_52w"),
        distance_from_high=_optional_snapshot_float(row, "distance_from_high"),
        drawdown=_optional_snapshot_float(row, "drawdown"),
        extension_atr=_optional_snapshot_float(row, "extension_atr"),
        avg_dollar_volume=_optional_snapshot_float(row, "avg_dollar_volume"),
        close=_required_snapshot_float(row, "close"),
    )
    sector = row.get("sector")
    if not isinstance(sector, str) or not sector.strip():
        raise DataValidationError("trigger feature snapshot lacks a sector")
    score = ScoredSymbol(
        symbol=transition.symbol,
        sector=sector,
        rs_spy_short=_optional_snapshot_float(row, "rs_spy_short"),
        rs_spy_long=_optional_snapshot_float(row, "rs_spy_long"),
        rs_sector=_optional_snapshot_float(row, "rs_sector"),
        momentum_score=_optional_snapshot_float(row, "momentum_score"),
        relative_strength_score=_optional_snapshot_float(row, "relative_strength_score"),
        reason_codes=_snapshot_reason_codes(row),
    )
    return feature, score


def _required_snapshot_int(row: dict[str, object], field: str) -> int:
    value = row.get(field)
    if type(value) is not int or value < 1:
        raise DataValidationError(f"trigger feature snapshot has invalid {field}")
    return value


def _required_snapshot_float(row: dict[str, object], field: str) -> float:
    value = _optional_snapshot_float(row, field)
    if value is None:
        raise DataValidationError(f"trigger feature snapshot lacks {field}")
    return value


def _optional_snapshot_float(row: dict[str, object], field: str) -> float | None:
    raw = row.get(field)
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise DataValidationError(f"trigger feature snapshot has invalid {field}") from exc
    if not math.isfinite(value):
        raise DataValidationError(f"trigger feature snapshot has non-finite {field}")
    return value


def _snapshot_reason_codes(row: dict[str, object]) -> list[str]:
    raw = row.get("reason_codes")
    if raw is None or raw == "":
        return []
    if not isinstance(raw, str):
        raise DataValidationError("trigger feature snapshot has invalid reason_codes")
    return raw.split(",")


def _result(
    as_of: date,
    regime: MarketRegime,
    rows: list,
    manifest_file: Path,
    *,
    skipped: bool,
) -> DailyRunResult:
    counts = {bucket: 0 for bucket in BUCKET_ORDER}
    for row in rows:
        counts[row.bucket] += 1
    return DailyRunResult(
        as_of=as_of,
        regime=regime,
        row_count=len(rows),
        bucket_counts=counts,
        manifest_path=manifest_file,
        skipped_as_noop=skipped,
    )
