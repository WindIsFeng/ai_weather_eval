"""Reproducible, resumable extraction of Weather Hub tropical-cyclone tracks."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import multiprocessing
import os
import resource
import time
from collections import Counter
from collections.abc import Sequence
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ai_weather_eval.catalog.ibtracs import load_ibtracs_csv
from ai_weather_eval.tracking.core import DEFAULT_CONFIG, TrackerConfig, track_forecast
from ai_weather_eval.tracking.reader import WeatherHubForecast
from ai_weather_eval.tracking.resources import ProcessMemorySampler

MODELS = ("aurora", "fengwu", "fuxi", "graphcast", "pangu")
TRACK_COLUMNS = (
    "model", "forecast_case_id", "case_id", "storm_id", "init_time", "valid_time",
    "nominal_lead_hours", "lead_hours", "latitude", "longitude", "msl_hpa", "vmax10_ms",
    "cyclonic_vorticity_s", "status", "reason",
)
CASE_COLUMNS = (
    "model", "forecast_case_id", "case_id", "storm_id", "init_time", "nominal_lead_hours",
    "forecast_hours", "seed_latitude", "seed_longitude", "status", "reason", "planned_points",
    "tracked_points", "last_tracked_lead_hours", "last_tracked_time", "elapsed_seconds",
    "peak_memory_mib", "checkpoint", "fingerprint", "resumed",
)
_THREAD_VARIABLES = (
    "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "BLIS_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS",
)


def _single_threaded_numerics() -> None:
    for variable in _THREAD_VARIABLES:
        os.environ[variable] = "1"


@contextmanager
def _worker_environment():
    previous = {variable: os.environ.get(variable) for variable in _THREAD_VARIABLES}
    _single_threaded_numerics()
    try:
        yield
    finally:
        for variable, value in previous.items():
            if value is None:
                os.environ.pop(variable, None)
            else:
                os.environ[variable] = value


def _canonical_model(model: str) -> str:
    return "pangu" if model == "pangu_weather" else model


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"initialization must include a UTC offset: {value}")
    return parsed.astimezone(timezone.utc)


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_csv(path: Path, rows: Sequence[dict], columns: Sequence[str]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def _file_identity(path: Path) -> dict:
    result = {"path": str(path.resolve()), "exists": path.is_file()}
    if result["exists"]:
        stat = path.stat()
        result.update(size=stat.st_size, mtime_ns=stat.st_mtime_ns)
    return result


def discover_runs(root: Path, models: Sequence[str] | None = None) -> dict[str, dict]:
    """Find one job per canonical model, retaining its actual on-disk model name."""
    selected = set(MODELS if models is None else map(_canonical_model, models))
    if not selected or selected.difference(MODELS):
        raise ValueError(f"models must be selected from {', '.join(MODELS)}")
    candidates = [root] if (root / "job.json").is_file() else sorted(root.iterdir())
    runs = {}
    for candidate in candidates:
        job_file = candidate / "job.json"
        if not job_file.is_file():
            continue
        source_model = str(_json(job_file).get("model", ""))
        model = _canonical_model(source_model)
        if model not in selected:
            continue
        if model in runs:
            raise ValueError(f"multiple model runs found for {model}")
        runs[model] = {"root": candidate.resolve(), "source_model": source_model}
    missing = selected.difference(runs)
    if missing:
        raise ValueError(f"missing model runs: {', '.join(sorted(missing))}")
    return dict(sorted(runs.items()))


def _load_cases(path: Path, case_ids: Sequence[str] | None) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {"case_id", "storm_id", "init_time", "forecast_hours"}
        if not required.issubset(reader.fieldnames or ()):
            raise ValueError(f"case CSV requires columns: {', '.join(sorted(required))}")
        rows = list(reader)
    seen = set()
    for row in rows:
        forecast_id = row["case_id"]
        if forecast_id in seen:
            raise ValueError(f"duplicate forecast case ID: {forecast_id}")
        seen.add(forecast_id)
        try:
            event_id, nominal = forecast_id.rsplit("-L", 1)
            nominal_lead = int(nominal)
            horizon = int(row["forecast_hours"])
        except ValueError as exc:
            raise ValueError(f"invalid case identity or forecast horizon: {forecast_id}") from exc
        if not event_id or "/" in forecast_id or "\\" in forecast_id:
            raise ValueError(f"invalid forecast case ID: {forecast_id}")
        if horizon <= 0 or horizon % 6:
            raise ValueError(f"forecast_hours must be a positive multiple of 6: {forecast_id}")
        if row.get("output_interval_hours", "6") not in ("", "6"):
            raise ValueError(f"tracker requires six-hour forecast output: {forecast_id}")
        init = _timestamp(row["init_time"])
        row.update(forecast_case_id=forecast_id, case_id=event_id,
                   nominal_lead_hours=nominal_lead, forecast_hours=horizon, init_time=_utc(init))
    if case_ids is not None:
        selected = set(case_ids)
        missing = selected.difference(seen)
        if missing:
            raise ValueError(f"unknown forecast case IDs: {', '.join(sorted(missing))}")
        rows = [row for row in rows if row["forecast_case_id"] in selected]
    if not rows:
        raise ValueError("no forecast cases selected")
    return sorted(rows, key=lambda row: row["forecast_case_id"])


def _seeds(path: Path) -> dict[tuple[str, str], tuple[float, float]]:
    # The lookup contains no future positions; only exact initialization keys are used.
    truth = load_ibtracs_csv(path)
    truth = truth[truth["TRACK_TYPE_NORMALIZED"] == "main"]
    return {
        (row.SID, _utc(row.ISO_TIME.to_pydatetime())):
            (float(row.LAT), (float(row.LON) + 180.0) % 360.0 - 180.0)
        for row in truth.itertuples(index=False)
    }


def _empty_points(case: dict, status: str, reason: str) -> list[dict]:
    return [
        {"lead_hours": lead, "latitude": None, "longitude": None, "msl_hpa": None,
         "vmax10_ms": None, "cyclonic_vorticity_s": None,
         "status": status if status == "no_seed" or lead == 6 else "terminated",
         "reason": reason}
        for lead in range(6, case["forecast_hours"] + 1, 6)
    ]


def _clean_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _process_case(task: dict, config: TrackerConfig) -> dict:
    """Execute one independent path; opening NetCDF is always local to this call."""
    started = time.monotonic()
    case = task["case"]
    seed = task["seed"]
    if task.get("worker_error"):
        points = _empty_points(case, "input_error", task["worker_error"])
    elif seed is None:
        points = _empty_points(case, "no_seed", "no exact main-track IBTrACS initialization")
    else:
        try:
            with WeatherHubForecast(
                Path(task["case_dir"]), task["model"], _timestamp(case["init_time"]),
                case["forecast_hours"],
            ) as forecast:
                points = track_forecast(
                    forecast, seed_latitude=seed[0], seed_longitude=seed[1], config=config,
                )
            expected = list(range(6, case["forecast_hours"] + 1, 6))
            if [int(point["lead_hours"]) for point in points] != expected:
                raise ValueError("tracker returned an incomplete or unordered forecast time axis")
        except Exception as exc:
            points = _empty_points(case, "input_error", f"{type(exc).__name__}: {exc}")
    points = [{key: _clean_scalar(value) for key, value in point.items()} for point in points]
    init = _timestamp(case["init_time"])
    identity = {key: case[key] for key in (
        "forecast_case_id", "case_id", "storm_id", "init_time", "nominal_lead_hours",
    )}
    identity["model"] = task["model"]
    tracks = [dict(identity, **point, valid_time=_utc(init + timedelta(hours=point["lead_hours"])))
              for point in points]
    tracked = [point for point in tracks if point["status"] == "ok"]
    failed = next((point for point in tracks if point["status"] != "ok"), None)
    terminal = failed["status"] if failed else "complete"
    record = dict(
        identity, forecast_hours=case["forecast_hours"],
        seed_latitude=seed[0] if seed else None, seed_longitude=seed[1] if seed else None,
        status=terminal, reason=failed["reason"] if failed else "",
        planned_points=len(tracks), tracked_points=len(tracked),
        last_tracked_lead_hours=tracked[-1]["lead_hours"] if tracked else None,
        last_tracked_time=tracked[-1]["valid_time"] if tracked else None,
        elapsed_seconds=round(time.monotonic() - started, 6),
        peak_memory_mib=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 3),
        checkpoint=task["checkpoint"], fingerprint=task["fingerprint"], resumed=False,
    )
    return {"fingerprint": task["fingerprint"], "case": record, "tracks": tracks,
            "worker_pid": os.getpid()}


def _execute_tasks(tasks: list[dict], config: TrackerConfig, workers: int):
    """Yield results with at most two pending cases per independently spawned worker."""
    with _worker_environment():
        if workers == 1:
            for task in tasks:
                yield task, _process_case(task, config)
            return
        if not tasks:
            return
        context = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(
            max_workers=workers, mp_context=context, initializer=_single_threaded_numerics,
        ) as pool:
            remaining = iter(tasks)
            pending = {}
            for _ in range(min(len(tasks), 2 * workers)):
                task = next(remaining)
                pending[pool.submit(_process_case, task, config)] = task
            while pending:
                finished, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in finished:
                    task = pending.pop(future)
                    try:
                        result = future.result()
                    except Exception as exc:
                        failed = dict(
                            task, worker_error=f"worker failure: {type(exc).__name__}: {exc}"
                        )
                        result = _process_case(failed, config)
                    yield task, result
                    task = next(remaining, None)
                    if task is not None:
                        pending[pool.submit(_process_case, task, config)] = task


def run_tracking(
    *, root: Path, output: Path, cases_file: Path, ibtracs_file: Path,
    models: Sequence[str] | None = None, case_ids: Sequence[str] | None = None,
    workers: int = 1, resume: bool = False, config: TrackerConfig = DEFAULT_CONFIG,
) -> dict:
    """Extract selected forecasts and write stable CSVs plus per-case checkpoints.

    Resume is strict for reusable results. Failed input cases are always retried,
    including after their input files are repaired; no-seed cases are reusable.
    """
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        raise ValueError("workers must be a positive integer")
    started = time.monotonic()
    started_utc = _utc(datetime.now(timezone.utc))
    root, output = Path(root).resolve(), Path(output).resolve()
    cases_file, ibtracs_file = Path(cases_file).resolve(), Path(ibtracs_file).resolve()
    runs = discover_runs(root, models)
    cases = _load_cases(cases_file, case_ids)
    seeds = _seeds(ibtracs_file)
    package = Path(__file__).parent
    sources = {path.name: _sha256(path) for path in sorted(package.glob("*.py"))}
    provenance = {
        "schema_version": 1, "root": str(root), "parameters": asdict(config),
        "cases_file": str(cases_file), "cases_sha256": _sha256(cases_file),
        "ibtracs_file": str(ibtracs_file), "ibtracs_sha256": _sha256(ibtracs_file),
        "implementation_sha256": sources, "models": list(runs),
        "forecast_case_ids": [case["forecast_case_id"] for case in cases],
    }
    fingerprint = _digest(provenance)
    manifest_path = output / "run.json"
    existing = any((output / name).exists() for name in (
        "run.json", "tracks.csv", "cases.csv", "summary.json", "checkpoints",
    ))
    if existing and not resume:
        raise ValueError(
            f"tracking results already exist in {output}; use --resume or a new output"
        )
    if existing and not manifest_path.is_file():
        raise ValueError("existing tracking output has no run.json; choose a new output directory")
    if manifest_path.is_file() and _json(manifest_path).get("fingerprint") != fingerprint:
        raise ValueError(
            "resume fingerprint mismatch: inputs, parameters, selection, or code changed"
        )
    tasks = []
    completed = []
    for model, run in runs.items():
        for case in cases:
            forecast_id = case["forecast_case_id"]
            case_dir = run["root"] / "outputs" / run["source_model"] / "cases" / forecast_id
            seed = seeds.get((case["storm_id"], case["init_time"]))
            source_files = [case_dir / "surface.nc",
                            case_dir / ("atmospheric.nc" if model == "aurora" else "upper.nc"),
                            case_dir / "case.json", case_dir / "manifest.json"]
            details = {"run_fingerprint": fingerprint, "model": model, "case": case,
                       "seed": seed, "sources": [_file_identity(path) for path in source_files]}
            task = {"model": model, "case": case, "case_dir": str(case_dir), "seed": seed,
                    "fingerprint": _digest(details),
                    "checkpoint": str(Path("checkpoints") / model / f"{forecast_id}.json")}
            checkpoint = output / task["checkpoint"]
            if resume and checkpoint.is_file():
                saved = _json(checkpoint)
                if not any(point["status"] == "input_error" for point in saved["tracks"]):
                    if saved.get("fingerprint") != task["fingerprint"]:
                        raise ValueError(f"resume fingerprint mismatch for {model}/{forecast_id}")
                    saved["case"]["resumed"] = True
                    completed.append(saved)
                    continue
            tasks.append(task)
    # Validate every checkpoint before writing anything, preventing mixed runs.
    output.mkdir(parents=True, exist_ok=True)
    _write_json(manifest_path, {"fingerprint": fingerprint, **provenance})
    total = len(completed) + len(tasks)
    worker_memory = {}
    with ProcessMemorySampler() as memory:
        for task, result in _execute_tasks(tasks, config, workers):
            _write_json(output / task["checkpoint"], result)
            completed.append(result)
            pid = result["worker_pid"]
            worker_memory[pid] = max(worker_memory.get(pid, 0), result["case"]["peak_memory_mib"])
            done = len(completed)
            if done == total or done % 25 == 0:
                print(f"TRACK {done}/{total}: {task['model']}/{task['case']['forecast_case_id']} "
                      f"{result['case']['status']}", flush=True)
        completed.sort(key=lambda result: (result["case"]["model"],
                                           result["case"]["forecast_case_id"]))
        case_rows = [result["case"] for result in completed]
        track_rows = [point for result in completed for point in result["tracks"]]
        _write_csv(output / "tracks.csv", track_rows, TRACK_COLUMNS)
        _write_csv(output / "cases.csv", case_rows, CASE_COLUMNS)
    status_counts = dict(sorted(Counter(point["status"] for point in track_rows).items()))
    model_stats = {}
    for model in runs:
        subset = [row for row in case_rows if row["model"] == model]
        points = [row for row in track_rows if row["model"] == model]
        model_stats[model] = {
            "cases": len(subset), "planned_points": len(points),
            "seeded_cases": sum(row["seed_latitude"] is not None for row in subset),
            "case_status_counts": dict(sorted(Counter(row["status"] for row in subset).items())),
            "point_status_counts": dict(sorted(Counter(row["status"] for row in points).items())),
            "tracked_points": sum(row["tracked_points"] for row in subset),
        }
    summary = {
        "fingerprint": fingerprint, "started_utc": started_utc,
        "finished_utc": _utc(datetime.now(timezone.utc)),
        "elapsed_seconds": round(time.monotonic() - started, 6), "workers": workers,
        "cases": len(case_rows), "planned_points": len(track_rows),
        "tracked_points": status_counts.get("ok", 0),
        "tracked_fraction": status_counts.get("ok", 0) / len(track_rows),
        "seeded_cases": sum(row["seed_latitude"] is not None for row in case_rows),
        "input_error_cases": sum(row["status"] == "input_error" for row in case_rows),
        "resumed_cases": sum(row["resumed"] for row in case_rows),
        "case_status_counts": dict(sorted(Counter(row["status"] for row in case_rows).items())),
        "point_status_counts": status_counts,
        "failure_reasons": dict(sorted(Counter(row["reason"] for row in case_rows
                                               if row["reason"]).items())),
        "peak_memory_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 3),
        "memory_measurement": "parent process lifetime maximum resident set size",
        "sampled_process_tree_peak_rss_mib": memory.peak_mib,
        "process_tree_memory_measurement": "aggregate RSS sampled every 0.1 s; shared pages "
                                           "may be counted more than once",
        "worker_peak_rss_mib": {str(pid): peak for pid, peak in sorted(worker_memory.items())},
        "models": model_stats, "source": provenance,
    }
    _write_json(output / "summary.json", summary)
    return summary
