"""Decode the uploaded single-member ECMWF tropical-cyclone BUFR product.

Observed storm positions remain metadata, never substitute forecast positions.
The decoder deliberately rejects unknown shapes rather than inventing output.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone


QUADRANTS = ("ne", "se", "sw", "nw")
THRESHOLDS_MS = (18, 26, 33)


def nullable_number(value: object) -> float | None:
    """Convert ecCodes missing sentinels before applying any unit conversion."""
    number = float(value)
    if not math.isfinite(number) or abs(number) >= 1e90 or number == 2147483647:
        return None
    return number


def position(latitude: object, longitude: object) -> tuple[float | None, float | None]:
    lat, lon = nullable_number(latitude), nullable_number(longitude)
    if lat is not None and not -90 <= lat <= 90:
        raise ValueError(f"Invalid latitude: {lat}")
    if lon is not None:
        if not -180 <= lon <= 360:
            raise ValueError(f"Invalid longitude: {lon}")
        lon = (lon + 180) % 360 - 180
    return lat, lon


def split_bufr_messages(data: bytes):
    """Yield complete messages; reject truncation and non-BUFR trailing bytes."""
    offset = 0
    while offset < len(data):
        if data[offset:offset + 4] != b"BUFR" or offset + 8 > len(data):
            raise ValueError(f"Invalid BUFR header at byte {offset}")
        length = int.from_bytes(data[offset + 4:offset + 7], "big")
        if length < 12 or offset + length > len(data):
            raise ValueError(f"Truncated BUFR message at byte {offset}")
        message = data[offset:offset + length]
        if message[-4:] != b"7777":
            raise ValueError(f"Missing BUFR terminator at byte {offset}")
        yield message
        offset += length
    if offset == 0:
        raise ValueError("Empty BUFR file")


def decode_message(data: bytes, *, message_id: str, source_file: str):
    """Return metadata and actual encoded forecast rows for one BUFR message."""
    import eccodes as ec

    handle = ec.codes_new_from_message(data)
    try:
        ec.codes_set(handle, "unpack", 1)
        if ec.codes_get(handle, "numberOfSubsets") != 1:
            raise ValueError("Only one-subset deterministic messages are supported")
        if ec.codes_get(handle, "compressedData") != 0:
            raise ValueError("Unexpected compressed deterministic message")
        members = list(ec.codes_get_array(handle, "ensembleMemberNumber"))
        if members != [52]:
            raise ValueError(f"Expected HRES member 52, found {members}")
        init = datetime(
            *(int(ec.codes_get(handle, k)) for k in ("year", "month", "day", "hour", "minute")),
            tzinfo=timezone.utc,
        )
        identifier = str(ec.codes_get(handle, "stormIdentifier")).strip().upper()
        name = str(ec.codes_get(handle, "longStormName")).strip()
        periods = (
            list(ec.codes_get_array(handle, "timePeriod"))
            if ec.codes_is_defined(handle, "timePeriod") else []
        )
        leads = [0] + [int(x) for x in periods]
        if leads != sorted(set(leads)) or any(x < 0 or x > 240 for x in leads):
            raise ValueError(f"Unexpected forecast periods: {leads}")
        latitudes = list(ec.codes_get_array(handle, "latitude"))
        longitudes = list(ec.codes_get_array(handle, "longitude"))
        significance = list(ec.codes_get_array(handle, "meteorologicalAttributeSignificance"))
        pressure = list(ec.codes_get_array(handle, "pressureReducedToMeanSeaLevel"))
        wind = list(ec.codes_get_array(handle, "windSpeedAt10M"))
        thresholds = list(ec.codes_get_array(handle, "windSpeedThreshold"))
        bearings = list(ec.codes_get_array(handle, "bearingOrAzimuth"))
        radii = list(ec.codes_get_array(handle, "effectiveRadiusWithRespectToWindSpeedsAboveThreshold"))
        count = len(leads)
        if not (len(latitudes) == len(longitudes) == len(significance) == 1 + 2 * count):
            raise ValueError("Unexpected position array shape")
        if significance[0] != 1 or significance[1] not in (4, 5):
            raise ValueError("Unexpected observed/analysis significance")
        if any(significance[2 * i + 2] != 3 for i in range(count)):
            raise ValueError("Unexpected maximum-wind significance")
        if any(significance[2 * i + 1] != 1 for i in range(1, count)):
            raise ValueError("Unexpected forecast storm-position significance")
        if len(pressure) != count or len(wind) != count or len(radii) != 12 * count:
            raise ValueError("Unexpected pressure/wind/radius array shape")
        if thresholds != list(THRESHOLDS_MS) * count:
            raise ValueError(f"Unexpected radius thresholds: {thresholds[:3]}")
        if bearings != [0, 90, 90, 180, 180, 270, 270, 0] * (3 * count):
            raise ValueError("Unexpected wind-radius quadrant bearings")
        observed_lat, observed_lon = position(latitudes[0], longitudes[0])
        analysis_lat, analysis_lon = position(latitudes[1], longitudes[1])
        metadata = {
            "message_id": message_id, "source_file": source_file,
            "init_time": init.isoformat(), "storm_identifier": identifier,
            "storm_name": name, "member_number": 52,
            "observed_latitude": observed_lat, "observed_longitude": observed_lon,
            "analysis_latitude": analysis_lat, "analysis_longitude": analysis_lon,
            "encoded_period_count": count,
            "max_encoded_lead_hours": max(leads),
        }
        rows = []
        for i, lead in enumerate(leads):
            lat, lon = position(latitudes[2 * i + 1], longitudes[2 * i + 1])
            pres, speed = nullable_number(pressure[i]), nullable_number(wind[i])
            row = {
                "message_id": message_id, "init_time": init.isoformat(),
                "storm_identifier": identifier, "member_number": 52,
                "lead_hours": lead, "valid_time": (init + timedelta(hours=lead)).isoformat(),
                "latitude": lat, "longitude": lon,
                "pressure_hpa": pres / 100 if pres is not None else None,
                "max_wind_ms": speed,
                "max_wind_kt": speed * 3600 / 1852 if speed is not None else None,
            }
            for j, threshold in enumerate(THRESHOLDS_MS):
                for k, quadrant in enumerate(QUADRANTS):
                    radius = nullable_number(radii[i * 12 + j * 4 + k])
                    row[f"radius_{threshold}ms_{quadrant}_km"] = (
                        radius / 1000 if radius is not None else None
                    )
            rows.append(row)
        positive = [r for r in rows if r["lead_hours"] > 0
                    and r["latitude"] is not None and r["longitude"] is not None]
        metadata["positive_position_count"] = len(positive)
        metadata["max_valid_lead_hours"] = max((r["lead_hours"] for r in positive), default=0)
        metadata["forecast_status"] = "forecast_available" if positive else "no_positive_forecast"
        return metadata, rows
    finally:
        ec.codes_release(handle)
