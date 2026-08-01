"""Public model-adapter contract."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Protocol

from ai_weather_eval.schemas import CANONICAL_FIELD_VARIABLES


CANONICAL_DIMENSIONS = ("init_time", "lead_time", "latitude", "longitude")


class ModelAdapter(Protocol):
    """Convert one model's files to the canonical xarray representation."""

    model_name: str

    def open_forecast(self, path: Path) -> Any:
        """Open one forecast without loading every value into memory."""
        ...

    def normalize(self, dataset: Any) -> Any:
        """Rename coordinates/variables, convert units, and normalize orientation."""
        ...


def validate_canonical_dataset(dataset: Any, *, require_all_fields: bool = True) -> None:
    """Validate dimensions and variables without depending on xarray at import time."""

    dimensions = set(getattr(dataset, "dims", ()))
    missing_dimensions = sorted(set(CANONICAL_DIMENSIONS).difference(dimensions))
    if missing_dimensions:
        raise ValueError("Missing canonical dimension(s): " + ", ".join(missing_dimensions))

    if require_all_fields:
        variables = set(getattr(dataset, "data_vars", ()))
        missing_variables = sorted(set(CANONICAL_FIELD_VARIABLES).difference(variables))
        if missing_variables:
            raise ValueError("Missing canonical variable(s): " + ", ".join(missing_variables))


def validate_mapping(mapping: Mapping[str, str], required_names: tuple[str, ...]) -> None:
    missing = sorted(set(required_names).difference(mapping))
    if missing:
        raise ValueError("Missing mapping entry/entries: " + ", ".join(missing))

