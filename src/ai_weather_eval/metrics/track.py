"""Track verification primitives."""

from __future__ import annotations

from math import asin, cos, radians, sin, sqrt


EARTH_RADIUS_KM = 6371.0088


def great_circle_distance_km(
    latitude_a: float,
    longitude_a: float,
    latitude_b: float,
    longitude_b: float,
) -> float:
    """Return the haversine distance between two geographic points."""

    lat_a = radians(latitude_a)
    lat_b = radians(latitude_b)
    delta_lat = lat_b - lat_a
    delta_lon = radians(longitude_b - longitude_a)
    haversine = sin(delta_lat / 2.0) ** 2 + cos(lat_a) * cos(lat_b) * sin(delta_lon / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_KM * asin(sqrt(min(1.0, haversine)))

