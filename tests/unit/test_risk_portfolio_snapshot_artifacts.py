"""M7 shadow-only external PortfolioSnapshot input/artifact contract."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

from smm.core.errors import DataValidationError
from smm.domain.models import PortfolioSnapshot
from smm.risk.portfolio_snapshot_artifacts import (
    load_shadow_portfolio_snapshot,
    portfolio_snapshot_artifact_path,
    portfolio_snapshot_artifact_sha256,
    render_portfolio_snapshot_artifact,
    write_portfolio_snapshot_artifact,
)

AS_OF = date(2024, 6, 20)
STRATEGY_VERSION = "SMM-V1.1.0"
CONFIG_HASH = "a" * 64


def snapshot(**updates: object) -> PortfolioSnapshot:
    values: dict[str, object] = {
        "as_of": AS_OF,
        "account_equity": "100000",
        "available_cash": "100000",
        "gross_exposure_capital": "0",
        "portfolio_initial_risk": "0",
        "sector_initial_risk": {},
        "cluster_initial_risk": {},
        "open_symbols": frozenset(),
        "reserved_signal_ids": frozenset(),
        "strategy_version": STRATEGY_VERSION,
        "config_hash": CONFIG_HASH,
    }
    values.update(updates)
    return PortfolioSnapshot(**values)


def write_input(path: Path, value: PortfolioSnapshot, *, pretty: bool) -> None:
    payload = value.model_dump(mode="json")
    if pretty:
        path.write_text(
            json.dumps(dict(reversed(tuple(payload.items()))), indent=2) + "\n",
            encoding="utf-8",
        )
    else:
        path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")


def test_shadow_loader_normalizes_reformatted_input_for_artifact_idempotency(
    tmp_path: Path,
) -> None:
    first_input = tmp_path / "snapshot-first.json"
    reformatted_input = tmp_path / "snapshot-reformatted.json"
    expected = snapshot()
    write_input(first_input, expected, pretty=False)
    write_input(reformatted_input, expected, pretty=True)

    first = load_shadow_portfolio_snapshot(
        first_input,
        as_of=AS_OF,
        strategy_version=STRATEGY_VERSION,
        config_hash=CONFIG_HASH,
    )
    reformatted = load_shadow_portfolio_snapshot(
        reformatted_input,
        as_of=AS_OF,
        strategy_version=STRATEGY_VERSION,
        config_hash=CONFIG_HASH,
    )

    target = write_portfolio_snapshot_artifact(tmp_path / "runs", first)
    (target.parent / "manifest.json").write_text("{}\n", encoding="utf-8")

    assert first == reformatted == expected
    assert target == portfolio_snapshot_artifact_path(tmp_path / "runs", AS_OF)
    assert target == tmp_path / "runs" / AS_OF.isoformat() / "portfolio_snapshot.json"
    assert target.read_text(encoding="utf-8") == render_portfolio_snapshot_artifact(first)
    assert portfolio_snapshot_artifact_sha256(first) == hashlib.sha256(
        target.read_bytes()
    ).hexdigest()
    assert write_portfolio_snapshot_artifact(tmp_path / "runs", reformatted) == target


def test_shadow_loader_rejects_snapshot_with_non_x_identity(tmp_path: Path) -> None:
    input_file = tmp_path / "snapshot.json"
    write_input(input_file, snapshot(as_of=date(2024, 6, 19)), pretty=False)

    with pytest.raises(DataValidationError, match="portfolio snapshot identity"):
        load_shadow_portfolio_snapshot(
            input_file,
            as_of=AS_OF,
            strategy_version=STRATEGY_VERSION,
            config_hash=CONFIG_HASH,
        )


def test_shadow_loader_rejects_invalid_external_json_without_fallback(tmp_path: Path) -> None:
    input_file = tmp_path / "snapshot.json"
    input_file.write_text('{"account_equity":100000}', encoding="utf-8")

    with pytest.raises(DataValidationError, match="invalid portfolio snapshot"):
        load_shadow_portfolio_snapshot(
            input_file,
            as_of=AS_OF,
            strategy_version=STRATEGY_VERSION,
            config_hash=CONFIG_HASH,
        )


def test_snapshot_artifact_sorts_set_backed_identity_facts(tmp_path: Path) -> None:
    target = write_portfolio_snapshot_artifact(
        tmp_path,
        snapshot(
            open_symbols=frozenset({"ZZZ", "AAA"}),
            reserved_signal_ids=frozenset({"signal-z", "signal-a"}),
        ),
    )

    artifact = json.loads(target.read_text(encoding="utf-8"))

    assert artifact["open_symbols"] == ["AAA", "ZZZ"]
    assert artifact["reserved_signal_ids"] == ["signal-a", "signal-z"]


def test_snapshot_artifact_conflict_does_not_overwrite_existing_canonical_bytes(
    tmp_path: Path,
) -> None:
    original = snapshot()
    target = write_portfolio_snapshot_artifact(tmp_path, original)
    before = target.read_bytes()

    with pytest.raises(DataValidationError, match="conflicting portfolio snapshot artifact"):
        write_portfolio_snapshot_artifact(
            tmp_path,
            snapshot(available_cash="99999"),
        )

    assert target.read_bytes() == before


def test_snapshot_artifact_cannot_be_added_to_completed_session(tmp_path: Path) -> None:
    day_dir = tmp_path / AS_OF.isoformat()
    day_dir.mkdir()
    (day_dir / "manifest.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(DataValidationError, match="cannot add PortfolioSnapshot artifact"):
        write_portfolio_snapshot_artifact(tmp_path, snapshot())

    assert not portfolio_snapshot_artifact_path(tmp_path, AS_OF).exists()
