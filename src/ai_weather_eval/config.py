"""Configuration loading and lightweight validation."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Mapping

import yaml


class ConfigError(ValueError):
    """Raised when a project configuration is incomplete or invalid."""


_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand_environment(value: Any) -> Any:
    if isinstance(value, str):
        missing = sorted({name for name in _ENV_PATTERN.findall(value) if name not in os.environ})
        if missing:
            raise ConfigError("Missing environment variable(s): " + ", ".join(missing))
        return _ENV_PATTERN.sub(lambda match: os.environ[match.group(1)], value)
    if isinstance(value, list):
        return [_expand_environment(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand_environment(item) for key, item in value.items()}
    return value


def load_yaml(path: str | Path, *, expand_environment: bool = True) -> dict[str, Any]:
    """Load one YAML mapping and optionally expand ``${ENV_VAR}`` expressions."""

    config_path = Path(path)
    if not config_path.is_file():
        raise ConfigError(f"Configuration does not exist: {config_path}")

    with config_path.open("r", encoding="utf-8") as stream:
        loaded = yaml.safe_load(stream)

    if not isinstance(loaded, Mapping):
        raise ConfigError(f"Configuration root must be a mapping: {config_path}")

    config = dict(loaded)
    return _expand_environment(config) if expand_environment else config


def validate_experiment(config: Mapping[str, Any]) -> None:
    """Validate the stable top-level contract of an experiment configuration."""

    required = {"experiment", "period", "case_selection", "forecast_sampling", "verification"}
    missing = sorted(required.difference(config))
    if missing:
        raise ConfigError("Missing experiment section(s): " + ", ".join(missing))

    leads = config["forecast_sampling"].get("nominal_lead_hours", [])
    if not leads or leads != sorted(set(leads)) or any(
        not isinstance(lead, int) or lead <= 0 for lead in leads
    ):
        raise ConfigError("nominal_lead_hours must contain unique positive values in order")

    cycles = config["forecast_sampling"].get("standard_cycle_hours_utc", [])
    if not cycles or cycles != sorted(set(cycles)) or any(
        not isinstance(hour, int) or not 0 <= hour < 24 for hour in cycles
    ):
        raise ConfigError("standard_cycle_hours_utc must contain unique UTC hours in order")

    offset = config["forecast_sampling"].get("maximum_cycle_offset_hours")
    if not isinstance(offset, (int, float)) or offset < 0:
        raise ConfigError("maximum_cycle_offset_hours must be nonnegative")
