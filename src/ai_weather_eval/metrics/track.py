"""Track verification primitives on the WGS84 ellipsoid."""

from __future__ import annotations

from pyproj import Geod


WGS84_GEOD = Geod(ellps="WGS84")


def great_circle_distance_km(
    latitude_a: float,
    longitude_a: float,
    latitude_b: float,
    longitude_b: float,
) -> float:
    """Return the WGS84 ellipsoidal geodesic distance between two points."""

    _, _, distance_m = WGS84_GEOD.inv(longitude_a, latitude_a, longitude_b, latitude_b)
    return float(distance_m) / 1000.0
