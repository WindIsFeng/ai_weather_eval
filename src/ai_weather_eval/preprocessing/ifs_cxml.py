"""Decode ECMWF deterministic TIGGE tracks and their separate synoptic fixes."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

from ai_weather_eval.preprocessing.ifs_bufr import nullable_number, position

GENESIS_ID = re.compile(r"[7-9]\d[A-Z]?")


def _time(text: str) -> datetime:
    stamp = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp


def _number(node: ET.Element, path: str, units: tuple[str, ...]) -> float | None:
    value = node.find(path)
    if value is None or not (value.text or "").strip():
        return None
    if value.get("units") not in units:
        raise ValueError(f"Unexpected CXML units at {path}: {value.get('units')!r}")
    return nullable_number(value.text)


def _position(fix: ET.Element) -> tuple[float | None, float | None]:
    # ECMWF encodes signed coordinates, including in elements marked "deg E".
    lat = _number(fix, "latitude", ("deg N", "deg S"))
    lon = _number(fix, "longitude", ("deg E", "deg W"))
    if lat is None or lon is None:
        return None, None
    return position(lat, lon)


def _identifier(storm: ET.Element) -> str:
    return storm.findtext("cycloneNumber", "").strip().upper()


def _storm_key(storm: ET.Element) -> tuple[str, str]:
    identifier = _identifier(storm)
    disturbance_id = storm.get("ID", "").strip()
    if not disturbance_id:
        raise ValueError("Missing CXML disturbance ID")
    return disturbance_id, identifier


def read_cxml(path: Path, *, prefix: bool = False) -> ET.Element:
    """Read a full XML or the complete header and blocks preceding the ensemble."""
    if not prefix:
        return ET.parse(path).getroot()
    data = path.read_bytes()
    ensemble = re.search(rb"""<data\b[^>]*\btype=(["'])ensembleForecast\1[^>]*>""", data)
    if ensemble is None:
        raise ValueError("CXML byte range does not reach the first ensemble block")
    root = ET.fromstring(data[:ensemble.start()] + b"</cxml>")
    kinds = [block.get("type") for block in root.findall("./data[@origin='ecmf']")]
    if kinds != ["analysis", "forecast"]:
        raise ValueError("Unexpected order of CXML blocks before the ensemble")
    return root


def decode_deterministic(
    path: Path, *, source_file: str, init: datetime,
) -> tuple[list[dict], list[dict]]:
    """Read only ECMWF's unnumbered forecast block, preserving actual fix times."""
    root = read_cxml(path, prefix=path.suffix == ".prefix")
    if root.tag != "cxml" or root.findtext("header/productionCenter", "").strip() != "ECMWF":
        raise ValueError("Expected an ECMWF CXML document")
    if _time(root.findtext("header/baseTime", "")) != init:
        raise ValueError("CXML baseTime disagrees with the requested cycle")
    data = root.findall("./data[@origin='ecmf']")
    anchors = defaultdict(set)
    for block in data:
        if block.get("type") != "analysis":
            continue
        for storm in block.findall("disturbance"):
            for fix in storm.findall("./fix[@source='synoptic']"):
                if _time(fix.findtext("validTime", "")) == init:
                    lat, lon = _position(fix)
                    if lat is not None and lon is not None:
                        anchors[_storm_key(storm)].add((lat, lon))

    messages, points = [], []
    for block_no, block in enumerate(data, start=1):
        if block.get("type") != "forecast":
            continue
        if block.get("member") is not None or block.get("perturb") is not None:
            raise ValueError("Unexpected ensemble attributes on deterministic CXML forecast")
        for storm_no, storm in enumerate(block.findall("disturbance"), start=1):
            key = _storm_key(storm)
            identifier = key[1]
            if GENESIS_ID.fullmatch(identifier):
                continue
            message_id = f"{source_file}#{block_no}.{storm_no}"
            observed = anchors[key]
            obs_lat, obs_lon = next(iter(observed)) if len(observed) == 1 else (None, None)
            rows = []
            for fix in storm.findall("fix"):
                if fix.get("source") != "model":
                    raise ValueError("Expected a model fix in deterministic CXML forecast")
                lead = int(fix.get("hour", "-1"))
                valid = _time(fix.findtext("validTime", ""))
                if lead < 0 or valid != init + timedelta(hours=lead):
                    raise ValueError(f"CXML validTime disagrees with lead at {message_id}")
                lat, lon = _position(fix)
                pressure = _number(fix, "cycloneData/minimumPressure/pressure", ("hPa",))
                wind = _number(fix, "cycloneData/maximumWind/speed", ("m/s",))
                rows.append({
                    "message_id": message_id, "init_time": init.isoformat(),
                    "storm_identifier": identifier, "member_number": None,
                    "lead_hours": lead, "valid_time": valid.isoformat(),
                    "latitude": lat, "longitude": lon, "pressure_hpa": pressure,
                    "max_wind_ms": wind,
                    "max_wind_kt": wind * 3600 / 1852 if wind is not None else None,
                })
            zero = next((row for row in rows if row["lead_hours"] == 0), {})
            messages.append({
                "message_id": message_id, "source_file": source_file,
                "init_time": init.isoformat(), "storm_identifier": identifier,
                "storm_name": storm.findtext("cycloneName", "").strip(), "member_number": None,
                "observed_latitude": obs_lat, "observed_longitude": obs_lon,
                "analysis_latitude": zero.get("latitude"),
                "analysis_longitude": zero.get("longitude"),
                "cxml_disturbance_id": key[0], "cxml_synoptic_position_count": len(observed),
            })
            points.extend(rows)
    return messages, points
