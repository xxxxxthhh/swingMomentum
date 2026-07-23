"""Manifest shape and determinism (M4 ADR §3/§6/§8)."""

from __future__ import annotations

from datetime import date

import pytest

from smm.core.errors import DataValidationError
from smm.domain.enums import MarketRegime
from smm.report.manifest import (
    CONDITIONALLY_EXCLUDED_FIELDS,
    ExecutionMode,
    build_manifest,
    render_manifest,
)

_FORBIDDEN_KEY_SUBSTRINGS = ("wall_clock", "timestamp", "run_id", "tmp", "temp")


def _manifest() -> dict:
    return build_manifest(
        as_of=date(2024, 6, 10),
        strategy_version="SMM-V1.0.0",
        config_hash="abc123",
        regime=MarketRegime.RISK_ON,
        provider_source="synthetic",
        universe_snapshot_id="2024-06-10_sp500_ndx",
        git_commit="deadbeef",
        transition_batch={"as_of": "2024-06-10", "transition_count": 0, "batch_digest": "x"},
        artifact_hashes={"report_csv": "aaa", "report_markdown": "bbb"},
    )


def test_manifest_carries_no_wall_clock_run_id_or_temp_path_field() -> None:
    def walk(node) -> list[str]:
        keys = []
        if isinstance(node, dict):
            for key, value in node.items():
                keys.append(key)
                keys.extend(walk(value))
        return keys

    keys = walk(_manifest())
    for key in keys:
        lowered = key.lower()
        assert not any(bad in lowered for bad in _FORBIDDEN_KEY_SUBSTRINGS), key


def test_manifest_declares_execution_mode_mvp_a_signal() -> None:
    assert _manifest()["execution_mode"] == "mvp_a_signal"


def test_manifest_default_mode_preserves_the_m4_bytes() -> None:
    assert render_manifest(_manifest()) == (
        '{"artifacts":{"report_csv":"aaa","report_markdown":"bbb"},'
        '"as_of":"2024-06-10","config_hash":"abc123",'
        '"execution_mode":"mvp_a_signal","git_commit":"deadbeef",'
        '"provider_source":"synthetic","regime":"risk_on",'
        '"reproduction_contract":{"conditionally_excluded_fields":["git_commit"]},'
        '"strategy_version":"SMM-V1.0.0",'
        '"transition_batch":{"as_of":"2024-06-10","batch_digest":"x",'
        '"transition_count":0},"universe_snapshot_id":"2024-06-10_sp500_ndx"}\n'
    )


@pytest.mark.parametrize(
    ("execution_mode", "expected"),
    [
        ("mvp_a_signal", "mvp_a_signal"),
        ("shadow", "shadow"),
        ("paper", "paper"),
        (ExecutionMode.PAPER, "paper"),
    ],
)
def test_manifest_renders_each_accepted_execution_mode(
    execution_mode: ExecutionMode | str, expected: str
) -> None:
    assert _manifest_with_mode(execution_mode)["execution_mode"] == expected


@pytest.mark.parametrize("execution_mode", ["", "intraday", None, 1])
def test_manifest_rejects_unknown_or_non_string_execution_mode(
    execution_mode: object,
) -> None:
    with pytest.raises(DataValidationError, match="execution mode"):
        _manifest_with_mode(execution_mode)


def test_manifest_declares_git_commit_as_conditionally_excluded() -> None:
    manifest = _manifest()
    assert manifest["reproduction_contract"]["conditionally_excluded_fields"] == list(
        CONDITIONALLY_EXCLUDED_FIELDS
    )
    assert "git_commit" in manifest


def test_render_manifest_is_byte_stable_across_calls() -> None:
    manifest = _manifest()
    assert render_manifest(manifest) == render_manifest(manifest)


def test_render_manifest_sorts_keys() -> None:
    text = render_manifest(_manifest())
    assert text.index('"artifacts"') < text.index('"as_of"') < text.index('"config_hash"')


def _manifest_with_mode(execution_mode: object) -> dict:
    return build_manifest(
        as_of=date(2024, 6, 10),
        strategy_version="SMM-V1.0.0",
        config_hash="abc123",
        regime=MarketRegime.RISK_ON,
        provider_source="synthetic",
        universe_snapshot_id="2024-06-10_sp500_ndx",
        git_commit="deadbeef",
        transition_batch={"as_of": "2024-06-10", "transition_count": 0, "batch_digest": "x"},
        artifact_hashes={"report_csv": "aaa", "report_markdown": "bbb"},
        execution_mode=execution_mode,
    )
