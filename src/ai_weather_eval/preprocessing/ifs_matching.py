"""Audit IFS BUFR storm identities and coverage of the frozen coastal cohort.

Identity uses the BUFR observed position at initialization, never a forecast
position or the storm name. Coverage retains the cohort's original cycles and
accepts a reference time only at a valid position or inside a <=6 h bracket.
"""

from __future__ import annotations

import re
from collections import defaultdict

import numpy as np
import pandas as pd
from pyproj import Geod


GEOD = Geod(ellps="WGS84")
MAX_IDENTITY_DISTANCE_KM = 150.0
MIN_CANDIDATE_SEPARATION_KM = 75.0
MAX_INTERPOLATION_GAP_HOURS = 6.0
IDENTIFIER_PREFIXES = {
    "L": "AL", "E": "EP", "C": "CP", "W": "WP",
    "A": "IO", "B": "IO", "S": "SH", "P": "SH",
}


def _require(frame: pd.DataFrame, columns: tuple[str, ...], label: str) -> None:
    missing = sorted(set(columns).difference(frame.columns))
    if missing:
        raise ValueError(f"Missing {label} columns: {', '.join(missing)}")


def _utc(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, utc=True, errors="raise", format="mixed")


def _position_valid(lat: pd.Series, lon: pd.Series) -> pd.Series:
    return np.isfinite(lat) & lat.between(-90, 90) & np.isfinite(lon) & lon.between(-360, 360)


def _valid_observed_position(lat: float, lon: float) -> bool:
    return bool(np.isfinite(lat) and abs(lat) <= 90 and np.isfinite(lon) and abs(lon) <= 360)


def _identifier_agrees(identifier: str, atcf_id: str) -> bool:
    match = re.fullmatch(r"(\d{2})([A-Z])", identifier.strip().upper())
    return bool(match and IDENTIFIER_PREFIXES.get(match[2]) == atcf_id[:2]
                and match[1] == atcf_id[2:4])


def _signature(frame: pd.DataFrame, message: pd.Series) -> tuple:
    """Compare decoded data, including missing rows and intensity/radius fields."""
    columns = sorted(set(frame.columns).difference(
        {"message_id", "source_file", "init_time", "storm_identifier", "storm_name"}
    ))
    ordered = frame.sort_values(["lead_hours", "member_number"], kind="stable")

    def value(item):
        if pd.isna(item):
            return None
        return item.isoformat() if isinstance(item, pd.Timestamp) else item

    header = tuple(value(message.get(column)) for column in (
        "storm_identifier", "observed_latitude", "observed_longitude",
        "analysis_latitude", "analysis_longitude", "member_number",
    ))
    return header, tuple(columns), tuple(
        tuple(value(item) for item in row)
        for row in ordered[columns].itertuples(index=False, name=None)
    )


def _reference_coverage(frame: pd.DataFrame, reference: pd.Timestamp) -> dict:
    """Return position and separate wind/pressure support at an event time."""
    absent = {
        "forecast_available": False, "forecast_wind_available": False,
        "forecast_pressure_available": False, "coverage_method": "unavailable",
        "previous_valid_time": pd.NaT, "next_valid_time": pd.NaT,
    }
    valid = frame.loc[_position_valid(frame.latitude, frame.longitude)].sort_values("valid_time")
    exact = valid.loc[valid.valid_time.eq(reference)]
    if not exact.empty:
        endpoints = exact.iloc[:1]
        method = "exact"
    else:
        before = valid.loc[valid.valid_time.lt(reference)]
        after = valid.loc[valid.valid_time.gt(reference)]
        if before.empty or after.empty:
            return absent
        endpoints = pd.concat([before.iloc[-1:], after.iloc[:1]])
        gap = (endpoints.valid_time.iloc[-1] - endpoints.valid_time.iloc[0]).total_seconds() / 3600
        if gap > MAX_INTERPOLATION_GAP_HOURS:
            return absent
        method = "interpolated"
    wind = endpoints.max_wind_ms
    pressure = endpoints.pressure_hpa
    return {
        "forecast_available": True,
        "forecast_wind_available": bool((np.isfinite(wind) & wind.ge(0)).all()),
        "forecast_pressure_available": bool((np.isfinite(pressure) & pressure.gt(0)).all()),
        "coverage_method": method,
        "previous_valid_time": endpoints.valid_time.iloc[0],
        "next_valid_time": endpoints.valid_time.iloc[-1],
    }


def match_ifs_to_cohort(
    messages: pd.DataFrame,
    tracks: pd.DataFrame,
    truth: pd.DataFrame,
    events: pd.DataFrame,
    cases: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    """Return auditable identity, event, case and aggregate coverage tables.

    Every input event/case remains in the output. Repeated SID+init messages
    with identical decoded data count once; any conflicting repeat excludes
    that initialization. Names do not decide identity. ATCF basin/number
    agreement is diagnostic, permitting audited hemisphere/season aliases.
    All truth main-track states are eligible, including extratropical states.
    """
    _require(messages, ("message_id", "source_file", "init_time", "storm_identifier",
                        "observed_latitude", "observed_longitude", "member_number"), "message")
    _require(tracks, ("message_id", "init_time", "member_number", "lead_hours", "valid_time",
                      "latitude", "longitude", "pressure_hpa", "max_wind_ms"), "track")
    _require(truth, ("SID", "USA_ATCF_ID", "ISO_TIME", "TRACK_TYPE", "LAT", "LON"), "truth")
    _require(events, ("case_id", "storm_id", "reference_time"), "event")
    _require(cases, ("forecast_case_id", "case_id", "storm_id", "init_time",
                    "reference_time", "nominal_lead_hours"), "case")
    if messages.message_id.duplicated().any():
        raise ValueError("message_id must be unique")
    if events.case_id.duplicated().any() or cases.forecast_case_id.duplicated().any():
        raise ValueError("Event and forecast case identifiers must be unique")
    if not set(cases.case_id).issubset(set(events.case_id)):
        raise ValueError("Forecast cases refer to an event outside the supplied cohort")
    event_keys = events.set_index("case_id")
    for case in cases.itertuples(index=False):
        event = event_keys.loc[case.case_id]
        case_reference = _utc(pd.Series([case.reference_time])).iloc[0]
        event_reference = _utc(pd.Series([event.reference_time])).iloc[0]
        if case.storm_id != event.storm_id or case_reference != event_reference:
            raise ValueError(f"Forecast case linkage differs from event {case.case_id}")

    msg, pts, obs = messages.copy(), tracks.copy(), truth.copy()
    msg["init_time"], pts["init_time"], pts["valid_time"] = (
        _utc(msg.init_time), _utc(pts.init_time), _utc(pts.valid_time)
    )
    obs["ISO_TIME"] = _utc(obs.ISO_TIME)
    if msg.init_time.isna().any() or pts[["init_time", "valid_time"]].isna().any().any():
        raise ValueError("Message and track times cannot be missing")
    for column in ("SID", "USA_ATCF_ID", "TRACK_TYPE"):
        obs[column] = obs[column].fillna("").astype(str).str.strip()
    obs["USA_ATCF_ID"] = obs.USA_ATCF_ID.str.upper()
    obs = obs.loc[obs.TRACK_TYPE.str.lower().eq("main")].copy()
    for column in ("LAT", "LON"):
        obs[column] = pd.to_numeric(obs[column], errors="coerce")
    obs = obs.loc[_position_valid(obs.LAT, obs.LON)]
    obs = obs.drop_duplicates(["SID", "ISO_TIME", "LAT", "LON", "USA_ATCF_ID"])
    if obs.duplicated(["SID", "ISO_TIME"]).any():
        raise ValueError("Conflicting IBTrACS main positions for one SID and time")
    for column in ("observed_latitude", "observed_longitude", "analysis_latitude",
                   "analysis_longitude", "member_number"):
        if column in msg:
            msg[column] = pd.to_numeric(msg[column], errors="coerce")
    for column in ("latitude", "longitude", "pressure_hpa", "max_wind_ms", "lead_hours",
                   "member_number"):
        pts[column] = pd.to_numeric(pts[column], errors="coerce")
    if pts.lead_hours.isna().any() or (pts.lead_hours < 0).any():
        raise ValueError("Track lead hours must be nonnegative and present")
    if not set(pts.message_id).issubset(set(msg.message_id)):
        raise ValueError("Track rows refer to an unknown message_id")
    if not (pts.valid_time == pts.init_time + pd.to_timedelta(pts.lead_hours, unit="h")).all():
        raise ValueError("Track valid_time conflicts with initialization plus lead_hours")
    msg_lookup = msg.set_index("message_id")
    if not pts.empty:
        metadata = pts.message_id.map(msg_lookup.init_time)
        members = pts.message_id.map(msg_lookup.member_number)
        if not pts.init_time.eq(metadata).all() or not pts.member_number.eq(members).all():
            raise ValueError("Track initialization or member conflicts with its message")

    truth_by_time = {key: group for key, group in obs.groupby("ISO_TIME", sort=False)}
    points_by_message = {
        key: group.drop_duplicates().copy() for key, group in pts.groupby("message_id", sort=False)
    }
    target_storms = set(events.storm_id)
    link_records = []
    for message in msg.sort_values("message_id", kind="stable").to_dict("records"):
        identifier = str(message["storm_identifier"]).strip().upper()
        group = points_by_message.get(message["message_id"], pts.iloc[:0])
        positive = group.loc[
            group.lead_hours.gt(0) & _position_valid(group.latitude, group.longitude)
        ]
        record = {
            **message, "storm_id": "", "truth_atcf_id": "", "truth_name": "",
            "identity_status": "no_truth_at_init", "id_mapping_method": "",
            "identifier_agrees": False, "init_match_distance_km": np.nan,
            "second_candidate_distance_km": np.nan, "position_candidate_count": 0,
            "positive_position_count_derived": len(positive),
            "max_valid_lead_hours_derived": positive.lead_hours.max() if len(positive) else np.nan,
            "duplicate_status": "unique", "canonical_message_id": "",
            "usable_forecast": False,
        }
        active = truth_by_time.get(message["init_time"])
        if not _valid_observed_position(
            message["observed_latitude"], message["observed_longitude"]
        ):
            record["identity_status"] = "missing_observed_position"
        elif active is not None and not active.empty:
            _, _, meters = GEOD.inv(
                np.full(len(active), message["observed_longitude"]),
                np.full(len(active), message["observed_latitude"]),
                active.LON.to_numpy(), active.LAT.to_numpy(),
            )
            candidates = active.assign(distance_km=np.abs(meters) / 1000)
            candidates = candidates.loc[candidates.distance_km.le(MAX_IDENTITY_DISTANCE_KM)]
            candidates = candidates.sort_values(["distance_km", "SID"], kind="stable")
            record["position_candidate_count"] = len(candidates)
            record["identity_status"] = "no_position_match"
            if not candidates.empty:
                closest = candidates.iloc[0]
                record["init_match_distance_km"] = closest.distance_km
                if len(candidates) > 1:
                    record["second_candidate_distance_km"] = candidates.iloc[1].distance_km
                if len(candidates) > 1 and (
                    candidates.iloc[1].distance_km - closest.distance_km
                    < MIN_CANDIDATE_SEPARATION_KM
                ):
                    record["identity_status"] = "ambiguous_position"
                else:
                    agrees = _identifier_agrees(identifier, closest.USA_ATCF_ID)
                    record.update(
                        storm_id=closest.SID, truth_atcf_id=closest.USA_ATCF_ID,
                        truth_name=closest.get("NAME", ""), identifier_agrees=agrees,
                        identity_status=("matched_cohort" if closest.SID in target_storms
                                         else "matched_outside_cohort"),
                        id_mapping_method=("identifier_and_position" if agrees
                                           else "position_identifier_mismatch"),
                    )
        if group.duplicated(["member_number", "lead_hours"]).any():
            record["duplicate_status"] = "within_message_conflict"
        link_records.append(record)
    link_columns = [*msg.columns, "storm_id", "truth_atcf_id", "truth_name", "identity_status",
                    "id_mapping_method", "identifier_agrees", "init_match_distance_km",
                    "second_candidate_distance_km", "position_candidate_count",
                    "positive_position_count_derived", "max_valid_lead_hours_derived",
                    "duplicate_status", "canonical_message_id", "usable_forecast"]
    links = pd.DataFrame.from_records(link_records, columns=link_columns)

    canonical = {}
    matched = links.loc[links.storm_id.ne("")]
    for key, group in matched.groupby(["storm_id", "init_time"], sort=True):
        indices = group.index
        conflict = group.duplicate_status.eq("within_message_conflict").any()
        signatures = []
        for row in group.itertuples(index=False):
            signatures.append(_signature(points_by_message.get(row.message_id, pts.iloc[:0]),
                                         msg_lookup.loc[row.message_id]))
        conflict = conflict or any(signature != signatures[0] for signature in signatures[1:])
        if conflict:
            links.loc[indices, "duplicate_status"] = "conflicting_duplicate_forecasts"
            continue
        chosen = group.iloc[0]
        canonical_id = chosen.message_id
        links.loc[indices, "canonical_message_id"] = canonical_id
        if len(group) > 1:
            links.loc[indices[1:], "duplicate_status"] = "equivalent_duplicate"
        usable = bool(chosen.positive_position_count_derived > 0)
        links.loc[indices, "usable_forecast"] = usable
        canonical[key] = {
            "message_id": canonical_id, "source_file": chosen.source_file,
            "points": points_by_message.get(canonical_id, pts.iloc[:0]), "usable": usable,
        }
    storm_initializations = defaultdict(list)
    for (sid, init), forecast in canonical.items():
        if forecast["usable"]:
            storm_initializations[sid].append((init, forecast))
    matched_keys = set(zip(matched.storm_id, matched.init_time, strict=True))

    case_records = []
    for case in cases.to_dict("records"):
        init, reference = _utc(pd.Series([case["init_time"], case["reference_time"]]))
        key = (case["storm_id"], init)
        forecast = canonical.get(key)
        record = {
            **case, "storm_has_forecast": bool(storm_initializations[case["storm_id"]]),
            "strict_init_present": key in matched_keys, "strict_init_usable": False,
            "matched_message_id": "", "source_file": "", "unavailable_reason": "",
            **_reference_coverage(pts.iloc[:0], reference),
        }
        if key in matched_keys and forecast is None:
            record["unavailable_reason"] = "conflicting_duplicate_forecasts"
        elif forecast is None:
            record["unavailable_reason"] = "planned_init_absent"
        else:
            record.update(
                matched_message_id=forecast["message_id"], source_file=forecast["source_file"],
                strict_init_usable=forecast["usable"],
            )
            if not forecast["usable"]:
                record["unavailable_reason"] = "no_positive_position_points"
            elif pd.isna(init) or init >= reference:
                record["unavailable_reason"] = "initialization_not_before_event"
            else:
                record.update(_reference_coverage(forecast["points"], reference))
                if not record["forecast_available"]:
                    record["unavailable_reason"] = "reference_time_not_covered"
        case_records.append(record)
    case_columns = [*cases.columns, "storm_has_forecast", "strict_init_present",
                    "strict_init_usable", "matched_message_id", "source_file",
                    "unavailable_reason", "forecast_available", "forecast_wind_available",
                    "forecast_pressure_available", "coverage_method", "previous_valid_time",
                    "next_valid_time"]
    case_overlap = pd.DataFrame.from_records(case_records, columns=case_columns)

    event_records = []
    for event in events.to_dict("records"):
        reference = _utc(pd.Series([event["reference_time"]])).iloc[0]
        event_cases = case_overlap.loc[case_overlap.case_id.eq(event["case_id"])]
        coverages = [
            _reference_coverage(forecast["points"], reference)
            for init, forecast in storm_initializations[event["storm_id"]] if init < reference
        ]
        event_records.append({
            **event, "storm_has_forecast": bool(storm_initializations[event["storm_id"]]),
            "storm_forecast_initializations": len(storm_initializations[event["storm_id"]]),
            "event_time_covered": any(item["forecast_available"] for item in coverages),
            "event_wind_available": any(item["forecast_wind_available"] for item in coverages),
            "event_pressure_available": any(
                item["forecast_pressure_available"] for item in coverages
            ),
            "covering_initialization_count": sum(item["forecast_available"] for item in coverages),
            "planned_case_count": len(event_cases),
            "strict_init_present_case_count": int(event_cases.strict_init_present.sum()),
            "available_case_count": int(event_cases.forecast_available.sum()),
            "wind_available_case_count": int(event_cases.forecast_wind_available.sum()),
            "pressure_available_case_count": int(event_cases.forecast_pressure_available.sum()),
        })
    event_columns = [*events.columns, "storm_has_forecast", "storm_forecast_initializations",
                     "event_time_covered", "event_wind_available", "event_pressure_available",
                     "covering_initialization_count", "planned_case_count",
                     "strict_init_present_case_count", "available_case_count",
                     "wind_available_case_count", "pressure_available_case_count"]
    event_overlap = pd.DataFrame.from_records(event_records, columns=event_columns)
    return {
        "storm_links": links,
        "event_overlap": event_overlap,
        "case_overlap": case_overlap,
        "coverage_by_year": _year_summary(event_overlap, case_overlap),
        "coverage_by_lead": _lead_summary(case_overlap),
    }


def _case_summary(group: pd.DataFrame) -> dict:
    return {
        "total_cases": len(group),
        "strict_init_present_cases": int(group.strict_init_present.sum()),
        "available_cases": int(group.forecast_available.sum()),
        "wind_available_cases": int(group.forecast_wind_available.sum()),
        "pressure_available_cases": int(group.forecast_pressure_available.sum()),
        "available_case_fraction": float(group.forecast_available.mean()) if len(group) else np.nan,
    }


def _year_summary(events: pd.DataFrame, cases: pd.DataFrame) -> pd.DataFrame:
    years = _utc(events.reference_time).dt.year
    case_years = _utc(cases.reference_time).dt.year
    records = []
    for year in sorted(years.unique()):
        group = events.loc[years.eq(year)]
        records.append({
            "year": int(year), "total_events": len(group), "total_storms": group.storm_id.nunique(),
            "storms_with_forecast": group.loc[group.storm_has_forecast, "storm_id"].nunique(),
            "events_with_storm_forecast": int(group.storm_has_forecast.sum()),
            "events_with_reference_coverage": int(group.event_time_covered.sum()),
            "events_with_any_available_case": int(group.available_case_count.gt(0).sum()),
            "events_with_all_planned_cases": int((group.planned_case_count.gt(0)
                                                   & group.available_case_count.eq(
                                                       group.planned_case_count)).sum()),
            **_case_summary(cases.loc[case_years.eq(year)]),
        })
    return pd.DataFrame.from_records(records)


def _lead_summary(cases: pd.DataFrame) -> pd.DataFrame:
    records = []
    for lead, group in cases.groupby("nominal_lead_hours", sort=True):
        available = group.loc[group.forecast_available]
        records.append({
            "nominal_lead_hours": lead,
            "available_events": available.case_id.nunique(),
            "available_storms": available.storm_id.nunique(),
            **_case_summary(group),
        })
    return pd.DataFrame.from_records(records)
