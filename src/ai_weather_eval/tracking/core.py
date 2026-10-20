"""Pangu-inspired MSLP/vorticity tracking without future observed positions.

The core has no file or batch assumptions: a reader supplies one small native-grid
window at a time. Distances use WGS84; wind derivatives use spherical coordinates,
including the curvature term. No smoothing or sub-grid position fitting is used.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import numpy as np
from scipy.ndimage import maximum_filter, minimum_filter

from ai_weather_eval.metrics.track import WGS84_GEOD

if TYPE_CHECKING:
    from ai_weather_eval.tracking.reader import ForecastWindow

EARTH_RADIUS_M = 6_371_000.0
POINT_COLUMNS = (
    "lead_hours", "latitude", "longitude", "msl_hpa", "vmax10_ms",
    "cyclonic_vorticity_s", "status", "reason",
)


@dataclass(frozen=True)
class TrackerConfig:
    """Shared thresholds for every model (kilometres and seconds)."""

    search_radius_km: float = 445.0
    diagnostic_radius_km: float = 278.0
    vorticity_threshold_s: float = 5e-5

    def __post_init__(self) -> None:
        for name in ("search_radius_km", "diagnostic_radius_km", "vorticity_threshold_s"):
            value = getattr(self, name)
            if not np.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")


DEFAULT_CONFIG = TrackerConfig()


class ForecastReader(Protocol):
    """A forecast opened in the calling process, with six-hourly output."""

    lead_hours: tuple[int, ...]

    def read_window(
        self, index: int, latitude: float, longitude: float, radius_km: float
    ) -> ForecastWindow: ...


def relative_vorticity(
    latitude: np.ndarray, longitude: np.ndarray, u: np.ndarray, v: np.ndarray
) -> np.ndarray:
    """Return relative vorticity in s^-1 on a regular latitude/longitude grid.

    zeta = [d(v)/d(lambda) - d(u*cos(phi))/d(phi)] / [a*cos(phi)].
    Ascending and descending latitudes are both supported. Longitudes may cross
    the seam; readers should return their locally unwrapped order.
    """

    phi = np.deg2rad(np.asarray(latitude, dtype=np.float64))
    lam = np.unwrap(np.deg2rad(np.asarray(longitude, dtype=np.float64)))
    u = np.asarray(u, dtype=np.float64)
    v = np.asarray(v, dtype=np.float64)
    if phi.ndim != 1 or lam.ndim != 1 or min(phi.size, lam.size) < 3:
        raise ValueError("Vorticity requires at least three latitudes and longitudes")
    if u.shape != (phi.size, lam.size) or v.shape != u.shape:
        raise ValueError("Wind shape must match latitude and longitude")
    if not all(np.all(np.diff(axis) > 0) or np.all(np.diff(axis) < 0)
               for axis in (phi, lam)):
        raise ValueError("Wind coordinates must be strictly monotonic")
    cosine = np.cos(phi)[:, None]
    with np.errstate(divide="ignore", invalid="ignore"):
        zeta = (
            np.gradient(v, lam, axis=1, edge_order=2)
            - np.gradient(u * cosine, phi, axis=0, edge_order=2)
        ) / (EARTH_RADIUS_M * cosine)
    return np.where(np.abs(cosine) > 1e-10, zeta, np.nan)


def _distance_grid(latitude: np.ndarray, longitude: np.ndarray, lat: float, lon: float):
    lons, lats = np.meshgrid(longitude, latitude)
    _, _, distance = WGS84_GEOD.inv(
        np.full(lats.shape, lon), np.full(lats.shape, lat), lons, lats
    )
    return np.asarray(distance) / 1000.0


def _find_center(window: ForecastWindow, lat: float, lon: float, config: TrackerConfig):
    latitude = np.asarray(window.latitude, dtype=np.float64)
    longitude = np.asarray(window.longitude, dtype=np.float64)
    pressure = np.asarray(window.msl, dtype=np.float64)
    if pressure.shape != (latitude.size, longitude.size):
        raise ValueError("Pressure shape does not match the forecast window")
    finite = np.isfinite(pressure)
    pressure_min = minimum_filter(np.where(finite, pressure, np.inf), size=3,
                                  mode="constant", cval=np.inf)
    pressure_max = maximum_filter(np.where(finite, pressure, -np.inf), size=3,
                                  mode="constant", cval=-np.inf)
    # A flat field is not a cyclone, and an incomplete neighbourhood is not a
    # confirmed local minimum. The reader supplies a halo beyond the search area.
    complete = minimum_filter(finite.astype(np.uint8), size=3, mode="constant", cval=0)
    distance = _distance_grid(latitude, longitude, lat, lon)
    minima = (finite & (complete == 1) & (pressure == pressure_min)
              & (pressure < pressure_max) & (distance <= config.search_radius_km))
    positions = np.argwhere(minima)
    if not len(positions):
        return None, "no_pressure_minimum"
    zeta = relative_vorticity(latitude, longitude, window.u850, window.v850)
    wind10 = np.hypot(window.u10, window.v10)
    ordered = sorted(
        positions,
        key=lambda ij: (pressure[tuple(ij)], distance[tuple(ij)],
                        latitude[ij[0]], longitude[ij[1]]),
    )
    reason = "vorticity_below_threshold"
    for iy, ix in ordered:
        center_lat = float(latitude[iy])
        center_lon = float(longitude[ix])
        disk = (_distance_grid(latitude, longitude, center_lat, center_lon)
                <= config.diagnostic_radius_km)
        if not np.isfinite(zeta[disk]).all() or not np.isfinite(wind10[disk]).all():
            reason = "missing_diagnostic_data"
            continue
        hemisphere = 1.0 if (center_lat if center_lat else lat) >= 0 else -1.0
        cyclonic = float(np.max(hemisphere * zeta[disk]))
        if cyclonic <= config.vorticity_threshold_s:
            continue
        return {
            "latitude": center_lat,
            "longitude": (center_lon + 180.0) % 360.0 - 180.0,
            "msl_hpa": float(pressure[iy, ix]) / 100.0,
            "vmax10_ms": float(np.max(wind10[disk])),
            "cyclonic_vorticity_s": cyclonic,
        }, ""
    return None, reason


def _empty_point(lead: int, status: str, reason: str) -> dict:
    return {
        "lead_hours": lead, "latitude": None, "longitude": None,
        "msl_hpa": None, "vmax10_ms": None, "cyclonic_vorticity_s": None,
        "status": status, "reason": reason,
    }


def track_forecast(
    reader: ForecastReader,
    *,
    seed_latitude: float | None,
    seed_longitude: float | None,
    config: TrackerConfig = DEFAULT_CONFIG,
) -> list[dict]:
    """Track a single already-open forecast, preserving every planned lead.

    Only the initial seed is observed. Once a step is lost there is no recovery,
    interpolation, or observed-position reset. Read errors preserve earlier points
    and are distinct from a physical tracking failure.
    """

    leads = tuple(int(lead) for lead in reader.lead_hours)
    if not leads or leads != tuple(range(6, leads[-1] + 1, 6)):
        raise ValueError("Forecast leads must be consecutive six-hour steps starting at 6")
    if seed_latitude is None or seed_longitude is None:
        return [_empty_point(lead, "no_seed", "no_exact_main_best_track_at_initialization")
                for lead in leads]
    if (not np.isfinite([seed_latitude, seed_longitude]).all()
            or not -90 < seed_latitude < 90):
        raise ValueError("Seed latitude/longitude must be finite and latitude inside (-90, 90)")
    lat, lon = float(seed_latitude), float(seed_longitude)
    points = []
    stopped = ""
    for index, lead in enumerate(leads):
        if stopped:
            points.append(_empty_point(lead, "terminated", stopped))
            continue
        try:
            window = reader.read_window(
                index, lat, lon, config.search_radius_km + config.diagnostic_radius_km
            )
            center, reason = _find_center(window, lat, lon, config)
        except Exception as exc:
            stopped = f"input_error_at_{lead}h: {type(exc).__name__}: {exc}"
            points.append(_empty_point(lead, "input_error", stopped))
            continue
        if center is None:
            points.append(_empty_point(lead, "track_lost", reason))
            stopped = f"tracking_stopped_at_{lead}h: {reason}"
            continue
        points.append({"lead_hours": lead, **center, "status": "ok", "reason": ""})
        lat, lon = center["latitude"], center["longitude"]
    return points
