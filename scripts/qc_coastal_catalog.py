"""Audit every selected coastal event against frozen tracks and coastline.

Run with AI_WEATHER_EVAL_DATA set to the frozen data root. The audit preserves
case-level evidence and only approves cases that pass every required check.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from ai_weather_eval.catalog.coastline import CoastlineIndex, WGS84_GEOD
from ai_weather_eval.catalog.ibtracs import load_ibtracs_csv
from ai_weather_eval.config import load_yaml


ROOT = Path(__file__).resolve().parents[1]
TIME_COLUMNS = ("episode_start", "episode_end", "reference_time")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_table(path: Path) -> pd.DataFrame:
    # IBTrACS uses NA for the North Atlantic basin; only blanks mean missing.
    return pd.read_csv(path, keep_default_na=False, na_values=[""])


def geodesic_km(lon_a: float, lat_a: float, lon_b: float, lat_b: float) -> float:
    return abs(float(WGS84_GEOD.inv(lon_a, lat_a, lon_b, lat_b)[2])) / 1000.0


def audit(catalog_dir: Path) -> dict[str, object]:
    ibtracs_config = load_yaml(ROOT / "configs/datasets/ibtracs.yaml")
    coastline_config = load_yaml(ROOT / "configs/datasets/coastline.yaml")
    experiment = load_yaml(ROOT / "configs/experiments/global_landfall_2022_2024.yaml")
    source_path = Path(ibtracs_config["path"])
    coastline_path = Path(coastline_config["path"])
    if sha256(source_path) != ibtracs_config["sha256"]:
        raise ValueError("Frozen IBTrACS SHA-256 differs from dataset configuration")
    archive_path = Path(coastline_config["archive_path"])
    if archive_path.stat().st_size != coastline_config["archive_bytes"]:
        raise ValueError("Frozen GSHHG byte length differs from dataset configuration")
    if sha256(archive_path) != coastline_config["archive_sha256"]:
        raise ValueError("Frozen GSHHG SHA-256 differs from dataset configuration")

    tracks = load_ibtracs_csv(source_path)
    coast = CoastlineIndex.from_gshhg(
        coastline_path,
        sample_spacing_km=float(coastline_config["candidate_sample_spacing_km"]),
        minimum_polygon_area_km2=float(coastline_config["minimum_polygon_area_km2"]),
    )
    all_cases = read_table(catalog_dir / "coastal_impact_cases.csv")
    selected = read_table(catalog_dir / "final_main_primary_coastal_impact_cases.csv")
    plans = read_table(catalog_dir / "forecast_cases.csv")
    points = read_table(catalog_dir / "coastal_track_points.csv.gz")
    for column in TIME_COLUMNS:
        selected[column] = pd.to_datetime(selected[column], utc=True, format="mixed")
    plans["reference_time"] = pd.to_datetime(plans["reference_time"], utc=True, format="mixed")
    plans["init_time"] = pd.to_datetime(plans["init_time"], utc=True, format="mixed")
    points["ISO_TIME"] = pd.to_datetime(points["ISO_TIME"], utc=True, format="mixed")
    start = pd.Timestamp(experiment["period"]["start"])
    end = pd.Timestamp(experiment["period"]["end"])

    if not all_cases["case_id"].is_unique or not selected["case_id"].is_unique:
        raise ValueError("Duplicate event IDs in catalog")
    if not plans["forecast_case_id"].is_unique:
        raise ValueError("Duplicate forecast-case IDs")
    expected_ids = set(
        all_cases.loc[all_cases["primary_sample"] & ~all_cases["provisional_track"], "case_id"]
    )
    if set(selected["case_id"]) != expected_ids:
        raise ValueError("Selected event IDs differ from final main A+B population")
    if set(plans["case_id"]) != expected_ids:
        raise ValueError("Forecast cases do not cover exactly the selected events")

    tracks_by_storm = {sid: group for sid, group in tracks.groupby("SID", sort=False)}
    points_by_storm = {sid: group for sid, group in points.groupby("storm_id", sort=False)}
    plans_by_case = {cid: group for cid, group in plans.groupby("case_id", sort=False)}
    records: list[dict[str, object]] = []
    for case in selected.itertuples(index=False):
        failures: list[str] = []
        notes: list[str] = []
        source = tracks_by_storm.get(case.storm_id)
        dense = points_by_storm.get(case.storm_id)
        schedule = plans_by_case.get(case.case_id)
        if source is None or dense is None or schedule is None:
            failures.append("missing_source_or_plan")
            records.append(
                {"case_id": case.case_id, "qc_status": "failed", "failures": ";".join(failures)}
            )
            continue

        if set(source["TRACK_TYPE_NORMALIZED"].dropna()) != {"main"}:
            failures.append("non_main_track")
        if case.basin not in set(source["BASIN"].dropna()):
            failures.append("basin_not_in_source_track")
        if case.name not in set(source["NAME"].dropna()):
            failures.append("name_not_in_source_track")
        if case.qc_status != "pending":
            notes.append("source_qc_status_already_changed")
        if not (case.primary_sample and not case.provisional_track and case.tier in {"A", "B"}):
            failures.append("sample_definition")
        if not (start <= case.reference_time <= end):
            failures.append("reference_outside_period")
        if not case.episode_start <= case.episode_end:
            failures.append("reversed_episode")
        padding = pd.Timedelta(hours=float(experiment["case_selection"]["intensity_window_hours"]))
        if not case.episode_start - padding <= case.reference_time <= case.episode_end + padding:
            failures.append("reference_outside_contact_window")
        if not np.isfinite(case.latitude) or not np.isfinite(case.longitude):
            failures.append("missing_coordinate")
        elif abs(case.latitude) > 90 or abs(case.longitude) > 180:
            failures.append("invalid_coordinate")
        if case.basin not in {"NA", "EP", "WP", "NI", "SI", "SP", "SA"}:
            failures.append("invalid_basin")
        if case.coastal_episode_vmax_kt < 64:
            failures.append("below_intensity_threshold")
        if not (0 <= case.r34_coverage_fraction <= 1 and 0 <= case.usa_wind_coverage_fraction <= 1):
            failures.append("invalid_coverage_fraction")

        episode = dense.loc[
            dense["ISO_TIME"].between(case.episode_start - padding, case.episode_end + padding)
        ]
        episode_wind_max = float(episode["USA_WIND"].max())
        if not np.isclose(episode_wind_max, case.coastal_episode_vmax_kt, atol=1e-6):
            failures.append("episode_wind_mismatch")
        if case.tier == "A" and not (case.r64_contact or case.landfall_vmax_kt >= 64):
            failures.append("tier_a_unsupported")
        if case.tier == "B" and (case.r64_contact or case.landfall_vmax_kt >= 64):
            failures.append("tier_b_should_be_a")
        if case.tier == "B" and not (case.r50_contact or case.landfall_crossing):
            failures.append("tier_b_unsupported")

        coast_distance = coast.distance_to_point(
            case.latitude, case.longitude, candidate_samples=64
        )
        surface = bool(coast.points_on_land([case.latitude], [case.longitude])[0])
        if case.reference_event == "landfall":
            if not (case.landfall_crossing and case.landfall_count >= 1):
                failures.append("landfall_flag_mismatch")
            if coast_distance.distance_km > 0.5:
                failures.append("landfall_not_on_coast")
            if geodesic_km(
                case.longitude,
                case.latitude,
                case.nearest_coast_longitude,
                case.nearest_coast_latitude,
            ) > 1e-6:
                failures.append("landfall_nearest_coast_mismatch")
            # Verify the anchor lies on a source track segment of at most six hours.
            before = source[source["ISO_TIME"] <= case.reference_time].tail(1)
            after = source[source["ISO_TIME"] >= case.reference_time].head(1)
            if before.empty or after.empty:
                failures.append("landfall_outside_source_track")
            else:
                point_a, point_b = before.iloc[0], after.iloc[0]
                span = (point_b.ISO_TIME - point_a.ISO_TIME).total_seconds()
                if not 0 < span <= 6 * 3600:
                    failures.append("landfall_source_segment_gap")
                else:
                    fraction = (case.reference_time - point_a.ISO_TIME).total_seconds() / span
                    azimuth, _, length_m = WGS84_GEOD.inv(
                        point_a.LON, point_a.LAT, point_b.LON, point_b.LAT
                    )
                    expected_lon, expected_lat, _ = WGS84_GEOD.fwd(
                        point_a.LON, point_a.LAT, azimuth, length_m * fraction
                    )
                    if geodesic_km(expected_lon, expected_lat, case.longitude, case.latitude) > 0.5:
                        failures.append("landfall_not_on_source_segment")
                    # The geodesic crossing solver has sub-kilometer precision;
                    # test direction 500 m either side while separately requiring
                    # the recorded anchor to lie within 500 m of the shoreline.
                    offset = min(500 / max(length_m, 1), fraction / 2, (1 - fraction) / 2)
                    before_lon, before_lat, _ = WGS84_GEOD.fwd(
                        point_a.LON, point_a.LAT, azimuth, length_m * (fraction - offset)
                    )
                    after_lon, after_lat, _ = WGS84_GEOD.fwd(
                        point_a.LON, point_a.LAT, azimuth, length_m * (fraction + offset)
                    )
                    before_land, after_land = coast.points_on_land(
                        [before_lat, after_lat], [before_lon, after_lon]
                    )
                    if bool(before_land) or not bool(after_land):
                        failures.append("landfall_direction_not_sea_to_land")
        elif case.reference_event == "coastal_approach":
            if case.landfall_crossing or case.landfall_count:
                failures.append("approach_has_landfall")
            if surface:
                failures.append("approach_center_on_land")
            if abs(coast_distance.distance_km - case.minimum_coast_distance_km) > 2:
                failures.append("approach_distance_mismatch")
            if abs(
                geodesic_km(
                    case.longitude,
                    case.latitude,
                    case.nearest_coast_longitude,
                    case.nearest_coast_latitude,
                )
                - case.minimum_coast_distance_km
            ) > 2:
                failures.append("approach_nearest_coast_mismatch")
            at_reference = dense.loc[
                (dense["ISO_TIME"] - case.reference_time).abs() <= pd.Timedelta(seconds=1)
            ]
            if at_reference.empty or min(
                geodesic_km(row.LON, row.LAT, case.longitude, case.latitude)
                for row in at_reference.itertuples(index=False)
            ) > 0.1:
                failures.append("approach_not_on_dense_track")
        else:
            failures.append("invalid_reference_event")

        expected_leads = set(experiment["forecast_sampling"]["nominal_lead_hours"])
        allowed_hours = set(experiment["forecast_sampling"]["standard_cycle_hours_utc"])
        max_offset = experiment["forecast_sampling"]["maximum_cycle_offset_hours"]
        if set(schedule["nominal_lead_hours"]) != expected_leads or len(schedule) != len(
            expected_leads
        ):
            failures.append("forecast_lead_coverage")
        for plan in schedule.itertuples(index=False):
            if plan.schedule_status != "matched":
                failures.append("forecast_cycle_unmatched")
                break
            if plan.reference_time != case.reference_time:
                failures.append("forecast_reference_time_mismatch")
                break
            target = case.reference_time - pd.Timedelta(hours=int(plan.nominal_lead_hours))
            recorded_target = pd.Timestamp(plan.target_init_time)
            if recorded_target != target:
                failures.append("forecast_target_time_mismatch")
                break
            if plan.init_time.hour not in allowed_hours or plan.init_time.minute != 0:
                failures.append("forecast_nonstandard_cycle")
                break
            actual = (case.reference_time - plan.init_time).total_seconds() / 3600
            if not np.isclose(actual, plan.actual_lead_hours, atol=1e-6):
                failures.append("forecast_actual_lead_mismatch")
                break
            if abs(actual - plan.nominal_lead_hours) > max_offset + 1e-6:
                failures.append("forecast_cycle_offset_exceeded")
                break
            if not np.isclose(
                (plan.init_time - target).total_seconds() / 3600,
                plan.cycle_offset_hours,
                atol=1e-6,
            ):
                failures.append("forecast_cycle_offset_mismatch")
                break

        if case.landfall_count >= 5:
            notes.append("multiple_landfalls")
        if case.r34_coverage_fraction < 0.8:
            notes.append("limited_r34_coverage")
        if case.usa_wind_coverage_fraction < 0.8:
            notes.append("limited_wind_coverage")
        if not case.episode_start <= case.reference_time <= case.episode_end:
            notes.append("anchor_within_six_hour_padding")
        if case.reference_event == "coastal_approach" and case.minimum_coast_distance_km > 100:
            notes.append("distant_wind_radius_contact")
        records.append(
            {
                "case_id": case.case_id,
                "storm_id": case.storm_id,
                "name": case.name,
                "basin": case.basin,
                "reference_time": case.reference_time,
                "reference_event": case.reference_event,
                "latitude": case.latitude,
                "longitude": case.longitude,
                "coast_distance_km_at_anchor": coast_distance.distance_km,
                "landfall_count": case.landfall_count,
                "r34_coverage_fraction": case.r34_coverage_fraction,
                "usa_wind_coverage_fraction": case.usa_wind_coverage_fraction,
                "qc_status": "passed" if not failures else "failed",
                "failures": ";".join(sorted(set(failures))),
                "review_notes": ";".join(notes),
            }
        )

    event_checks = pd.DataFrame.from_records(records)
    event_checks.to_csv(catalog_dir / "qc_event_checks.csv", index=False)
    approved = selected.drop(columns=["qc_status"]).merge(
        event_checks[["case_id", "qc_status"]], on="case_id", validate="one_to_one"
    )
    approved.to_csv(catalog_dir / "final_main_primary_coastal_impact_cases_qc.csv", index=False)
    summary = {
        "ibtracs_sha256": sha256(source_path),
        "gshhg_archive_sha256": sha256(archive_path),
        "all_case_count": len(all_cases),
        "selected_case_count": len(selected),
        "passed_case_count": int(event_checks["qc_status"].eq("passed").sum()),
        "failed_case_count": int(event_checks["qc_status"].eq("failed").sum()),
        "cases_with_review_notes": int(event_checks["review_notes"].fillna("").ne("").sum()),
        "maximum_landfall_anchor_coast_distance_km": float(
            event_checks.loc[
                event_checks["reference_event"].eq("landfall"), "coast_distance_km_at_anchor"
            ].max()
        ),
        "note_counts": {
            note: int(event_checks["review_notes"].fillna("").str.contains(note).sum())
            for note in (
                "multiple_landfalls",
                "limited_r34_coverage",
                "limited_wind_coverage",
                "anchor_within_six_hour_padding",
                "distant_wind_radius_contact",
            )
        },
        "failure_counts": event_checks.loc[event_checks["qc_status"].eq("failed"), "failures"]
        .value_counts()
        .to_dict(),
    }
    (catalog_dir / "qc_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog-dir", type=Path, required=True)
    args = parser.parse_args()
    summary = audit(args.catalog_dir.resolve())
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["failed_case_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
