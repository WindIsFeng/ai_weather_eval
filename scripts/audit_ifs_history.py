"""Reconcile downloaded AWS BUFR and NCAR TIGGE tracks with the frozen IFS baseline."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime
from itertools import combinations
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np
import pandas as pd
from download_ifs_history import INTERIM, PLAN, raw_path
from extract_ifs_bufr import sha256

from ai_weather_eval.preprocessing.ifs_cxml import decode_deterministic
from ai_weather_eval.preprocessing.ifs_matching import GEOD, match_ifs_to_cohort

STAMP = "2026-11-10"
CATALOGS = Path("data/catalogs")
BASE = Path("data/manifests/ifs_bufr_2026-10-01.json")
TRUTH = Path("data/frozen_sources/ibtracs.since1980.2026-09-22.study_range.csv.gz")
CASE_OUT = CATALOGS / f"ifs_case_supplement_{STAMP}.csv"
MANIFEST = Path(f"data/manifests/ifs_historical_tracks_{STAMP}.json")
READ_OPTIONS = {"keep_default_na": False, "na_values": [""]}
GZIP = {"method": "gzip", "mtime": 0}
SOURCES = ("uploaded_bufr", "aws", "ncar")
COVERAGE = ("forecast_available", "forecast_wind_available", "forecast_pressure_available")
SELECTION = [
    *COVERAGE, "matched_message_id", "source_file", "coverage_method",
    "previous_valid_time", "next_valid_time",
]
MESSAGE_FIELDS = [
    "message_id", "source_file", "init_time", "storm_identifier", "storm_name",
    "member_number", "observed_latitude", "observed_longitude",
]
TRACK_FIELDS = [
    "message_id", "init_time", "member_number", "lead_hours", "valid_time",
    "latitude", "longitude", "pressure_hpa", "max_wind_ms",
]


def utc(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, utc=True, format="mixed")


def index_by(frame: pd.DataFrame, key: str) -> pd.DataFrame:
    if frame[key].duplicated().any():
        raise ValueError(f"Duplicate {key} in the audit inputs")
    return frame.set_index(key)


def read_checked(path: Path, expected_hash: str, **options) -> pd.DataFrame:
    if sha256(path) != expected_hash:
        raise ValueError(f"Frozen input has changed: {path}")
    return pd.read_csv(path, **READ_OPTIONS, **options)


def decode_aws(root: Path, files: list[dict]) -> dict:
    ready = [
        (str(root / r["source_file"]), r["source_file"], r["init_time"])
        for r in files if r["download_status"] == "available"
    ]
    worker = Path(__file__).with_name("decode_ifs_history_aws.py")
    result = subprocess.run(
        [sys.executable, str(worker)], input=json.dumps(ready),
        text=True, stdout=subprocess.PIPE, check=True,
    )
    return json.loads(result.stdout)


def match_source(source: str, root: Path, plan: pd.DataFrame, truth, events, cases):
    subset = plan.loc[plan.source.eq(source)]
    downloaded = pd.read_csv(root / INTERIM / f"{source}_downloads.csv", **READ_OPTIONS)
    downloads = index_by(downloaded, "object_key").to_dict("index")
    inventory, messages, points = [], [], []
    for row in subset.itertuples(index=False):
        init = datetime.fromisoformat(row.init_time)
        relative = raw_path(source, init, row.object_key)
        download = downloads.get(row.object_key, {})
        download_status = download.get("download_status", "not_attempted")
        acquired = download_status in ("downloaded", "existing")
        byte_range = download.get("byte_range")
        if pd.notna(byte_range) and byte_range:
            if source != "ncar" or byte_range != f"0-{int(download['size_bytes']) - 1}":
                raise ValueError(f"Invalid archive byte range for {row.object_key}: {byte_range}")
            relative = relative.with_suffix(".xml.prefix")
        record = {
            "source": source, "init_time": row.init_time, "object_key": row.object_key,
            "source_file": relative.as_posix(),
            "download_status": "available" if acquired else download_status,
            "download_url": download.get("download_url"), "byte_range": byte_range,
            "size_bytes": download.get("size_bytes", 0),
            "sha256": download.get("sha256"), "error": download.get("error"),
            "decode_status": "not_decoded", "decoded_tracks": 0, "track_points": 0,
        }
        if acquired:
            path = root / relative
            if sha256(path) != record["sha256"]:
                raise ValueError(f"Raw file checksum disagrees with the download inventory: {path}")
        inventory.append(record)

    decoded = decode_aws(root, inventory) if source == "aws" else {}
    for record in inventory:
        if record["download_status"] != "available":
            continue
        source_file = record["source_file"]
        if source == "aws":
            item = decoded[source_file]
            if "error" in item:
                record.update(decode_status="failed", error=item["error"])
                continue
            msg, pts = item["messages"], item["points"]
        else:
            try:
                msg, pts = decode_deterministic(
                    root / source_file, source_file=source_file,
                    init=datetime.fromisoformat(record["init_time"]),
                )
            except (ValueError, ET.ParseError) as exc:
                record.update(decode_status="failed", error=f"{type(exc).__name__}: {exc}")
                continue
        messages.extend(msg)
        points.extend(pts)
        record.update(
            decode_status="decoded" if msg else "no_deterministic_tracks",
            decoded_tracks=len(msg), track_points=len(pts),
        )
    files = pd.DataFrame(inventory)
    meta = pd.DataFrame(messages) if messages else pd.DataFrame(columns=MESSAGE_FIELDS)
    tracks = pd.DataFrame(points) if points else pd.DataFrame(columns=TRACK_FIELDS)
    matched = match_ifs_to_cohort(meta, tracks, truth, events, cases)
    links = matched["storm_links"]
    canonical = links.loc[
        links.identity_status.eq("matched_cohort") & links.usable_forecast
        & links.message_id.eq(links.canonical_message_id),
        ["message_id", "storm_id", "truth_atcf_id", "storm_name", "source_file"],
    ]
    tracks = tracks.merge(canonical, on="message_id", how="inner", validate="many_to_one")
    return files, matched, tracks


def archive_case_status(overlap: pd.DataFrame, inventory: pd.DataFrame) -> pd.Series:
    by_cycle = index_by(inventory.assign(init_time=utc(inventory.init_time)), "init_time")
    init = utc(overlap.init_time)
    downloads = init.map(by_cycle.download_status)
    decoded = init.map(by_cycle.decode_status)
    status = overlap.unavailable_reason.copy()
    status.loc[overlap.forecast_available] = "track_only"
    status.loc[overlap[list(COVERAGE)].all(axis=1)] = "track_and_intensity"
    status.loc[decoded.eq("no_deterministic_tracks")] = "no_deterministic_tracks"
    status.loc[decoded.eq("failed")] = "decode_failed"
    status.loc[downloads.eq("failed")] = "download_failed"
    status.loc[downloads.eq("not_attempted")] = "not_attempted"
    status.loc[downloads.isna()] = "not_in_request_plan"
    return status


def paired_comparison(tracks: pd.DataFrame) -> dict:
    fields = ["latitude", "longitude", "pressure_hpa", "max_wind_ms"]
    keys = ["storm_id", "init_time", "lead_hours"]
    positive = tracks.loc[tracks.lead_hours.gt(0)].dropna(subset=fields)
    results = {}
    for first, second in combinations(SOURCES, 2):
        a, b = (
            positive.loc[positive.forecast_source.eq(source), keys + fields]
            for source in (first, second)
        )
        paired = a.merge(b, on=keys, suffixes=("_a", "_b"), validate="one_to_one")
        result = {"paired_points": len(paired)}
        if not paired.empty:
            _, _, meters = GEOD.inv(
                paired.longitude_a.to_numpy(), paired.latitude_a.to_numpy(),
                paired.longitude_b.to_numpy(), paired.latitude_b.to_numpy(),
            )
            wind = (paired.max_wind_ms_a - paired.max_wind_ms_b).abs()
            pressure = (paired.pressure_hpa_a - paired.pressure_hpa_b).abs()
            result.update(
                identical_positions_wind_pressure=int(
                    (np.isclose(meters, 0) & np.isclose(wind, 0) & np.isclose(pressure, 0)).sum()
                ),
                median_distance_km=float(np.median(meters) / 1000),
                max_distance_km=float(np.max(meters) / 1000),
                max_wind_difference_ms=float(wind.max()),
                max_pressure_difference_hpa=float(pressure.max()),
            )
        results[f"{first}_vs_{second}"] = result
    return results


def save(frame: pd.DataFrame, path: Path) -> dict:
    frame.to_csv(path, index=False, compression=GZIP if path.suffix == ".gz" else None)
    return {"path": path.as_posix(), "rows": len(frame), "sha256": sha256(path)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path,
                        default=Path(os.environ.get("AI_WEATHER_EVAL_DATA", "data")))
    parser.add_argument("--publish", action="store_true",
                        help="Write the dated repository manifest and case supplement")
    args = parser.parse_args()
    root = args.data_root
    local = root / INTERIM
    baseline = json.loads(BASE.read_text())
    inputs = baseline["cohort"]
    base_artifacts = baseline["artifacts"]
    cases = read_checked(Path(inputs["cases_path"]), inputs["cases_sha256"])
    events = read_checked(Path(inputs["events_path"]), inputs["events_sha256"])
    with gzip.open(TRUTH, "rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != inputs["truth_sha256"]:
            raise ValueError("Frozen IBTrACS content differs from the original baseline")
    truth = pd.read_csv(TRUTH, skiprows=[1], dtype=str, keep_default_na=False,
                        skipinitialspace=True, low_memory=False)
    baseline_cases = index_by(read_checked(
        Path(base_artifacts["case_overlap"]["path"]), base_artifacts["case_overlap"]["sha256"],
    ), "forecast_case_id")
    baseline_events = index_by(read_checked(
        Path(base_artifacts["event_overlap"]["path"]), base_artifacts["event_overlap"]["sha256"],
    ), "case_id")
    base_tracks = read_checked(root / "interim/ifs_bufr/cohort_tracks.csv.gz",
                               base_artifacts["cohort_tracks"]["sha256"])

    plan = pd.read_csv(PLAN, **READ_OPTIONS)
    combined = index_by(cases[[
        "forecast_case_id", "case_id", "storm_id", "init_time", "reference_time",
        "nominal_lead_hours",
    ]], "forecast_case_id")
    combined[SELECTION] = baseline_cases[SELECTION]
    for column in ("previous_valid_time", "next_valid_time"):
        combined[column] = utc(combined[column])
    combined["baseline_available"] = combined[list(COVERAGE)].all(axis=1)
    combined["forecast_source"] = np.where(combined.baseline_available, SOURCES[0], "")
    combined["baseline_unavailable_reason"] = baseline_cases.unavailable_reason
    coverage_events = baseline_events.event_time_covered.copy()
    tracks = [base_tracks.assign(forecast_source=SOURCES[0])]
    inventories, audits, source_summary = [], {}, {}
    for source in SOURCES[1:]:
        inventory, result, points = match_source(source, root, plan, truth, events, cases)
        inventories.append(inventory)
        audits[source] = result["storm_links"]
        tracks.append(points.assign(forecast_source=source))
        case_overlap = index_by(result["case_overlap"], "forecast_case_id")
        combined[f"{source}_status"] = archive_case_status(case_overlap, inventory)
        for column in ("previous_valid_time", "next_valid_time"):
            case_overlap[column] = utc(case_overlap[column])
        all_fields = case_overlap[list(COVERAGE)].all(axis=1)
        take = all_fields & combined.forecast_source.eq("")
        combined.loc[take, SELECTION] = case_overlap.loc[take, SELECTION]
        combined.loc[take, "forecast_source"] = source
        source_summary[source] = {
            "files": inventory.download_status.value_counts().to_dict(),
            "decode_status": inventory.decode_status.value_counts().to_dict(),
            "bytes": int(inventory.size_bytes.sum()),
            "available_cxml_prefixes": int(inventory.byte_range.notna().sum()),
            "decoded_tracks": int(inventory.decoded_tracks.sum()),
            "track_points": int(inventory.track_points.sum()),
            "matching_outcomes": result["storm_links"].identity_status.value_counts().to_dict(),
            "new_cases": int(take.sum()),
        }
        coverage_events |= result["event_overlap"].set_index("case_id").event_time_covered
    combined = combined.reset_index()
    for column in ("init_time", "reference_time", "previous_valid_time", "next_valid_time"):
        combined[column] = utc(combined[column])
    tracks = pd.concat([part for part in tracks if not part.empty], ignore_index=True)
    tracks["init_time"], tracks["valid_time"] = utc(tracks.init_time), utc(tracks.valid_time)
    if not combined[list(COVERAGE)].all(axis=1).eq(combined.forecast_source.ne("")).all():
        raise ValueError("Case selection and joint position/intensity coverage disagree")
    select = combined.loc[combined.forecast_source.ne("")]
    support = select[[
        "forecast_case_id", "case_id", "reference_time", "nominal_lead_hours",
        "coverage_method", "forecast_source", "matched_message_id",
        "previous_valid_time", "next_valid_time",
    ]].melt(
        value_vars=["previous_valid_time", "next_valid_time"],
        value_name="valid_time", var_name="endpoint",
        id_vars=["forecast_case_id", "case_id", "reference_time", "nominal_lead_hours",
                 "coverage_method", "forecast_source", "matched_message_id"],
    ).drop_duplicates(["forecast_case_id", "valid_time"])
    endpoints = support.drop(columns="endpoint").merge(
        tracks, left_on=["forecast_source", "matched_message_id", "valid_time"],
        right_on=["forecast_source", "message_id", "valid_time"], validate="many_to_one",
    ).sort_values(["forecast_case_id", "valid_time"])
    if len(endpoints) != len(support) or endpoints[
        ["latitude", "longitude", "pressure_hpa", "max_wind_ms"]
    ].isna().any().any():
        raise ValueError("Missing required selected-case endpoint")
    files = pd.concat(inventories, ignore_index=True).sort_values(["source", "init_time"])
    if args.publish and files.download_status.eq("not_attempted").any():
        raise ValueError("Complete both download attempts before publishing the dated snapshot")

    artifacts = {
        "file_inventory": save(files, PLAN if args.publish else local / "file_inventory.csv"),
        "case_supplement": save(
            combined, CASE_OUT if args.publish else local / "case_supplement.csv",
        ),
        "cohort_tracks": save(tracks, local / "cohort_tracks.csv.gz"),
        "matched_case_endpoints": save(endpoints, local / "matched_case_endpoints.csv.gz"),
    }
    for source, links in audits.items():
        artifacts[f"{source}_storm_links"] = save(links, local / f"{source}_storm_links.csv.gz")

    manifest = {
        "baseline_manifest": BASE.as_posix(), "baseline_manifest_sha256": sha256(BASE),
        "sources": source_summary,
        "selection": "Uploaded BUFR, then AWS HRES BUFR, then NCAR ECMWF deterministic CXML",
        "coverage": {
            "total_cases": len(combined),
            "available_cases": len(select),
            "cases_by_source": select.forecast_source.value_counts().to_dict(),
            "available_cases_by_lead": select.groupby("nominal_lead_hours").size().to_dict(),
            "events_with_any_planned_case": select.case_id.nunique(),
            "events_with_any_earlier_forecast": int(coverage_events.sum()),
            "new_events_with_any_earlier_forecast": list(
                coverage_events.index[coverage_events & ~baseline_events.event_time_covered]
            ),
        },
        "matched_track_comparison": paired_comparison(tracks),
        "artifacts": artifacts,
    }
    destination = MANIFEST if args.publish else local / "audit.json"
    destination.write_text(json.dumps(manifest, indent=2) + "\n")
    summary = {key: manifest[key] for key in ("sources", "coverage", "matched_track_comparison")}
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
