"""Benchmark forecast tracking on a fixed, seeded 50-task sample.

Run with ``conda run --name ai-weather-eval python scripts/benchmark_tracker.py
--output outputs/tracker_benchmark``. Each timed configuration launches a fresh
CLI process. A four-worker warm-up precedes two interleaved timing rounds; it is
excluded from selection. No operating-system or disk cache is flushed, so results
describe this host and storage under naturally warmed-cache conditions, not cold
disk performance. Fresh output directories prevent checkpoint reuse.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import platform
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from ai_weather_eval.catalog.ibtracs import load_ibtracs_csv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = (
    PROJECT_ROOT / "data/handoffs/weather_hub_cases_2022_2024_v2026-09-22.csv"
)
DEFAULT_IBTRACS = (
    PROJECT_ROOT / "data/frozen_sources/ibtracs.since1980.2026-09-22.study_range.csv.gz"
)
MODELS = ("aurora", "fengwu", "fuxi", "graphcast", "pangu")
WORKERS = (1, 2, 4, 8, 16)
NOMINAL_LEADS = (24, 48, 72, 96, 120)
RUN_COLUMNS = (
    "run_id", "phase", "repeat", "workers", "elapsed_seconds",
    "end_to_end_wall_seconds", "cases_per_second", "planned_points_per_second",
    "sampled_process_tree_peak_rss_mib", "cases", "planned_points", "tracked_points",
    "resumed_cases", "input_error_cases", "tracks_sha256", "output", "stdout_log",
)
CACHE_NOTE = (
    "One excluded 4-worker warm-up; no forced OS or device cache flushing. "
    "Fresh CLI processes and output directories; timings describe this hardware "
    "and storage with naturally warmed caches, not cold-disk performance."
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_runs(path: Path, runs: list[dict]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=RUN_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(runs)
    temporary.replace(path)


def select_cases(cases_file: Path, ibtracs_file: Path) -> list[dict]:
    """Choose by ID, hemisphere, and nominal lead using only exact main-track seeds."""
    truth = load_ibtracs_csv(ibtracs_file)
    truth = truth.loc[truth["TRACK_TYPE_NORMALIZED"].eq("main")]
    seeds = {
        (row.SID, row.ISO_TIME.isoformat().replace("+00:00", "Z")):
            (float(row.LAT), (float(row.LON) + 180.0) % 360.0 - 180.0)
        for row in truth.itertuples(index=False)
    }
    with cases_file.open(newline="", encoding="utf-8-sig") as handle:
        cases = sorted(csv.DictReader(handle), key=lambda row: row["case_id"])
    if len({row["case_id"] for row in cases}) != len(cases):
        raise ValueError("benchmark source contains duplicate forecast case IDs")
    eligible = []
    for row in cases:
        initialized = datetime.fromisoformat(row["init_time"].replace("Z", "+00:00"))
        if initialized.tzinfo is None:
            raise ValueError(f"initialization has no timezone: {row['case_id']}")
        init_time = initialized.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        seed = seeds.get((row["storm_id"], init_time))
        if seed is None or seed[0] == 0.0:
            continue
        eligible.append({
            "forecast_case_id": row["case_id"], "storm_id": row["storm_id"],
            "init_time": init_time, "nominal_lead_hours": int(row["case_id"].rsplit("-L", 1)[1]),
            "forecast_hours": int(row["forecast_hours"]),
            "seed_latitude": seed[0], "seed_longitude": seed[1],
            "hemisphere": "north" if seed[0] > 0 else "south",
        })
    selected = []
    for lead in NOMINAL_LEADS:
        for hemisphere in ("north", "south"):
            candidates = [row for row in eligible
                          if row["nominal_lead_hours"] == lead
                          and row["hemisphere"] == hemisphere]
            if not candidates:
                raise ValueError(f"no exact main seed for {hemisphere}, nominal lead {lead} h")
            selected.append(candidates[0])
    return sorted(selected, key=lambda row: row["forecast_case_id"])


def _run_once(
    args: argparse.Namespace, selected: list[dict], *, workers: int, repeat: int, phase: str,
) -> dict:
    run_id = f"{phase}_r{repeat}_w{workers:02d}"
    output = args.output / run_id
    stdout_log = args.output / f"{run_id}.log"
    command = [
        sys.executable, "-m", "ai_weather_eval.cli", "track",
        "--root", str(args.root), "--output", str(output),
        "--cases-file", str(args.cases_file), "--ibtracs", str(args.ibtracs),
        "--workers", str(workers), "--models", *MODELS,
        "--case-ids", *(row["forecast_case_id"] for row in selected),
    ]
    environment = os.environ.copy()
    for variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                     "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "BLIS_NUM_THREADS"):
        environment[variable] = "1"
    print(f"BENCHMARK {run_id}: {len(selected) * len(MODELS)} tasks", flush=True)
    started = time.monotonic()
    with stdout_log.open("w", encoding="utf-8") as log:
        result = subprocess.run(
            command, stdout=log, stderr=subprocess.STDOUT, check=False,
            cwd=PROJECT_ROOT, env=environment,
        )
    wall = time.monotonic() - started
    if result.returncode:
        raise RuntimeError(f"{run_id} exited {result.returncode}; see {stdout_log}")
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    expected_cases = len(selected) * len(MODELS)
    if summary["cases"] != expected_cases or summary["seeded_cases"] != expected_cases:
        raise RuntimeError(f"{run_id}: expected {expected_cases} seeded model/case tasks")
    if set(summary["models"]) != set(MODELS):
        raise RuntimeError(f"{run_id}: model coverage differs from all five required models")
    if summary.get("resumed_cases") != 0:
        raise RuntimeError(f"{run_id}: reused checkpoints would invalidate benchmark timing")
    input_errors = max(summary.get("input_error_cases", 0),
                       summary["case_status_counts"].get("input_error", 0),
                       summary["point_status_counts"].get("input_error", 0))
    if input_errors:
        raise RuntimeError(f"{run_id}: {input_errors} input errors; see {stdout_log}")
    elapsed = float(summary["elapsed_seconds"])
    peak = float(summary["sampled_process_tree_peak_rss_mib"])
    if not math.isfinite(elapsed) or elapsed <= 0 or not math.isfinite(peak) or peak <= 0:
        raise RuntimeError(f"{run_id}: invalid elapsed time or process-tree memory measurement")
    measurement = {
        "run_id": run_id, "phase": phase, "repeat": repeat, "workers": workers,
        "elapsed_seconds": elapsed, "end_to_end_wall_seconds": round(wall, 6),
        "cases_per_second": expected_cases / elapsed,
        "planned_points_per_second": summary["planned_points"] / elapsed,
        "sampled_process_tree_peak_rss_mib": peak,
        "cases": summary["cases"], "planned_points": summary["planned_points"],
        "tracked_points": summary["tracked_points"], "resumed_cases": 0,
        "input_error_cases": 0, "tracks_sha256": _sha256(output / "tracks.csv"),
        "output": str(output), "stdout_log": str(stdout_log),
    }
    print(f"BENCHMARK {run_id}: {elapsed:.3f}s, {peak:.1f} MiB sampled peak", flush=True)
    return measurement


def _worker_statistics(runs: list[dict]) -> dict[str, dict]:
    timed = [run for run in runs if run["phase"] == "timed"]
    medians = {}
    for workers in sorted({run["workers"] for run in timed}):
        group = [run for run in timed if run["workers"] == workers]
        elapsed = statistics.median(run["elapsed_seconds"] for run in group)
        medians[str(workers)] = {
            "repetitions": len(group), "median_elapsed_seconds": elapsed,
            "median_end_to_end_wall_seconds": statistics.median(
                run["end_to_end_wall_seconds"] for run in group
            ),
            "cases_per_second": group[0]["cases"] / elapsed,
            "planned_points_per_second": group[0]["planned_points"] / elapsed,
            "maximum_sampled_process_tree_peak_rss_mib": max(
                run["sampled_process_tree_peak_rss_mib"] for run in group
            ),
        }
    return medians


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/mnt/usb"))
    parser.add_argument("--output", type=Path, required=True, help="New or empty output directory")
    parser.add_argument("--cases-file", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--ibtracs", type=Path, default=DEFAULT_IBTRACS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    for name in ("root", "output", "cases_file", "ibtracs"):
        setattr(args, name, getattr(args, name).resolve())
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError("benchmark output must be new or empty; prior timings cannot be reused")
    selected = select_cases(args.cases_file, args.ibtracs)
    args.output.mkdir(parents=True, exist_ok=True)
    report = {
        "status": "running", "started_utc": _utcnow(), "cache_interpretation": CACHE_NOTE,
        "root": str(args.root), "cases_file": str(args.cases_file),
        "cases_sha256": _sha256(args.cases_file), "ibtracs_file": str(args.ibtracs),
        "ibtracs_sha256": _sha256(args.ibtracs), "models": list(MODELS),
        "selected_case_ids": [row["forecast_case_id"] for row in selected],
        "selected_cases": selected, "task_count": len(selected) * len(MODELS),
        "hardware": {"host": platform.node(), "platform": platform.platform(),
                     "logical_cpu_count": os.cpu_count()},
        "memory_measurement": (
            "Aggregate process-tree RSS sampled every 0.1 s; shared pages may be "
            "counted more than once. The reported peak is a sampled estimate."
        ),
        "selection_rule": "fewest workers within 5% of fastest median tracker elapsed time",
        "runs": [],
    }
    report_path = args.output / "summary.json"
    _write_json(report_path, report)
    reference_checksum = None

    def measure(workers: int, repeat: int, phase: str) -> None:
        nonlocal reference_checksum
        measurement = _run_once(args, selected, workers=workers, repeat=repeat, phase=phase)
        report["runs"].append(measurement)
        _write_runs(args.output / "benchmark.csv", report["runs"])
        if reference_checksum is None:
            reference_checksum = measurement["tracks_sha256"]
        if measurement["tracks_sha256"] != reference_checksum:
            report["all_tracks_identical"] = False
            raise RuntimeError(f"track CSV differs for {measurement['run_id']}")
        report["tracks_sha256"] = reference_checksum
        report["all_tracks_identical"] = True
        _write_json(report_path, report)

    try:
        measure(4, 0, "warmup")
        for repeat, order in ((1, WORKERS), (2, tuple(reversed(WORKERS)))):
            for workers in order:
                measure(workers, repeat, "timed")
        medians = _worker_statistics(report["runs"])
        test_32 = (medians["16"]["median_elapsed_seconds"]
                   <= 0.9 * medians["8"]["median_elapsed_seconds"])
        report["tested_32_workers"] = test_32
        if test_32:
            for repeat in (1, 2):
                measure(32, repeat, "timed")
            medians = _worker_statistics(report["runs"])
        fastest = min(item["median_elapsed_seconds"] for item in medians.values())
        selected_workers = min(int(workers) for workers, item in medians.items()
                               if item["median_elapsed_seconds"] <= fastest * 1.05)
        report.update(status="complete", finished_utc=_utcnow(), worker_medians=medians,
                      selected_workers=selected_workers, all_tracks_identical=True)
        _write_json(report_path, report)
        print(f"BENCHMARK selected workers={selected_workers}; report: {report_path}", flush=True)
        return 0
    except Exception as exc:
        report.update(status="failed", finished_utc=_utcnow(), error=f"{type(exc).__name__}: {exc}")
        _write_json(report_path, report)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
