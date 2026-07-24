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
    build_shadow_manifest,
    render_manifest,
)

_FORBIDDEN_KEY_SUBSTRINGS = ("wall_clock", "timestamp", "run_id", "tmp", "temp")
_SHADOW_ARTIFACT_HASHES = {
    "report_csv": "a" * 64,
    "report_markdown": "b" * 64,
    "features_snapshot": "c" * 64,
    "portfolio_snapshot": "d" * 64,
    "circuit_state": "e" * 64,
    "risk_decisions": "f" * 64,
    "market_data_verifications": "1" * 64,
}
_DEFAULT_SHADOW_ARTIFACT_HASHES = object()


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
        '"market_event_snapshot":null,'
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


def test_shadow_manifest_requires_all_canonical_artifacts_and_identity() -> None:
    manifest = _shadow_manifest()

    assert manifest["execution_mode"] == "shadow"
    assert manifest["artifacts"] == _shadow_artifact_hashes()
    assert manifest["circuit_state_identity"] == "f" * 64
    assert manifest["reproduction_contract"] == {
        "conditionally_excluded_fields": list(CONDITIONALLY_EXCLUDED_FIELDS),
    }


@pytest.mark.parametrize(
    ("artifact_key", "invalid_hash"),
    [
        ("report_csv", ""),
        ("report_markdown", "A" * 64),
        ("features_snapshot", "a" * 63),
        ("portfolio_snapshot", "g" * 64),
        ("circuit_state", None),
        ("risk_decisions", 1),
    ],
)
def test_shadow_manifest_rejects_invalid_hash_for_every_artifact(
    artifact_key: str, invalid_hash: object
) -> None:
    artifact_hashes = _shadow_artifact_hashes()
    artifact_hashes[artifact_key] = invalid_hash

    with pytest.raises(DataValidationError, match=f"artifact hash for {artifact_key}"):
        _shadow_manifest(artifact_hashes=artifact_hashes)


@pytest.mark.parametrize("identity", ["", "F" * 64, "f" * 63, None, 1])
def test_shadow_manifest_rejects_invalid_circuit_state_identity(identity: object) -> None:
    with pytest.raises(DataValidationError, match="circuit_state_identity"):
        _shadow_manifest(circuit_state_identity=identity)


@pytest.mark.parametrize(
    "artifact_hashes",
    [
        {
            key: value
            for key, value in _SHADOW_ARTIFACT_HASHES.items()
            if key != "risk_decisions"
        },
        {**_SHADOW_ARTIFACT_HASHES, "extra": "e" * 64},
    ],
)
def test_shadow_manifest_rejects_missing_or_extra_artifact_keys(
    artifact_hashes: dict[str, object],
) -> None:
    with pytest.raises(DataValidationError, match="shadow manifest artifact keys"):
        _shadow_manifest(artifact_hashes=artifact_hashes)


@pytest.mark.parametrize(
    "artifact_hashes",
    [
        None,
        list(_SHADOW_ARTIFACT_HASHES),
    ],
)
def test_shadow_manifest_rejects_non_mapping_artifact_hashes(artifact_hashes: object) -> None:
    with pytest.raises(DataValidationError, match="must be a mapping"):
        _shadow_manifest(artifact_hashes=artifact_hashes)


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


def _shadow_artifact_hashes() -> dict[str, object]:
    return dict(_SHADOW_ARTIFACT_HASHES)


def _shadow_manifest(
    *,
    artifact_hashes: object = _DEFAULT_SHADOW_ARTIFACT_HASHES,
    circuit_state_identity: object = "f" * 64,
) -> dict:
    return build_shadow_manifest(
        as_of=date(2024, 6, 10),
        strategy_version="SMM-V1.0.0",
        config_hash="abc123",
        regime=MarketRegime.RISK_ON,
        provider_source="synthetic",
        universe_snapshot_id="2024-06-10_sp500_ndx",
        git_commit="deadbeef",
        transition_batch={
            "as_of": "2024-06-10",
            "transition_count": 0,
            "batch_digest": "x",
        },
        artifact_hashes=(
            _shadow_artifact_hashes()
            if artifact_hashes is _DEFAULT_SHADOW_ARTIFACT_HASHES
            else artifact_hashes
        ),
        circuit_state_identity=circuit_state_identity,
    )
