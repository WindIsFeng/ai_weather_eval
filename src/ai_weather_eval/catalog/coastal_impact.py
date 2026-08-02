"""Build coastal-impact episodes from IBTrACS tracks and a fixed coastline."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Sequence

import numpy as np
import pandas as pd

from ai_weather_eval.catalog.coastline import CoastCrossing, CoastlineIndex, WGS84_GEOD


WIND_THRESHOLDS = (34, 50, 64)
QUADRANTS = ("NE", "SE", "SW", "NW")
INTERPOLATED_NUMERIC_COLUMNS = (
    "USA_WIND",
    "USA_PRES",
    *(f"USA_R{threshold}_{quadrant}" for threshold in WIND_THRESHOLDS for quadrant in QUADRANTS),
)
NEAREST_COLUMNS = (
    "SID",
    "NAME",
    "BASIN",
    "NATURE",
    "TRACK_TYPE",
    "TRACK_TYPE_NORMALIZED",
    "USA_STATUS",
)


@dataclass(frozen=True)
class CoastalImpactSelection:
    """Thresholds controlling coastal-impact episode selection."""

    start: pd.Timestamp
    end: pd.Timestamp
    coastal_peak_threshold_kt: float = 64.0
    missing_radius_distance_km: float = 100.0
    episode_gap_hours: float = 6.0
    intensity_window_hours: float = 6.0
    maximum_track_gap_hours: float = 6.0
    maximum_track_sample_spacing_km: float = 10.0
    maximum_time_step_hours: float = 1.0
    distance_candidate_samples: int = 6
    crossing_candidate_samples: int = 16
    primary_tiers: tuple[str, ...] = ("A", "B")

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError("Selection end must not precede start")
        positive = {
            "coastal_peak_threshold_kt": self.coastal_peak_threshold_kt,
            "missing_radius_distance_km": self.missing_radius_distance_km,
            "episode_gap_hours": self.episode_gap_hours,
            "intensity_window_hours": self.intensity_window_hours,
            "maximum_track_gap_hours": self.maximum_track_gap_hours,
            "maximum_track_sample_spacing_km": self.maximum_track_sample_spacing_km,
            "maximum_time_step_hours": self.maximum_time_step_hours,
        }
        invalid = [name for name, value in positive.items() if value <= 0]
        if invalid:
            raise ValueError("Selection values must be positive: " + ", ".join(invalid))


def _interpolate_number(value_a: object, value_b: object, fraction: float) -> float:
    number_a = pd.to_numeric(pd.Series([value_a]), errors="coerce").iloc[0]
    number_b = pd.to_numeric(pd.Series([value_b]), errors="coerce").iloc[0]
    if pd.isna(number_a) or pd.isna(number_b):
        if fraction <= 1e-12 and not pd.isna(number_a):
            return float(number_a)
        if fraction >= 1.0 - 1e-12 and not pd.isna(number_b):
            return float(number_b)
        return float("nan")
    return float(number_a) + fraction * (float(number_b) - float(number_a))


def _quadrant_for_bearing(bearing_degrees: float) -> str:
    bearing = bearing_degrees % 360.0
    if bearing < 90.0:
        return "NE"
    if bearing < 180.0:
        return "SE"
    if bearing < 270.0:
        return "SW"
    return "NW"


def _radius_toward_coast_km(
    frame: pd.DataFrame, threshold: int, bearings: pd.Series
) -> pd.Series:
    radii = pd.Series(np.nan, index=frame.index, dtype=float)
    quadrants = bearings.map(_quadrant_for_bearing)
    for quadrant in QUADRANTS:
        mask = quadrants.eq(quadrant)
        radii.loc[mask] = frame.loc[mask, f"USA_R{threshold}_{quadrant}"] * 1.852
    return radii


def _densify_track(
    storm: pd.DataFrame,
    *,
    maximum_spacing_km: float,
    maximum_time_step_hours: float,
    maximum_track_gap_hours: float,
) -> pd.DataFrame:
    storm = storm.sort_values("ISO_TIME", kind="stable").reset_index(drop=True)
    rows: list[dict[str, object]] = []

    def original_row(source: pd.Series, source_index: int, break_before: bool) -> dict[str, object]:
        row = {column: source.get(column, pd.NA) for column in NEAREST_COLUMNS}
        row.update({column: source.get(column, np.nan) for column in INTERPOLATED_NUMERIC_COLUMNS})
        row.update(
            {
                "ISO_TIME": source["ISO_TIME"],
                "LAT": float(source["LAT"]),
                "LON": float(source["LON"]),
                "SOURCE_ROW": int(source["SOURCE_ROW"]),
                "source_segment_index": source_index,
                "source_fraction": 0.0,
                "is_original_point": True,
                "segment_break_before": break_before,
            }
        )
        return row

    rows.append(original_row(storm.iloc[0], 0, False))
    for source_index in range(len(storm) - 1):
        point_a = storm.iloc[source_index]
        point_b = storm.iloc[source_index + 1]
        time_delta = point_b["ISO_TIME"] - point_a["ISO_TIME"]
        hours = time_delta.total_seconds() / 3600.0
        if hours <= 0:
            continue
        if hours > maximum_track_gap_hours:
            rows.append(original_row(point_b, source_index + 1, True))
            continue

        azimuth, _, length_m = WGS84_GEOD.inv(
            point_a["LON"], point_a["LAT"], point_b["LON"], point_b["LAT"]
        )
        step_count = max(
            1,
            ceil(length_m / (maximum_spacing_km * 1000.0)),
            ceil(hours / maximum_time_step_hours),
        )
        for step in range(1, step_count + 1):
            fraction = step / step_count
            longitude, latitude, _ = WGS84_GEOD.fwd(
                point_a["LON"], point_a["LAT"], azimuth, length_m * fraction
            )
            nearest = point_a if fraction < 0.5 else point_b
            row = {column: nearest.get(column, pd.NA) for column in NEAREST_COLUMNS}
            row.update(
                {
                    column: _interpolate_number(point_a.get(column), point_b.get(column), fraction)
                    for column in INTERPOLATED_NUMERIC_COLUMNS
                }
            )
            row.update(
                {
                    "ISO_TIME": point_a["ISO_TIME"] + time_delta * fraction,
                    "LAT": float(latitude),
                    "LON": float(longitude),
                    "SOURCE_ROW": int(point_b["SOURCE_ROW"]) if step == step_count else -1,
                    "source_segment_index": source_index,
                    "source_fraction": fraction,
                    "is_original_point": step == step_count,
                    "segment_break_before": False,
                }
            )
            rows.append(row)
    return pd.DataFrame(rows)


def _find_storm_crossings(
    storm: pd.DataFrame,
    coastline: CoastlineIndex,
    selection: CoastalImpactSelection,
) -> list[tuple[pd.Timestamp, CoastCrossing]]:
    storm = storm.sort_values("ISO_TIME", kind="stable").reset_index(drop=True)
    crossing_records: list[tuple[pd.Timestamp, CoastCrossing]] = []
    for source_index in range(len(storm) - 1):
        point_a = storm.iloc[source_index]
        point_b = storm.iloc[source_index + 1]
        time_delta = point_b["ISO_TIME"] - point_a["ISO_TIME"]
        hours = time_delta.total_seconds() / 3600.0
        if hours <= 0 or hours > selection.maximum_track_gap_hours:
            continue
        pair_crossings = coastline.find_crossings(
            [point_a["LAT"], point_b["LAT"]],
            [point_a["LON"], point_b["LON"]],
            maximum_subsegment_km=selection.maximum_track_sample_spacing_km,
            candidate_samples=selection.crossing_candidate_samples,
        )
        for crossing in pair_crossings:
            crossing_time = point_a["ISO_TIME"] + time_delta * crossing.fraction
            crossing_records.append((crossing_time, crossing))
    return crossing_records


def _flag_duration_hours(frame: pd.DataFrame, flag_column: str) -> float:
    if len(frame) < 2:
        return 0.0
    times = frame["ISO_TIME"].reset_index(drop=True)
    flags = frame[flag_column].fillna(False).astype(float).reset_index(drop=True)
    interval_hours = times.diff().dt.total_seconds().div(3600.0).iloc[1:].to_numpy()
    weights = ((flags.iloc[:-1].to_numpy() + flags.iloc[1:].to_numpy()) / 2.0)
    return float(np.sum(interval_hours * weights))


def _episode_tier(episode: pd.DataFrame, crossing_vmax_kt: float) -> str:
    if episode["r64_contact"].any() or crossing_vmax_kt >= 64.0:
        return "A"
    if episode["r50_contact"].any() or episode["coastline_crossing"].any():
        return "B"
    if episode["r34_contact"].any():
        return "C"
    return "D"


def _prepare_storm_points(
    storm: pd.DataFrame,
    coastline: CoastlineIndex,
    selection: CoastalImpactSelection,
) -> tuple[pd.DataFrame, list[tuple[pd.Timestamp, CoastCrossing]]]:
    dense = _densify_track(
        storm,
        maximum_spacing_km=selection.maximum_track_sample_spacing_km,
        maximum_time_step_hours=selection.maximum_time_step_hours,
        maximum_track_gap_hours=selection.maximum_track_gap_hours,
    )
    coast_distances = coastline.distances_to_points(
        dense["LAT"].to_numpy(),
        dense["LON"].to_numpy(),
        candidate_samples=selection.distance_candidate_samples,
    )
    dense["coast_distance_km"] = [distance.distance_km for distance in coast_distances]
    dense["nearest_coast_latitude"] = [
        distance.coast_latitude for distance in coast_distances
    ]
    dense["nearest_coast_longitude"] = [
        distance.coast_longitude for distance in coast_distances
    ]
    dense["nearest_coast_bearing_degrees"] = [
        distance.bearing_degrees for distance in coast_distances
    ]
    for threshold in WIND_THRESHOLDS:
        radius_column = f"r{threshold}_toward_coast_km"
        contact_column = f"r{threshold}_contact"
        dense[radius_column] = _radius_toward_coast_km(
            dense, threshold, dense["nearest_coast_bearing_degrees"]
        )
        dense[contact_column] = (
            dense[radius_column].notna()
            & (dense["coast_distance_km"] <= dense[radius_column])
        )

    crossing_records = _find_storm_crossings(storm, coastline, selection)
    dense["coastline_crossing"] = False
    for crossing_time, _ in crossing_records:
        nearest_index = (dense["ISO_TIME"] - crossing_time).abs().idxmin()
        dense.loc[nearest_index, "coastline_crossing"] = True

    dense["distance_proxy_contact"] = (
        dense["r34_toward_coast_km"].isna()
        & (dense["coast_distance_km"] <= selection.missing_radius_distance_km)
    )
    dense["coastal_contact"] = (
        dense["r34_contact"]
        | dense["distance_proxy_contact"]
        | dense["coastline_crossing"]
    )
    return dense, crossing_records


def build_coastal_impact_catalog(
    tracks: pd.DataFrame,
    coastline: CoastlineIndex,
    selection: CoastalImpactSelection,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return episode-level cases and auditable dense track points.

    Storms are prefiltered by lifetime ``USA_WIND`` only for computational
    efficiency.  Final inclusion requires the threshold within the coastal
    episode plus its configured intensity window.
    """

    lifetime_vmax = tracks.groupby("SID", sort=False)["USA_WIND"].max()
    strong_storm_ids = lifetime_vmax[
        lifetime_vmax >= selection.coastal_peak_threshold_kt
    ].index
    strong_tracks = tracks[tracks["SID"].isin(strong_storm_ids)].copy()

    cases: list[dict[str, object]] = []
    point_frames: list[pd.DataFrame] = []
    for storm_id, storm in strong_tracks.groupby("SID", sort=True):
        storm = storm.sort_values("ISO_TIME", kind="stable").reset_index(drop=True)
        padding = pd.Timedelta(hours=selection.intensity_window_hours)
        if storm["ISO_TIME"].max() < selection.start - padding:
            continue
        if storm["ISO_TIME"].min() > selection.end + padding:
            continue
        dense, crossing_records = _prepare_storm_points(storm, coastline, selection)
        dense["storm_id"] = storm_id
        point_frames.append(dense)
        contact = dense[dense["coastal_contact"]].copy()
        if contact.empty:
            continue
        contact["episode_group"] = (
            contact["ISO_TIME"].diff().dt.total_seconds().div(3600.0)
            > selection.episode_gap_hours
        ).cumsum()

        qualifying_episode_index = 0
        for _, episode_contact in contact.groupby("episode_group", sort=True):
            episode_start = episode_contact["ISO_TIME"].min()
            episode_end = episode_contact["ISO_TIME"].max()
            episode_window = dense[
                dense["ISO_TIME"].between(episode_start - padding, episode_end + padding)
            ].copy()
            episode_span = dense[
                dense["ISO_TIME"].between(episode_start, episode_end)
            ].copy()
            coastal_vmax = episode_window["USA_WIND"].max()
            if pd.isna(coastal_vmax) or coastal_vmax < selection.coastal_peak_threshold_kt:
                continue

            closest_index = episode_contact["coast_distance_km"].idxmin()
            closest = dense.loc[closest_index]
            episode_crossings = [
                (time, crossing)
                for time, crossing in crossing_records
                if episode_start - padding <= time <= episode_end + padding
            ]
            episode_crossing_times = [time for time, _ in episode_crossings]
            crossing_vmax = float("nan")
            if episode_crossing_times:
                crossing_indices = [
                    (dense["ISO_TIME"] - crossing_time).abs().idxmin()
                    for crossing_time in episode_crossing_times
                ]
                crossing_vmax = float(dense.loc[crossing_indices, "USA_WIND"].max())
                closest_time, closest_crossing = min(
                    episode_crossings,
                    key=lambda item: abs((item[0] - closest["ISO_TIME"]).total_seconds()),
                )
                minimum_distance_km = 0.0
                nearest_crossing_index = (dense["ISO_TIME"] - closest_time).abs().idxmin()
                closest = dense.loc[nearest_crossing_index]
                closest_latitude = closest_crossing.latitude
                closest_longitude = closest_crossing.longitude
                nearest_coast_latitude = closest_crossing.latitude
                nearest_coast_longitude = closest_crossing.longitude
            else:
                closest_time = closest["ISO_TIME"]
                minimum_distance_km = float(closest["coast_distance_km"])
                closest_latitude = float(closest["LAT"])
                closest_longitude = float(closest["LON"])
                nearest_coast_latitude = float(closest["nearest_coast_latitude"])
                nearest_coast_longitude = float(closest["nearest_coast_longitude"])

            if not selection.start <= closest_time <= selection.end:
                continue
            qualifying_episode_index += 1
            tier = _episode_tier(episode_contact, crossing_vmax)
            methods: list[str] = []
            if episode_contact["coastline_crossing"].any():
                methods.append("coastline_crossing")
            if episode_contact["r34_contact"].any():
                methods.append("wind_radius")
            if episode_contact["distance_proxy_contact"].any():
                methods.append("distance_proxy")
            track_types = sorted(storm["TRACK_TYPE"].dropna().astype(str).unique())
            cases.append(
                {
                    "case_id": f"{storm_id}-CE{qualifying_episode_index:02d}",
                    "storm_id": storm_id,
                    "name": closest["NAME"],
                    "basin": closest["BASIN"],
                    "episode_index": qualifying_episode_index,
                    "episode_start": episode_start,
                    "episode_end": episode_end,
                    "closest_approach_time": closest_time,
                    "latitude": closest_latitude,
                    "longitude": closest_longitude,
                    "nearest_coast_latitude": nearest_coast_latitude,
                    "nearest_coast_longitude": nearest_coast_longitude,
                    "minimum_coast_distance_km": minimum_distance_km,
                    "lifetime_vmax_kt": float(lifetime_vmax.loc[storm_id]),
                    "coastal_episode_vmax_kt": float(coastal_vmax),
                    "closest_approach_vmax_kt": (
                        float(closest["USA_WIND"])
                        if pd.notna(closest["USA_WIND"])
                        else np.nan
                    ),
                    "crossing_vmax_kt": crossing_vmax,
                    "coastline_crossing": bool(episode_crossing_times),
                    "crossing_count": len(episode_crossing_times),
                    "r34_contact": bool(episode_contact["r34_contact"].any()),
                    "r50_contact": bool(episode_contact["r50_contact"].any()),
                    "r64_contact": bool(episode_contact["r64_contact"].any()),
                    "r34_contact_hours": _flag_duration_hours(
                        episode_span, "r34_contact"
                    ),
                    "coastal_contact_span_hours": (
                        episode_end - episode_start
                    ).total_seconds()
                    / 3600.0,
                    "tier": tier,
                    "primary_sample": tier in selection.primary_tiers,
                    "selection_method": "+".join(methods),
                    "nature_at_closest_approach": closest["NATURE"],
                    "usa_status_at_closest_approach": closest["USA_STATUS"],
                    "track_types": "+".join(track_types),
                    "provisional_track": any(
                        track_type.lower() != "main" for track_type in track_types
                    ),
                    "r34_coverage_fraction": float(
                        episode_window[
                            [f"USA_R34_{quadrant}" for quadrant in QUADRANTS]
                        ]
                        .notna()
                        .any(axis=1)
                        .mean()
                    ),
                    "usa_wind_coverage_fraction": float(
                        episode_window["USA_WIND"].notna().mean()
                    ),
                    "qc_status": "pending",
                }
            )

    case_frame = pd.DataFrame(cases)
    if not case_frame.empty:
        case_frame = case_frame.sort_values(
            ["closest_approach_time", "storm_id", "episode_index"], kind="stable"
        ).reset_index(drop=True)
    point_frame = pd.concat(point_frames, ignore_index=True) if point_frames else pd.DataFrame()
    return case_frame, point_frame
