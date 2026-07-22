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
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Protocol

from smm.config.loader import LoadedConfig
from smm.core.errors import DataValidationError
from smm.domain.enums import MarketRegime
from smm.features.pipeline import run_features
from smm.features.snapshot import snapshot_path, write_snapshot
from smm.report.csv_writer import render_csv
from smm.report.manifest import build_manifest, render_manifest
from smm.report.markdown_writer import render_markdown
from smm.report.rows import BUCKET_ORDER, build_report_rows
from smm.scanner.engine import scan_session
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


def sanitize_path_segment(value: str, *, label: str) -> str:
    """Reject anything that is not a safe single path component.

    M4 ADR §3 requires both `strategy_version` and `config_hash` to be
    sanitized before use as artifact-root directory names -- a `/` or `..`
    in either would escape the intended root rather than just fail to make
    a valid directory name.
    """
    if not value or not _SAFE_SEGMENT.match(value):
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
) -> DailyRunResult:
    """Execute one M4 daily task. ``root`` is the caller-resolved,
    already-sanitized artifact root for this strategy_version/config_hash.
    """
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
