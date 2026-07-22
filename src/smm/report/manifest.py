"""Completion manifest assembly (M4 ADR §3/§6/§8).

Pure data assembly only -- computing artifact SHA-256s and discovering the
git commit both need the actual files/repo on disk, which is the daily-task
orchestrator's job (§6: manifest is written last, after the bundle is
already on disk). This module only fixes the manifest's *shape* and
guarantees nothing here can smuggle in a wall-clock value, a random run id,
or an absolute temp path -- the whole point of writing this before the
orchestrator exists.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from smm.domain.enums import MarketRegime
from smm.report.format import dump_json_deterministic

EXECUTION_MODE = "mvp_a_signal"

# §8: git_commit may be excluded from a byte-comparison only when the two
# compared roots are legitimately at different tree SHAs -- and only the
# comparison logic (not this manifest) may decide that. Declaring it here
# is the explicit opt-in §8 requires; comparing manifests at equal tree SHA
# must ignore this list and include git_commit anyway.
CONDITIONALLY_EXCLUDED_FIELDS = ("git_commit",)


def build_manifest(
    *,
    as_of: date,
    strategy_version: str,
    config_hash: str,
    regime: MarketRegime,
    provider_source: str,
    universe_snapshot_id: str,
    git_commit: str,
    transition_batch: dict[str, Any],
    artifact_hashes: dict[str, str],
) -> dict[str, Any]:
    """Assemble the manifest payload. No wall-clock, run id, or temp path
    field exists anywhere in this shape -- that is enforced by omission,
    not by a runtime check, so there is nothing here to accidentally leak.
    """
    return {
        "as_of": as_of.isoformat(),
        "strategy_version": strategy_version,
        "config_hash": config_hash,
        "execution_mode": EXECUTION_MODE,
        "regime": regime.value,
        "provider_source": provider_source,
        "universe_snapshot_id": universe_snapshot_id,
        "git_commit": git_commit,
        "transition_batch": transition_batch,
        "artifacts": artifact_hashes,
        "reproduction_contract": {
            "conditionally_excluded_fields": list(CONDITIONALLY_EXCLUDED_FIELDS),
        },
    }


def render_manifest(manifest: dict[str, Any]) -> str:
    return dump_json_deterministic(manifest)
