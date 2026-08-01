"""Configurable NetCDF/Zarr adapter for models already using regular grids."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ai_weather_eval.adapters.base import (
    CANONICAL_DIMENSIONS,
    validate_canonical_dataset,
    validate_mapping,
)
from ai_weather_eval.schemas import CANONICAL_FIELD_VARIABLES


class GenericXarrayAdapter:
    """Normalize coordinates and variables using declarative mappings."""

    def __init__(
        self,
        model_name: str,
        file_format: str,
        coordinate_mapping: Mapping[str, str],
        variable_mapping: Mapping[str, str],
    ) -> None:
        self.model_name = model_name
        self.file_format = file_format
        self.coordinate_mapping = dict(coordinate_mapping)
        self.variable_mapping = dict(variable_mapping)
        validate_mapping(self.coordinate_mapping, CANONICAL_DIMENSIONS)
        validate_mapping(self.variable_mapping, CANONICAL_FIELD_VARIABLES)

    def open_forecast(self, path: Path) -> Any:
        import xarray as xr

        if self.file_format == "netcdf":
            return xr.open_dataset(path, chunks="auto")
        if self.file_format == "zarr":
            return xr.open_zarr(path, chunks="auto")
        raise ValueError(f"Unsupported forecast format: {self.file_format}")

    def normalize(self, dataset: Any) -> Any:
        rename = {
            source: canonical
            for canonical, source in {
                **self.coordinate_mapping,
                **self.variable_mapping,
            }.items()
            if source != canonical
        }
        normalized = dataset.rename(rename)
        validate_canonical_dataset(normalized)
        return normalized

