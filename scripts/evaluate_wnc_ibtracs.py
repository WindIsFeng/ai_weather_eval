"""Verify 2023–2024 WN-C cyclone tracks against NOAA IBTrACS best tracks.

The evaluation population is tropical or subtropical storms present in the
best track both at initialization and at the target valid time. Run with:
conda run --name ai-weather-eval python scripts/evaluate_wnc_ibtracs.py
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import Geod


LEADS = (24, 48, 72, 96, 120)
HOURS = (0, 6, 12, 18)
GEOD = Geod(ellps="WGS84")
IB_COLS = (
    "SID", "USA_ATCF_ID", "ISO_TIME", "TRACK_TYPE", "NATURE", "BASIN",
    "LAT", "LON", "USA_WIND", "USA_PRES",
)


def cycles(start: date, end: date):
    day = start
    while day <= end:
        for hour in HOURS:
            yield datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc)
        day += timedelta(days=1)


def load_truth(path: Path):
    frame = pd.read_csv(
        path, skiprows=[1], usecols=list(IB_COLS), dtype=str,
        keep_default_na=False, skipinitialspace=True, low_memory=False,
    )
    for col in ("SID", "USA_ATCF_ID", "TRACK_TYPE", "NATURE", "BASIN"):
        frame[col] = frame[col].str.strip()
    frame["ISO_TIME"] = pd.to_datetime(frame["ISO_TIME"], utc=True, errors="raise")
    for col in ("LAT", "LON", "USA_WIND", "USA_PRES"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.loc[
        frame["ISO_TIME"].between("2023-01-01", "2024-12-31 23:59:59")
        & frame["TRACK_TYPE"].str.lower().eq("main")
        & frame["NATURE"].isin(("TS", "SS"))
        & frame["USA_ATCF_ID"].ne("")
    ].copy()
    if frame[["LAT", "LON"]].isna().any().any():
        raise ValueError("IBTrACS contains an eligible row without a position")
    frame["USA_ATCF_ID"] = frame["USA_ATCF_ID"].str.upper()
    if frame.duplicated(["USA_ATCF_ID", "ISO_TIME"]).any():
        raise ValueError("Ambiguous ATCF ID and valid time in IBTrACS")
    truth = {}
    active_by_time = defaultdict(list)
    for row in frame.itertuples(index=False):
        key = (row.USA_ATCF_ID, row.ISO_TIME.to_pydatetime())
        truth[key] = row
        active_by_time[row.ISO_TIME.to_pydatetime()].append(row.USA_ATCF_ID)
    return truth, active_by_time, frame


def read_forecast(path: Path):
    if not path.is_file():
        return []
    with path.open(newline="") as stream:
        reader = csv.DictReader(line for line in stream if not line.startswith("#"))
        return list(reader)


def forecast_path(root: Path, model: str, product: str, init: datetime):
    filename = f"{model}_{init:%Y_%m_%dT%H_00}_paired.csv"
    return root / model / product / str(init.year) / filename


def lead_hours(row, init):
    valid = datetime.fromisoformat(row["valid_time"]).replace(tzinfo=timezone.utc)
    delta = (valid - init).total_seconds() / 3600
    if not delta.is_integer():
        raise ValueError(f"Non-integer lead time: {row['valid_time']}")
    lead = int(delta)
    if row.get("lead_time_hours") not in (None, "") and int(float(row["lead_time_hours"])) != lead:
        raise ValueError(f"Conflicting lead_time_hours: {row}")
    return lead


def float_or_nan(value):
    return float(value) if value not in (None, "") else float("nan")


def empirical_crps(members: np.ndarray, truth: float) -> float:
    return float(np.abs(members - truth).mean() - np.abs(members[:, None] - members[None, :]).mean() / 2)


def position_energy_score(lons: np.ndarray, lats: np.ndarray, truth_lon: float, truth_lat: float):
    """Energy score of a 50-member position ensemble using WGS84 distances."""

    _, _, errors_m = GEOD.inv(lons, lats, np.full(50, truth_lon), np.full(50, truth_lat))
    _, _, pair_m = GEOD.inv(
        np.repeat(lons, 50), np.repeat(lats, 50),
        np.tile(lons, 50), np.tile(lats, 50),
    )
    return float(np.abs(errors_m).mean() / 1000 - np.abs(pair_m).mean() / 2000)


def storm_bootstrap_mae(group: pd.DataFrame, error_column: str, seed: int):
    valid = group.loc[group[error_column].notna(), ["storm_id", error_column]].copy()
    if valid.empty:
        return float("nan"), float("nan")
    valid[error_column] = valid[error_column].abs()
    aggregates = valid.groupby("storm_id")[error_column].agg(["sum", "count"])
    draws = np.random.default_rng(seed).integers(0, len(aggregates), size=(1000, len(aggregates)))
    totals = aggregates["sum"].to_numpy()[draws].sum(axis=1)
    counts = aggregates["count"].to_numpy()[draws].sum(axis=1)
    return tuple(float(value) for value in np.quantile(totals / counts, [0.025, 0.975]))


def link_tracks_at_init(means, active_ids, truth, init):
    """Map Google track IDs to IBTrACS ATCF IDs using same-time positions.

    Southern Indian Ocean IDs often use IO in Weather Lab and SH in IBTrACS.
    A unique position within 150 km is required. Some southern storms also use
    the calendar year in Weather Lab and the following season year in IBTrACS.
    """

    matches = {}
    claimed = set()
    for (model_id, lead), row in means.items():
        if lead != 0:
            continue
        lat, lon = float_or_nan(row["lat"]), float_or_nan(row["lon"])
        if not (np.isfinite(lat) and np.isfinite(lon)):
            continue
        candidates = []
        for truth_id in active_ids:
            target = truth[(truth_id, init)]
            _, _, meters = GEOD.inv(lon, lat, target.LON, target.LAT)
            if abs(meters) <= 150_000:
                candidates.append((abs(meters), truth_id))
        candidates.sort()
        if not candidates or (len(candidates) > 1 and candidates[1][0] - candidates[0][0] < 75_000):
            continue
        distance_m, truth_id = candidates[0]
        if truth_id in claimed:
            raise ValueError(f"Two Weather Lab tracks map to {truth_id} at {init}")
        claimed.add(truth_id)
        method = (
            "exact" if model_id == truth_id else
            "position_suffix" if model_id[-6:] == truth_id[-6:] else "position_only"
        )
        matches[truth_id] = (model_id, method, distance_m / 1000)
    return matches


def verify(root: Path, model: str, ib_path: Path, output: Path):
    truth, active_by_time, truth_frame = load_truth(ib_path)
    records = []
    qc = Counter()
    for init in cycles(date(2023, 1, 1), date(2024, 12, 31)):
        eligible = []
        for track_id in active_by_time.get(init, []):
            for lead in LEADS:
                valid = init + timedelta(hours=lead)
                if target := truth.get((track_id, valid)):
                    eligible.append((track_id, lead, target))
        if not eligible:
            continue
        mean_path = forecast_path(root, model, "ensemble_mean", init)
        ensemble_path = forecast_path(root, model, "ensemble", init)
        means = {}
        for row in read_forecast(mean_path):
            lead = lead_hours(row, init)
            if lead in LEADS or lead == 0:
                key = (row["track_id"].strip().upper(), lead)
                if key in means:
                    raise ValueError(f"Duplicate mean forecast key: {mean_path} {key}")
                means[key] = row
        members = defaultdict(dict)
        for row in read_forecast(ensemble_path):
            lead = lead_hours(row, init)
            if lead in LEADS:
                key = (row["track_id"].strip().upper(), lead)
                sample = int(float(row["sample"]))
                if sample in members[key]:
                    raise ValueError(f"Duplicate member forecast key: {ensemble_path} {key} {sample}")
                members[key][sample] = (
                    float_or_nan(row["maximum_sustained_wind_speed_knots"]),
                    float_or_nan(row["lon"]), float_or_nan(row["lat"]),
                )
        if not mean_path.is_file():
            qc["missing_mean_files_needed"] += 1
        if not ensemble_path.is_file():
            qc["missing_ensemble_files_needed"] += 1
        track_links = link_tracks_at_init(means, active_by_time.get(init, []), truth, init)
        for _, method, _ in track_links.values():
            qc[f"linked_tracks_{method}"] += 1
        for track_id, lead, target in eligible:
            model_id, mapping_method, match_distance = track_links.get(
                track_id, (None, "unlinked", float("nan"))
            )
            key = (model_id, lead)
            row = means.get(key)
            member_values = members.get(key, {})
            observed_vmax = float(target.USA_WIND)
            observed_pressure = float(target.USA_PRES)
            item = {
                "init_time": init.isoformat(), "valid_time": (init + timedelta(hours=lead)).isoformat(),
                "init_year": init.year, "lead_hours": lead, "track_id": track_id,
                "storm_id": target.SID, "basin": target.BASIN, "forecast_present": row is not None,
                "forecast_track_id": model_id, "id_mapping_method": mapping_method,
                "init_match_distance_km": match_distance,
                "init_truth_vmax_kt": float(truth[(track_id, init)].USA_WIND),
                "truth_vmax_kt": observed_vmax, "truth_pressure_hpa": observed_pressure,
                "member_count": len(member_values),
                "track_error_km": float("nan"), "vmax_error_kt": float("nan"),
                "pressure_error_hpa": float("nan"), "vmax_crps_kt": float("nan"),
                "vmax_brier_64": float("nan"), "vmax_probability_64": float("nan"),
                "track_energy_score_km": float("nan"),
            }
            if row is not None:
                lon, lat = float_or_nan(row["lon"]), float_or_nan(row["lat"])
                if np.isfinite(lon) and np.isfinite(lat):
                    _, _, meters = GEOD.inv(lon, lat, target.LON, target.LAT)
                    item["track_error_km"] = abs(meters) / 1000
                vmax = float_or_nan(row["maximum_sustained_wind_speed_knots"])
                if np.isfinite(vmax) and np.isfinite(observed_vmax):
                    item["vmax_error_kt"] = vmax - observed_vmax
                pressure = float_or_nan(row["minimum_sea_level_pressure_hpa"])
                if np.isfinite(pressure) and np.isfinite(observed_pressure):
                    item["pressure_error_hpa"] = pressure - observed_pressure
                if set(member_values) == set(range(50)):
                    array = np.array([member_values[sample] for sample in range(50)])
                    if np.isfinite(array[:, 1:]).all():
                        item["track_energy_score_km"] = position_energy_score(
                            array[:, 1], array[:, 2], target.LON, target.LAT
                        )
                    if np.isfinite(array[:, 0]).all() and np.isfinite(observed_vmax):
                        item["vmax_crps_kt"] = empirical_crps(array[:, 0], observed_vmax)
                        probability = float((array[:, 0] >= 64).mean())
                        item["vmax_probability_64"] = probability
                        item["vmax_brier_64"] = (probability - float(observed_vmax >= 64)) ** 2
            records.append(item)
    frame = pd.DataFrame.from_records(records)
    output.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output / "verification_cases.csv", index=False)
    summaries = []
    for (year, lead), group in frame.groupby(["init_year", "lead_hours"]):
        track = group["track_error_km"].dropna()
        intensity = group["vmax_error_kt"].dropna()
        pressure = group["pressure_error_hpa"].dropna()
        track_ci = storm_bootstrap_mae(group, "track_error_km", seed=20220801 + int(year) + int(lead))
        vmax_ci = storm_bootstrap_mae(group, "vmax_error_kt", seed=20230801 + int(year) + int(lead))
        summaries.append({
            "year": int(year), "lead_hours": int(lead), "eligible": len(group),
            "matched": int(group["forecast_present"].sum()),
            "storms": int(group.loc[group["forecast_present"], "storm_id"].nunique()),
            "track_n": len(track), "track_mae_km": float(track.mean()),
            "track_median_km": float(track.median()),
            "track_mae_ci_low_km": track_ci[0], "track_mae_ci_high_km": track_ci[1],
            "vmax_n": len(intensity), "vmax_mae_kt": float(intensity.abs().mean()),
            "vmax_bias_kt": float(intensity.mean()),
            "vmax_mae_ci_low_kt": vmax_ci[0], "vmax_mae_ci_high_kt": vmax_ci[1],
            "pressure_n": len(pressure), "pressure_mae_hpa": float(pressure.abs().mean()),
            "full_ensemble_n": int(group["vmax_crps_kt"].notna().sum()),
            "vmax_crps_kt": float(group["vmax_crps_kt"].mean()),
            "vmax_brier_64": float(group["vmax_brier_64"].mean()),
            "track_energy_score_km": float(group["track_energy_score_km"].mean()),
        })
    summary = pd.DataFrame(summaries)
    summary.to_csv(output / "summary_by_year_lead.csv", index=False, float_format="%.3f")
    overview = {
        "model": model,
        "truth_file": str(ib_path),
        "truth_rows_2023_2024_tropical_subtropical_main_atcf": len(truth_frame),
        "eligible_forecast_cases": len(frame),
        "matched_forecast_cases": int(frame["forecast_present"].sum()),
        "unique_storms_eligible": int(frame["storm_id"].nunique()),
        "unique_storms_matched": int(frame.loc[frame["forecast_present"], "storm_id"].nunique()),
        "vmax_truth_available_matched": int(frame.loc[frame["forecast_present"], "truth_vmax_kt"].notna().sum()),
        "complete_50_member_cases": int(frame["vmax_crps_kt"].notna().sum()),
        **dict(qc),
    }
    (output / "overview.json").write_text(json.dumps(overview, indent=2) + "\n")
    print(json.dumps(overview, indent=2))
    print(summary.to_string(index=False, float_format=lambda value: f"{value:.2f}"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("data/raw/forecasts/wnc_weatherlab"))
    parser.add_argument("--model", default="FNV3P2")
    parser.add_argument("--ibtracs", type=Path, default=Path("data/raw/ibtracs/ibtracs_2023_2024_range.csv"))
    parser.add_argument("--output", type=Path, default=Path("outputs/wnc_2023_2024"))
    args = parser.parse_args()
    verify(args.root, args.model, args.ibtracs, args.output)


if __name__ == "__main__":
    main()
