"""Organize, decode and audit the uploaded ECMWF deterministic BUFR archive.

Run: conda run --name ai-weather-eval python scripts/extract_ifs_bufr.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import pandas as pd

from ai_weather_eval.preprocessing.ifs_bufr import decode_message, split_bufr_messages


ARCHIVE = "ECMF_BUFR_2021-2025.tar.gz"
FROZEN = "2022_2024_v2026-09-22"
STAMP = "2026-10-01"
RAW = Path("raw/baselines/ifs")
INTERIM = Path("interim/ifs_bufr")
REFERENCE = Path("data/frozen_sources/ifs_bufr")


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def extract(root: Path):
    archive = root / RAW / ARCHIVE
    output = root / INTERIM
    output.mkdir(parents=True, exist_ok=True)
    inventory, messages, tracks, failures = [], [], [], []
    with tarfile.open(archive, "r:gz") as bundle:
        for member in sorted(bundle.getmembers(), key=lambda item: item.name):
            if member.isdir():
                continue
            relative = PurePosixPath(member.name)
            if (not member.isfile() or len(relative.parts) != 3
                    or relative.parts[0] != "ECMF_BUFR_2021-2025"
                    or relative.parts[1] not in {"2021", "2022", "2023", "2024", "2025"}
                    or relative.suffix != ".bin" or ".." in relative.parts):
                raise ValueError(f"Unexpected archive member: {member.name}")
            with bundle.extractfile(member) as stream:
                data = stream.read()
            raw_relative = RAW / relative.parts[1] / relative.parts[2]
            destination = root / raw_relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256(data).hexdigest()
            if destination.exists():
                if sha256(destination) != digest:
                    raise ValueError(f"Existing raw file has different content: {destination}")
            else:
                destination.write_bytes(data)
            entry = {
                "source_file": raw_relative.as_posix(), "archive_member": member.name,
                "year": int(relative.parts[1]), "size_bytes": len(data), "sha256": digest,
            }
            try:
                filename_time = re.search(r"_ECMP_(\d{14})_", relative.name)
                if filename_time is None:
                    raise ValueError("Missing initialization time in filename")
                declared_init = datetime.strptime(filename_time[1], "%Y%m%d%H%M%S").replace(
                    tzinfo=timezone.utc
                )
                file_messages, file_tracks = [], []
                for index, encoded in enumerate(split_bufr_messages(data), 1):
                    metadata, rows = decode_message(
                        encoded, message_id=f"{raw_relative.as_posix()}#{index}",
                        source_file=raw_relative.as_posix(),
                    )
                    if datetime.fromisoformat(metadata["init_time"]) != declared_init:
                        raise ValueError("Filename and BUFR initialization disagree")
                    file_messages.append(metadata)
                    file_tracks.extend(rows)
                messages.extend(file_messages)
                tracks.extend(file_tracks)
                entry["decode_status"] = "decoded"
                entry["message_count"] = len(file_messages)
            except Exception as error:
                entry["decode_status"] = "failed"
                entry["error"] = f"{type(error).__name__}: {error}"
                failures.append(entry.copy())
            inventory.append(entry)
    inv = pd.DataFrame(inventory)
    meta = pd.DataFrame(messages)
    points = pd.DataFrame(tracks)
    inv.to_csv(output / "file_inventory.csv", index=False)
    meta.to_csv(output / "messages.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    if not points.empty:
        years = pd.to_datetime(points["init_time"], utc=True).dt.year
        for year, part in points.groupby(years):
            part.to_csv(output / f"tracks_{year}.csv.gz", index=False,
                        compression={"method": "gzip", "mtime": 0})
    qc = {
        "archive_sha256": sha256(archive), "files": len(inv), "decoded_messages": len(meta),
        "decode_failures": failures, "encoded_track_rows": len(points),
        "files_by_year": inv.groupby("year").size().to_dict(),
        "duplicate_file_hashes": int(inv["sha256"].duplicated().sum()),
        "positive_forecast_messages": int(meta["positive_position_count"].gt(0).sum()),
        "no_positive_forecast_messages": int(meta["positive_position_count"].eq(0).sum()),
        "members": sorted(int(x) for x in meta["member_number"].unique()),
        "max_encoded_lead_hours": int(meta["max_encoded_lead_hours"].max()),
    }
    if not points.empty:
        positioned = points[["latitude", "longitude"]].notna().all(axis=1)
        qc["valid_positive_track_rows"] = int((positioned & points["lead_hours"].gt(0)).sum())
        qc["missing_position_rows"] = int((~positioned).sum())
        qc["missing_pressure_rows"] = int(points["pressure_hpa"].isna().sum())
        qc["missing_wind_rows"] = int(points["max_wind_ms"].isna().sum())
        qc["duplicate_track_keys"] = int(points.duplicated(["message_id", "lead_hours"]).sum())
        qc["positive_forecast_messages_by_utc_hour"] = (
            meta.loc[meta["positive_position_count"].gt(0)]
            .groupby(pd.to_datetime(meta["init_time"], utc=True).dt.hour).size().to_dict()
        )
    (output / "qc_summary.json").write_text(json.dumps(qc, indent=2) + "\n")
    if failures:
        raise ValueError(f"{len(failures)} files failed decoding; inspect qc_summary.json")
    return inv, meta, points, qc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path,
                        default=Path(os.environ.get("AI_WEATHER_EVAL_DATA", "data")),
                        help="Shared data root containing raw/ and interim/")
    parser.add_argument("--extract-only", action="store_true")
    parser.add_argument("--match-only", action="store_true",
                        help="Reuse a completed local extraction and rerun cohort matching")
    parser.add_argument("--truth", type=Path, help="Override the frozen IBTrACS source")
    args = parser.parse_args()
    if args.extract_only and args.match_only:
        parser.error("--extract-only and --match-only are mutually exclusive")
    if args.match_only:
        output = args.data_root / INTERIM
        qc = json.loads((output / "qc_summary.json").read_text())
        if qc["decode_failures"] or qc["archive_sha256"] != sha256(
            args.data_root / RAW / ARCHIVE
        ):
            raise ValueError("Existing extraction failed or belongs to another archive")
        options = {"keep_default_na": False, "na_values": [""]}
        inventory = pd.read_csv(output / "file_inventory.csv", **options)
        messages = pd.read_csv(output / "messages.csv.gz", **options)
        tracks = pd.concat([pd.read_csv(path, **options)
                            for path in sorted(output.glob("tracks_*.csv.gz"))], ignore_index=True)
        if (len(inventory), len(messages), len(tracks)) != (
            qc["files"], qc["decoded_messages"], qc["encoded_track_rows"]
        ):
            raise ValueError("Existing extraction row counts disagree with its QC summary")
    else:
        inventory, messages, tracks, qc = extract(args.data_root)
    if args.extract_only:
        print(json.dumps(qc, indent=2))
        return 0
    from ai_weather_eval.preprocessing.ifs_matching import match_ifs_to_cohort

    truth_path = args.truth or (
        args.data_root / "raw/ibtracs/ibtracs.since1980.2026-09-22.study_range.csv"
    )
    if args.truth is None and not truth_path.exists():
        truth_path = Path("data/frozen_sources/ibtracs.since1980.2026-09-22.study_range.csv.gz")
    truth = pd.read_csv(truth_path, skiprows=[1], dtype=str, keep_default_na=False,
                        skipinitialspace=True, low_memory=False)
    read_options = {"keep_default_na": False, "na_values": [""]}
    events_path = Path(f"data/catalogs/coastal_impact_events_{FROZEN}.csv")
    cases_path = Path(f"data/catalogs/coastal_impact_forecast_cases_{FROZEN}.csv")
    events = pd.read_csv(events_path, **read_options)
    cases = pd.read_csv(cases_path, **read_options)
    results = match_ifs_to_cohort(messages, tracks, truth, events, cases)
    output = args.data_root / INTERIM
    artifacts = {}
    for name, frame in results.items():
        path = (output / "storm_links.csv" if name == "storm_links"
                else Path("data/catalogs") / f"ifs_{name}_{STAMP}.csv")
        frame.to_csv(path, index=False)
        artifacts[name] = {"rows": len(frame), "path": path.as_posix(), "sha256": sha256(path)}
    links = results["storm_links"]
    cohort_links = links.loc[
        links["identity_status"].eq("matched_cohort") & links["usable_forecast"]
        & links["message_id"].eq(links["canonical_message_id"]),
        ["message_id", "storm_id", "truth_atcf_id", "storm_name", "source_file"],
    ]
    cohort_tracks = tracks.merge(cohort_links, on="message_id", how="inner", validate="many_to_one")
    cohort_tracks.to_csv(output / "cohort_tracks.csv.gz", index=False,
                         compression={"method": "gzip", "mtime": 0})
    endpoints = []
    by_message = {key: group for key, group in cohort_tracks.groupby("message_id", sort=False)}
    for case in results["case_overlap"].loc[
        results["case_overlap"]["forecast_available"]
    ].itertuples(index=False):
        rows = by_message[case.matched_message_id]
        valid = pd.to_datetime(rows["valid_time"], utc=True)
        chosen = rows.loc[valid.isin([case.previous_valid_time, case.next_valid_time])].copy()
        chosen.insert(0, "forecast_case_id", case.forecast_case_id)
        chosen.insert(1, "case_id", case.case_id)
        chosen.insert(2, "reference_time", case.reference_time)
        chosen.insert(3, "nominal_lead_hours", case.nominal_lead_hours)
        chosen.insert(4, "coverage_method", case.coverage_method)
        endpoints.append(chosen)
    case_endpoints = pd.concat(endpoints, ignore_index=True) if endpoints else pd.DataFrame()
    case_endpoints.to_csv(output / "matched_case_endpoints.csv.gz", index=False,
                         compression={"method": "gzip", "mtime": 0})
    manifest = {
        "dataset": "User-uploaded ECMF deterministic tropical-cyclone BUFR, 2021–2025",
        "organized_at_utc": datetime.now(timezone.utc).isoformat(),
        "archive": {"path": (args.data_root / RAW / ARCHIVE).as_posix(),
                    "sha256": qc["archive_sha256"]},
        "original_extractor": {"path": (REFERENCE / "extract_original.py").as_posix(),
                               "sha256": sha256(REFERENCE / "extract_original.py")},
        "decoder": "ecCodes; actual encoded periods only; HRES member 52",
        "raw_inventory": (output / "file_inventory.csv").as_posix(),
        "qc": qc,
        "cohort": {"events_path": events_path.as_posix(), "events_sha256": sha256(events_path),
                   "cases_path": cases_path.as_posix(), "cases_sha256": sha256(cases_path),
                   "truth_path": truth_path.as_posix(), "truth_sha256": sha256(truth_path)},
        "matching": {"identity": "Same-time observed position within 150 km; 75 km ambiguity gap",
                     "planned_cases": "Exact SID plus frozen init_time; no cycle substitution",
                     "reference_coverage": "Exact position or bracketing positions at most 6 h apart"},
        "artifacts": artifacts,
    }
    for name, frame in (("cohort_tracks", cohort_tracks), ("matched_case_endpoints", case_endpoints)):
        path = output / f"{name}.csv.gz"
        manifest["artifacts"][name] = {
            "rows": len(frame), "path": path.as_posix(), "sha256": sha256(path)
        }
    Path(f"data/manifests/ifs_bufr_{STAMP}.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    )
    print(json.dumps(qc, indent=2))
    print(results["coverage_by_year"].to_string(index=False))
    print(results["coverage_by_lead"].to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
