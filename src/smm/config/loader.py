"""Load and hash strategy YAML configs.

Config hash algorithm
---------------------
1. Parse YAML into a plain dict.
2. Validate with StrategyConfig (pydantic).
3. Serialize the validated model with ``model_dump(mode="json")``.
4. Dump JSON with ``sort_keys=True``, separators ``(",", ":")``, UTF-8.
5. SHA-256 hex digest of that UTF-8 byte string.

Same logical config ⇒ same hash across machines (no float formatting drift
beyond JSON's default for values that round-trip via pydantic).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from smm.config.schema import StrategyConfig
from smm.core.errors import ConfigError

# Default relative to repository root when installed from source checkout.
_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_PATH = _REPO_ROOT / "configs" / "smm_v1_0_0.yaml"


@dataclass(frozen=True, slots=True)
class LoadedConfig:
    """Validated strategy config plus stable content hash."""

    config: StrategyConfig
    config_hash: str
    path: Path | None = None

    @property
    def version(self) -> str:
        return self.config.strategy.version


def compute_config_hash(config: StrategyConfig) -> str:
    """Return SHA-256 hex digest of canonical JSON for ``config``."""
    # Optional M6 fields are absent from the frozen V1.0 YAML. Excluding their
    # ``None`` placeholders preserves the historical V1.0 config identity.
    payload = config.model_dump(mode="json", exclude_none=True)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _parse_yaml(text: str) -> dict[str, Any]:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError("config root must be a mapping")
    return data


def load_config_from_mapping(data: dict[str, Any], *, path: Path | None = None) -> LoadedConfig:
    try:
        config = StrategyConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(str(exc)) from exc
    return LoadedConfig(config=config, config_hash=compute_config_hash(config), path=path)


def load_config_from_path(path: Path | str) -> LoadedConfig:
    config_path = Path(path)
    if not config_path.is_file():
        raise ConfigError(f"config file not found: {config_path}")
    text = config_path.read_text(encoding="utf-8")
    data = _parse_yaml(text)
    return load_config_from_mapping(data, path=config_path.resolve())


def load_config(path: Path | str | None = None) -> LoadedConfig:
    """Load config from ``path`` or the repo default ``configs/smm_v1_0_0.yaml``."""
    return load_config_from_path(path or DEFAULT_CONFIG_PATH)
