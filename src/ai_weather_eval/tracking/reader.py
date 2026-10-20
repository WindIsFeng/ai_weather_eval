"""Bounded, local NetCDF reads from the five Weather Hub forecast products."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from netCDF4 import Dataset, date2num
from numpy.typing import NDArray

# The smallest WGS84 meridional radius makes this spherical bounding box
# conservative for the ellipsoidal distance masks used by the tracker core.
_MIN_WGS84_RADIUS_KM = 6335.439
_CACHE_BYTES = 2 * 1024 * 1024
_MODELS = {"graphcast", "fengwu", "fuxi", "pangu", "aurora"}


@dataclass(frozen=True)
class ForecastWindow:
    """Native grid subset; pressure is Pa and all wind components are m/s.

    Latitude retains its source ordering. Longitude increases monotonically and
    is unwrapped around the requested center, including across the dateline.
    """

    latitude: NDArray[np.float64]
    longitude: NDArray[np.float64]
    msl: NDArray[np.float64]
    u850: NDArray[np.float64]
    v850: NDArray[np.float64]
    u10: NDArray[np.float64]
    v10: NDArray[np.float64]


def _values(variable, key=slice(None)) -> NDArray[np.float64]:
    """Convert fill values to NaN without promoting a masked integer fill."""
    return np.asarray(np.ma.asarray(variable[key], dtype=np.float64).filled(np.nan))


def _regular_coordinate(dataset, name: str, units: str) -> NDArray[np.float64]:
    variable = dataset.variables[name]
    values = _values(variable)
    if variable.dimensions != (name,) or getattr(variable, "units", None) != units:
        raise ValueError(f"invalid {name} dimensions or units")
    delta = np.diff(values)
    if (
        values.ndim != 1
        or values.size < 3
        or not np.isfinite(values).all()
        or not ((delta > 0).all() or (delta < 0).all())
        or not np.allclose(delta, delta[0], rtol=0, atol=1e-6)
    ):
        raise ValueError(f"{name} must be a finite, regular, monotonic grid")
    if name == "latitude" and (np.abs(values) > 90).any():
        raise ValueError("latitude is outside [-90, 90]")
    if name == "longitude" and abs(values[-1] - values[0]) >= 360 - 1e-6:
        raise ValueError("longitude must not duplicate a periodic endpoint")
    return values


class WeatherHubForecast:
    """A context manager owning one surface and one upper-air NetCDF handle.

    Only coordinate vectors are read on entry. ``read_window`` reads the five
    required fields at one lead and one pressure level through contiguous local
    slices; a longitude seam is split into separate reads. The extra two grid
    cells on every side support local minima and finite-difference diagnostics.
    """

    def __init__(
        self, case_dir: Path, model: str, init_time: str | datetime, forecast_hours: int
    ):
        self.case_dir = Path(case_dir)
        self.model = model.lower()
        if self.model not in _MODELS:
            raise ValueError(f"unsupported forecast model: {model}")
        if int(forecast_hours) != forecast_hours or forecast_hours <= 0 or forecast_hours % 6:
            raise ValueError("forecast_hours must be a positive multiple of 6")
        self.lead_hours = tuple(range(6, int(forecast_hours) + 1, 6))
        stamp = datetime.fromisoformat(init_time.replace("Z", "+00:00")) if isinstance(
            init_time, str
        ) else init_time
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        self.init_time = stamp.astimezone(timezone.utc).replace(tzinfo=None)
        self._stack: ExitStack | None = None

    def __enter__(self) -> WeatherHubForecast:
        if self._stack is not None:
            raise RuntimeError("forecast is already open")
        stack = ExitStack()
        try:
            surface = stack.enter_context(Dataset(self.case_dir / "surface.nc", "r"))
            filename = "atmospheric.nc" if self.model == "aurora" else "upper.nc"
            upper = stack.enter_context(Dataset(self.case_dir / filename, "r"))
            self.latitude, self.longitude = self._validate(surface, upper=False)
            upper_latitude, upper_longitude = self._validate(upper, upper=True)
            if not np.array_equal(self.latitude, upper_latitude) or not np.array_equal(
                self.longitude, upper_longitude
            ):
                raise ValueError("surface and upper-air grids disagree")
            level = _values(upper.variables["level"])
            if (
                upper.variables["level"].dimensions != ("level",)
                or getattr(upper.variables["level"], "units", None) != "hPa"
                or level.ndim != 1
                or not np.isfinite(level).all()
                or np.unique(level).size != level.size
            ):
                raise ValueError("invalid pressure level coordinate")
            indices = np.flatnonzero(np.isclose(level, 850, rtol=0, atol=1e-6))
            if len(indices) != 1:
                raise ValueError("850 hPa level is required")
            self._level_index = int(indices[0])
            self._surface, self._upper = surface, upper
            self._stack = stack
            return self
        except BaseException:
            stack.close()
            raise

    def __exit__(self, exc_type, exc_value, traceback):
        if self._stack is not None:
            self._stack.close()
            self._stack = None

    def _validate(self, dataset, *, upper: bool):
        if getattr(dataset, "status", None) != "complete":
            raise ValueError("NetCDF status is not complete")
        latitude = _regular_coordinate(dataset, "latitude", "degrees_north")
        longitude = _regular_coordinate(dataset, "longitude", "degrees_east")
        lead = dataset.variables["lead_time"]
        if (
            lead.dimensions != ("valid_time",)
            or getattr(lead, "units", None) != "hours"
            or not np.array_equal(_values(lead), self.lead_hours)
        ):
            raise ValueError("lead_time must match the complete 6-hour forecast schedule")
        times = [self.init_time + timedelta(hours=hour) for hour in self.lead_hours]
        for name, expected_times in (
            ("forecast_reference_time", self.init_time), ("valid_time", times)
        ):
            variable = dataset.variables[name]
            expected_dimensions = () if name == "forecast_reference_time" else ("valid_time",)
            if variable.dimensions != expected_dimensions:
                raise ValueError(f"invalid {name} dimensions")
            calendar = getattr(variable, "calendar", "standard")
            if calendar not in ("standard", "gregorian", "proleptic_gregorian"):
                raise ValueError(f"unsupported {name} calendar: {calendar}")
            expected = np.asarray(date2num(expected_times, variable.units, calendar=calendar))
            actual = _values(variable, Ellipsis)
            if actual.shape != expected.shape or not np.allclose(
                actual, expected, rtol=0, atol=1e-6
            ):
                raise ValueError(f"{name} does not match init_time and lead_time")
        fields = {"u": "m s-1", "v": "m s-1"} if upper else {
            "msl": "Pa", self._u10_name: "m s-1", self._v10_name: "m s-1"
        }
        dimensions = ("valid_time", "level", "latitude", "longitude") if upper else (
            "valid_time", "latitude", "longitude"
        )
        for name, units in fields.items():
            variable = dataset.variables[name]
            if variable.dimensions != dimensions or getattr(variable, "units", None) != units:
                raise ValueError(f"invalid {name} dimensions or units")
            variable.set_var_chunk_cache(size=_CACHE_BYTES, nelems=1009, preemption=0.5)
        return latitude, longitude

    @property
    def _u10_name(self):
        return "10u" if self.model == "aurora" else "u10"

    @property
    def _v10_name(self):
        return "10v" if self.model == "aurora" else "v10"

    def read_window(
        self, index: int, latitude: float, longitude: float, radius_km: float
    ) -> ForecastWindow:
        """Read a spherical search radius plus a two-cell differentiation halo."""
        if self._stack is None:
            raise RuntimeError("open forecast with a context manager before reading")
        if index not in range(len(self.lead_hours)):
            raise IndexError("forecast time index is outside the lead schedule")
        if (
            not np.isfinite([latitude, longitude, radius_km]).all()
            or abs(latitude) > 90
            or radius_km <= 0
        ):
            raise ValueError("invalid window center or radius")
        angular_radius = min(radius_km / _MIN_WGS84_RADIUS_KM, np.pi)
        latitude_radius = np.degrees(angular_radius)
        if abs(latitude) + latitude_radius >= 90:
            longitude_radius = 180.0
        else:
            longitude_radius = np.degrees(np.arcsin(
                np.clip(np.sin(angular_radius) / np.cos(np.radians(latitude)), -1, 1)
            ))
        latitude_halo = 2 * abs(self.latitude[1] - self.latitude[0])
        latitude_indices = np.flatnonzero(
            np.abs(self.latitude - latitude) <= latitude_radius + latitude_halo + 1e-8
        )
        if latitude_indices.size == 0:
            raise ValueError("requested center is outside the available latitude grid")
        latitude_slice = slice(int(latitude_indices[0]), int(latitude_indices[-1]) + 1)
        unwrapped = longitude + (self.longitude - longitude + 180) % 360 - 180
        longitude_halo = 2 * abs(self.longitude[1] - self.longitude[0])
        longitude_indices = np.flatnonzero(
            np.abs(unwrapped - longitude) <= longitude_radius + longitude_halo + 1e-8
        )
        if longitude_indices.size == 0:
            raise ValueError("requested center is outside the available longitude grid")
        longitude_indices = longitude_indices[np.argsort(unwrapped[longitude_indices])]
        # At the periodic seam sorted coordinates split into two contiguous runs.
        breaks = np.flatnonzero(np.abs(np.diff(longitude_indices)) != 1) + 1
        segments = np.split(longitude_indices, breaks)

        def read(variable, level=None):
            pieces = []
            for segment in segments:
                selection = (index,) if level is None else (index, level)
                selection += (
                    latitude_slice, slice(int(segment.min()), int(segment.max()) + 1)
                )
                piece = _values(variable, selection)
                if segment.size > 1 and segment[0] > segment[-1]:
                    piece = piece[:, ::-1]
                pieces.append(piece)
            return np.concatenate(pieces, axis=1)

        return ForecastWindow(
            latitude=self.latitude[latitude_slice].copy(),
            longitude=unwrapped[longitude_indices],
            msl=read(self._surface.variables["msl"]),
            u850=read(self._upper.variables["u"], self._level_index),
            v850=read(self._upper.variables["v"], self._level_index),
            u10=read(self._surface.variables[self._u10_name]),
            v10=read(self._surface.variables[self._v10_name]),
        )
