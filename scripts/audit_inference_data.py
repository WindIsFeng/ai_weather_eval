"""Read-only audit of transferred Weather Hub runs against the frozen case plan.

Run with ``conda run --name ai-weather-eval python scripts/audit_inference_data.py``.
Checks every file's metadata and every NetCDF header before sampling each variable
at every lead. This deliberately does not claim a full numerical or checksum scan.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from netCDF4 import Dataset, date2num

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "data/handoffs/weather_hub_cases_2022_2024_v2026-09-22.csv"
MODELS = ("graphcast", "fengwu", "fuxi", "pangu", "aurora")
LEVELS = [1000, 925, 850, 700, 600, 500, 400, 300, 250, 200, 150, 100, 50]
UNITS = {
    "t2m": "K", "2t": "K", "t": "K", "msl": "Pa", "u10": "m s-1",
    "v10": "m s-1", "10u": "m s-1", "10v": "m s-1", "u": "m s-1",
    "v": "m s-1", "z": "m2 s-2", "q": "kg kg-1", "r": "%",
    "w": "Pa s-1", "tp6": "m", "tp": "mm",
}


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def csv_rows(path):
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def timing_leads(rows):
    """FuXi uses lead_time_hours; the other writers use lead_hours."""
    return [int(row["lead_time_hours"] if "lead_time_hours" in row else row["lead_hours"])
            for row in rows]


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def products(model):
    surface = ["t2m", "msl", "u10", "v10"]
    upper = ["z", "t", "u", "v", "q"]
    if model == "graphcast":
        surface += ["tp6"]
        upper += ["w"]
    if model == "fuxi":
        surface += ["tp"]
        upper = ["z", "t", "u", "v", "r"]
    if model == "aurora":
        surface = ["2t", "10u", "10v", "msl"]
    return {"surface.nc": surface,
            "atmospheric.nc" if model == "aurora" else "upper.nc": upper}


def compare_case(actual, expected, label, errors):
    for key in ("case_id", "storm_id", "init_time", "forecast_hours", "name", "basin"):
        value = actual.get(key)
        target = expected[key]
        if key == "init_time" and value:
            same = stamp(value) == stamp(target)
        elif key == "forecast_hours" and value is not None:
            same = int(value) == int(target)
        else:
            same = value == target
        if not same:
            errors.append(f"{label}: {key}={value!r}, expected {target!r}")
    if int(actual.get("output_interval_hours", 6)) != 6:
        errors.append(f"{label}: output interval is not six hours")


def inspect_netcdf(task):
    """A separate process owns each netCDF handle; no HDF5 threads are used."""
    path = Path(task["path"])
    result = {"model": task["model"], "case_id": task["case"]["case_id"],
              "path": str(path), "mode": task["mode"], "errors": [],
              "sample_values": 0, "sample_tiles": 0, "sampled_leads": 0}
    errors = result["errors"]
    try:
        with Dataset(path) as ds:
            if task["mode"] == "sample":
                sample_payload(ds, task, result)
                return result
            model, case = task["model"], task["case"]
            expected_leads = np.arange(6, int(case["forecast_hours"]) + 1, 6)
            expected_dims = {"valid_time": len(expected_leads),
                             "latitude": 720 if model == "aurora" else 721,
                             "longitude": 1440}
            upper = path.name != "surface.nc"
            if upper:
                expected_dims["level"] = 13
            if model == "fuxi" and not upper:
                expected_dims["bounds"] = 2
            dims = {key: len(value) for key, value in ds.dimensions.items()}
            result["dimensions"] = dims
            if dims != expected_dims:
                errors.append(f"dimensions {dims}, expected {expected_dims}")
            if getattr(ds, "status", None) != "complete":
                errors.append("NetCDF status is not complete")
            if task.get("forecast_id") and getattr(ds, "forecast_id", "") != task["forecast_id"]:
                errors.append("NetCDF forecast_id disagrees with case.json")
            if model == "aurora" and getattr(ds, "case_id", "") != case["case_id"]:
                errors.append("NetCDF case_id disagrees with frozen plan")
            expected_coords = {
                "latitude": 90 - np.arange(expected_dims["latitude"]) / 4,
                "longitude": np.arange(1440) / 4,
                "lead_time": expected_leads,
            }
            if upper:
                expected_coords["level"] = LEVELS[::-1] if model == "aurora" else LEVELS
            for name, values in expected_coords.items():
                actual = np.ma.asarray(ds[name][:])
                if (actual.shape != np.shape(values) or np.ma.getmaskarray(actual).any()
                        or not np.allclose(actual, values, rtol=0, atol=1e-6)):
                    errors.append(f"invalid {name} coordinate")
            coordinate_units = {"latitude": "degrees_north", "longitude": "degrees_east",
                                "lead_time": "hours"}
            if upper:
                coordinate_units["level"] = "hPa"
            for name, units in coordinate_units.items():
                if getattr(ds[name], "units", None) != units:
                    errors.append(f"invalid {name} units")
            origin = stamp(case["init_time"]).replace(tzinfo=None)
            for name, offset in (("forecast_reference_time", 0), ("valid_time", expected_leads)):
                var = ds[name]
                expected = date2num(origin, var.units,
                                    calendar=getattr(var, "calendar", "standard"))
                # date2num follows the variable's units; convert leads via timestamps.
                if name == "valid_time":
                    from datetime import timedelta
                    expected = date2num([origin + timedelta(hours=int(h)) for h in offset],
                                        var.units, calendar=getattr(var, "calendar", "standard"))
                actual = np.ma.asarray(var[...])
                if (actual.shape != np.shape(expected) or np.ma.getmaskarray(actual).any()
                        or not np.allclose(actual, expected, rtol=0, atol=1e-6)):
                    errors.append(f"invalid {name} values")
            dimensions = ("valid_time", "level", "latitude", "longitude") if upper else (
                "valid_time", "latitude", "longitude")
            expected_variables = set(products(model)[path.name])
            actual_variables = {name for name, var in ds.variables.items()
                                if "latitude" in var.dimensions and "longitude" in var.dimensions}
            if actual_variables != expected_variables:
                errors.append(f"data variables {sorted(actual_variables)}, expected "
                              f"{sorted(expected_variables)}")
            result["variables"] = sorted(actual_variables)
            for name in expected_variables:
                var = ds[name]
                if var.dimensions != dimensions or var.dtype != np.dtype("float32"):
                    errors.append(f"{name}: unexpected dimensions or dtype")
                if getattr(var, "units", None) != UNITS[name]:
                    errors.append(f"{name}: unexpected units")
            if model == "fuxi" and not upper:
                bounds = ds["precipitation_time_bounds"]
                expected = np.column_stack((ds["valid_time"][:] - 6, ds["valid_time"][:]))
                if bounds.dimensions != ("valid_time", "bounds") or not np.allclose(
                        bounds[:], expected, rtol=0, atol=1e-6):
                    errors.append("invalid precipitation time bounds")
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    return result


def sample_payload(ds, task, result):
    """At each lead sample every variable, rotating geography and upper levels.

    Upper fields cover 200/500/850 hPa plus one rotating level at every lead;
    first/last leads cover all levels. Each selection reads a 2x2 grid tile.
    """
    seed = int(hashlib.sha256(task["case"]["case_id"].encode()).hexdigest()[:8], 16)
    ntime = len(ds.dimensions["valid_time"])
    nlat, nlon = len(ds.dimensions["latitude"]), len(ds.dimensions["longitude"])
    stats = {}
    for name in products(task["model"])[Path(task["path"]).name]:
        var = ds[name]
        var.set_var_chunk_cache(size=2 * 1024**2, nelems=101, preemption=0.75)
        stats[name] = {"values": 0, "masked": 0, "nonfinite": 0, "min": None, "max": None}
        for index in range(ntime):
            # Offset within different compressed chunks across successive leads/cases.
            lat = (seed + index * 181) % (nlat - 1)
            lon = (seed // 17 + index * 360) % (nlon - 1)
            selection = (index, slice(lat, lat + 2), slice(lon, lon + 2))
            planes = 1
            if "level" in var.dimensions:
                levels = np.asarray(ds["level"][:])
                chosen = list(range(len(levels))) if index in (0, ntime - 1) else sorted(
                    {int(np.flatnonzero(levels == p)[0]) for p in (200, 500, 850)}
                    | {(seed + index) % len(levels)})
                planes = len(chosen)
                selection = (index, chosen, slice(lat, lat + 2), slice(lon, lon + 2))
            values = np.ma.asarray(var[selection])
            mask = np.ma.getmaskarray(values)
            valid = np.asarray(values.data)[~mask]
            info = stats[name]
            info["values"] += int(values.size)
            info["masked"] += int(mask.sum())
            info["nonfinite"] += int((~np.isfinite(valid)).sum())
            finite = valid[np.isfinite(valid)]
            if finite.size:
                low, high = float(finite.min()), float(finite.max())
                info["min"] = low if info["min"] is None else min(info["min"], low)
                info["max"] = high if info["max"] is None else max(info["max"], high)
            result["sample_values"] += int(values.size)
            result["sample_tiles"] += planes
        if stats[name]["masked"] or stats[name]["nonfinite"]:
            result["errors"].append(f"{name}: masked={stats[name]['masked']}, "
                                    f"nonfinite={stats[name]['nonfinite']} in sampled data")
    result["sampled_leads"] = ntime
    result["statistics"] = stats


def inventory_run(root, model, baseline, output):
    errors, warnings, tasks = [], [], []
    def issue(message):
        errors.append(message)
    expected = {row["case_id"]: row for row in baseline}
    result = load_json(root / "result.json")
    batch = load_json(root / "outputs" / model / "batch.json")
    job = load_json(root / "job.json")
    if job.get("status") != "succeeded" or job.get("exit_code") != 0 or result.get("error"):
        issue("job/result does not indicate successful completion")
    if job.get("effective_config_sha256") != sha256(root / "effective-config.yaml"):
        issue("effective-config.yaml SHA-256 disagrees with job.json")
    for label, rows in (("cases.csv", csv_rows(root / "cases.csv")),
                        ("batch.json", batch["cases"]),
                        ("result batch_metadata", result["batch_metadata"]["cases"])):
        ids = [r["case_id"] for r in rows]
        if len(ids) != len(set(ids)) or set(ids) != set(expected):
            issue(f"{label}: duplicate/missing/extra case IDs")
        for row in rows:
            if row["case_id"] in expected:
                compare_case(row, expected[row["case_id"]], label + ":" + row["case_id"], errors)
    records = result["cases"]
    record_ids = [r["case_id"] for r in records]
    if len(set(record_ids)) != len(record_ids) or set(record_ids) != set(expected):
        issue("result.json: duplicate/missing/extra case IDs")
    record_index = {r["case_id"]: r for r in records}
    cases_dir = root / "outputs" / model / "cases"
    present = {p.name for p in cases_dir.iterdir() if p.is_dir()}
    if present != set(expected):
        issue(f"case directories missing={sorted(set(expected)-present)}, "
              f"extra={sorted(present-set(expected))}")
    all_files, netcdf_bytes, links, empty, partial = {}, 0, [], [], []
    with (output / f"{model}-inventory.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["relative_path", "bytes", "inode", "hardlinks"])
        for parent, dirs, files in os.walk(root, followlinks=False):
            for name in dirs + files:
                path = Path(parent) / name
                if path.is_symlink():
                    links.append(str(path.relative_to(root)))
            for name in files:
                path = Path(parent) / name
                if path.is_symlink():
                    continue
                stat = path.stat()
                relative = str(path.relative_to(root))
                all_files[relative] = stat.st_size
                writer.writerow([relative, stat.st_size, stat.st_ino, stat.st_nlink])
                if path.suffix == ".nc":
                    netcdf_bytes += stat.st_size
                if not stat.st_size and name != ".lock":
                    empty.append(relative)
                if name.endswith((".part", ".partial", ".tmp", ".incomplete")):
                    partial.append(relative)
    if links or partial:
        issue(f"symlinks={links}; partial files={partial}")
    inventory = root / "transfer-inventory.csv"
    inventory_checked = 0
    if inventory.exists():
        for row in csv_rows(inventory):
            relative = row.get("relative_path", row.get("file"))
            size = int(row.get("size_bytes", row.get("bytes")))
            if all_files.get(relative) != size:
                issue(f"transfer inventory mismatch: {relative}, expected {size}, "
                      f"actual {all_files.get(relative)}")
            inventory_checked += 1
    for case_id, expected_case in expected.items():
        case_dir = cases_dir / case_id
        if not case_dir.is_dir():
            continue
        try:
            metadata = load_json(case_dir / "case.json")
            compare_case(metadata, expected_case, case_id + "/case.json", errors)
            if metadata.get("status") not in ("complete", "reused"):
                issue(f"{case_id}: incomplete case.json status")
            record = record_index.get(case_id, {})
            if record.get("status") not in ("complete", "reused") or record.get("error"):
                issue(f"{case_id}: incomplete/failed result.json status")
            if record.get("forecast_id", "") != metadata.get("forecast_id", ""):
                issue(f"{case_id}: result/case forecast IDs disagree")
            manifest = {} if model == "aurora" else load_json(case_dir / "manifest.json")
            if manifest:
                identity = manifest["identity"]
                expected_leads = list(range(6, int(expected_case["forecast_hours"]) + 1, 6))
                if (manifest.get("status") != "complete"
                        or manifest.get("forecast_id") != metadata.get("forecast_id")
                        or identity.get("leads") != expected_leads
                        or stamp(identity["init_time"]) != stamp(expected_case["init_time"])
                        or identity.get("forecast_hours") != int(expected_case["forecast_hours"])):
                    issue(f"{case_id}: manifest identity/status/leads disagree")
                if [s["lead"] for s in manifest["schedule"]] != expected_leads:
                    issue(f"{case_id}: manifest schedule leads disagree")
            timing = csv_rows(case_dir / "step-timing.csv")
            if timing_leads(timing) != list(
                    range(6, int(expected_case["forecast_hours"]) + 1, 6)):
                issue(f"{case_id}: timing leads missing/duplicate/out of order")
            for row in timing:
                value = float(row["inference_seconds"])
                if not np.isfinite(value) or value < 0:
                    issue(f"{case_id}: invalid inference timing")
            for name in products(model):
                path = case_dir / name
                if not path.is_file() or not path.stat().st_size:
                    issue(f"{case_id}: missing or empty {name}")
                    continue
                size = manifest.get("output_sizes", {}).get(path.stem)
                if size is not None and path.stat().st_size != size:
                    issue(f"{case_id}/{name}: size differs from manifest")
                tasks.append({"model": model, "case": expected_case, "path": str(path),
                              "forecast_id": metadata.get("forecast_id", ""), "mode": "structure"})
        except Exception as exc:
            issue(f"{case_id}: {type(exc).__name__}: {exc}")
    expected_nc = {str(Path("outputs") / model / "cases" / case / name)
                   for case in expected for name in products(model)}
    actual_nc = {path for path in all_files if path.endswith(".nc")}
    if actual_nc != expected_nc:
        issue(f"NetCDF inventory missing={sorted(expected_nc-actual_nc)}, "
              f"extra={sorted(actual_nc-expected_nc)}")
    if empty:
        warnings.append(f"zero-byte files (including logs): {empty}")
    warnings.append("No output-payload cryptographic checksum baseline; size/header/sample "
                    "validation cannot establish byte-for-byte equality with the source.")
    summary = {"root": str(root), "expected_cases": len(expected), "present_cases": len(present),
               "expected_case_steps": sum(int(r["forecast_hours"]) // 6 for r in baseline),
               "status_counts": dict(Counter(r["status"] for r in records)),
               "forecast_ids": len({r["forecast_id"] for r in records if r.get("forecast_id")}),
               "netcdf_files": len(actual_nc), "netcdf_bytes": netcdf_bytes,
               "regular_files": len(all_files), "transfer_inventory_entries_checked": inventory_checked,
               "symlinks": links, "partial_files": partial, "empty_files": empty,
               "errors": errors, "warnings": warnings,
               "metadata_sha256": {name: sha256(root / name) for name in (
                   "job.json", "cases.csv", "result.json", "effective-config.yaml")}}
    return summary, tasks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/mnt/usb"))
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--metadata-only", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    baseline = csv_rows(args.baseline)
    if len({row["case_id"] for row in baseline}) != len(baseline):
        raise ValueError("baseline has duplicate case IDs")
    report = {"started_utc": utcnow(), "baseline": str(args.baseline),
              "baseline_sha256": sha256(args.baseline), "script_sha256": sha256(__file__),
              "scope": "all files and NetCDF structures; every variable at every lead sampled",
              "full_numerical_scan": False, "models": {}}
    tasks = []
    runs = {}
    for root in args.root.iterdir():
        if root.is_dir() and (root / "job.json").is_file():
            model = load_json(root / "job.json").get("model")
            if model in MODELS:
                if model in runs:
                    raise ValueError(f"multiple runs for {model}")
                runs[model] = root
    for model in MODELS:
        if model not in runs:
            raise ValueError(f"missing model run: {model}")
        summary, new_tasks = inventory_run(runs[model], model, baseline, args.output)
        report["models"][model] = summary
        tasks.extend(new_tasks)
        print(f"INVENTORY {model}: {summary['present_cases']} cases, "
              f"{summary['netcdf_files']} NetCDF, {len(summary['errors'])} errors", flush=True)
    def save_report():
        report["elapsed_seconds"] = round(time.monotonic() - started, 2)
        temporary = args.output / "summary.json.tmp"
        temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        temporary.replace(args.output / "summary.json")
    report["phase"] = "inventory_complete"
    save_report()
    if args.metadata_only:
        return
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for mode in ("structure", "sample"):
            report["phase"] = mode
            for model in MODELS:
                report["models"][model][mode] = {"checked_files": 0, "failed_files": 0,
                    "sample_values": 0, "sample_tiles": 0, "sampled_leads": 0}
            save_report()
            phase_tasks = [{**task, "mode": mode} for task in tasks]
            last_update = time.monotonic()
            with (args.output / f"{mode}.jsonl").open("w") as handle:
                for index, detail in enumerate(pool.map(inspect_netcdf, phase_tasks, chunksize=1), 1):
                    handle.write(json.dumps(detail, ensure_ascii=False) + "\n")
                    summary = report["models"][detail["model"]][mode]
                    summary["checked_files"] += 1
                    summary["failed_files"] += bool(detail["errors"])
                    for key in ("sample_values", "sample_tiles", "sampled_leads"):
                        summary[key] += detail[key]
                    if time.monotonic() - last_update >= 10 or index == len(phase_tasks):
                        handle.flush()
                        report["progress"] = {"completed_files": index, "total_files": len(phase_tasks)}
                        save_report()
                        print(f"{mode.upper()} {index}/{len(phase_tasks)} files; "
                              f"elapsed={time.monotonic()-started:.0f}s", flush=True)
                        last_update = time.monotonic()
    report["phase"] = "complete"
    report["finished_utc"] = utcnow()
    report["passed"] = all(not value["errors"] and not value["structure"]["failed_files"]
                           and not value["sample"]["failed_files"]
                           for value in report["models"].values())
    save_report()
    print(f"FINISHED passed={report['passed']} report={args.output / 'summary.json'}", flush=True)


if __name__ == "__main__":
    main()
