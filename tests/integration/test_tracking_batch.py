"""End-to-end extraction and safe resumption against small five-model forecasts."""

import csv
import json
from datetime import datetime, timedelta

import numpy as np
import pytest
from netCDF4 import Dataset, date2num

from ai_weather_eval.catalog.ibtracs import REQUIRED_COLUMNS
from ai_weather_eval.tracking import batch
from ai_weather_eval.tracking.core import TrackerConfig

INIT = "2024-08-01T00:00:00Z"
SEEDED_ID = "2024001N20110-CE01-L024"
UNSEEDED_ID = "2024002S10100-CE01-L048"
STORM = "2024001N20110"


def _csv(path):
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _write_truth(path, future_latitude=22):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=REQUIRED_COLUMNS)
        writer.writeheader()
        writer.writerow(dict.fromkeys(REQUIRED_COLUMNS, ""))  # IBTrACS units row.
        for sid, stamp, lat, lon, track_type in (
            (STORM, INIT, 20, 110, "main"),
            (STORM, "2024-08-01T06:00:00Z", future_latitude, 120, "main"),
            ("2024002S10100", INIT, -10, 100, "provisional"),
        ):
            row = dict.fromkeys(REQUIRED_COLUMNS, "")
            row.update(SID=sid, BASIN="WP", NAME="TEST", ISO_TIME=stamp, NATURE="TS",
                       LAT=lat, LON=lon, TRACK_TYPE=track_type, USA_STATUS="TS", USA_WIND=40)
            writer.writerow(row)


def _forecast(case_dir, model):
    case_dir.mkdir(parents=True)
    lat = np.arange(-5, 46, dtype=float)
    lon = np.arange(80, 141, dtype=float)
    x, y = np.meshgrid(lon, lat)
    init = datetime(2024, 8, 1)
    times = [init + timedelta(hours=lead) for lead in (6, 12)]
    for upper in (False, True):
        path = case_dir / ("atmospheric.nc" if model == "aurora" else "upper.nc")
        if not upper:
            path = case_dir / "surface.nc"
        with Dataset(path, "w") as dataset:
            dataset.status = "complete"
            for name, size in (("valid_time", 2), ("latitude", len(lat)), ("longitude", len(lon))):
                dataset.createDimension(name, size)
            for name, values, units in (
                ("latitude", lat, "degrees_north"), ("longitude", lon, "degrees_east"),
                ("lead_time", [6, 12], "hours"),
            ):
                dimension = "valid_time" if name == "lead_time" else name
                var = dataset.createVariable(name, "f8", (dimension,))
                var.units = units
                var[:] = values
            units = "hours since 2024-08-01 00:00:00"
            valid = dataset.createVariable("valid_time", "f8", ("valid_time",))
            valid.units = units
            valid[:] = date2num(times, units)
            reference = dataset.createVariable("forecast_reference_time", "f8")
            reference.units = units
            reference.assignValue(date2num(init, units))
            if upper:
                dataset.createDimension("level", 2)
                levels = dataset.createVariable("level", "f8", ("level",))
                levels.units = "hPa"
                levels[:] = [850, 1000] if model == "aurora" else [1000, 850]
                for name, field in (
                    ("u", -4e-5 * np.deg2rad(y - 20) * 6_371_000),
                    ("v", 4e-5 * np.deg2rad(x - 110) * 6_371_000 * np.cos(np.deg2rad(y))),
                ):
                    var = dataset.createVariable(
                        name, "f8", ("valid_time", "level", "latitude", "longitude")
                    )
                    var.units = "m s-1"
                    var[:] = np.broadcast_to(field, (2, 2, *field.shape))
            else:
                pressure = np.asarray([
                    98_000 + 50 * ((y - center) ** 2 + (x - (90 + center)) ** 2)
                    for center in (21, 22)
                ])
                for name, values, field_units in (
                    ("msl", pressure, "Pa"),
                    ("10u" if model == "aurora" else "u10", np.full_like(pressure, 20), "m s-1"),
                    ("10v" if model == "aurora" else "v10", np.zeros_like(pressure), "m s-1"),
                ):
                    var = dataset.createVariable(
                        name, "f8", ("valid_time", "latitude", "longitude")
                    )
                    var.units = field_units
                    var[:] = values


@pytest.fixture
def forecasts(tmp_path):
    root = tmp_path / "forecasts"
    for model in batch.MODELS:
        run = root / f"run_{model}"
        run.mkdir(parents=True)
        (run / "job.json").write_text(json.dumps({"model": model}))
        _forecast(run / "outputs" / model / "cases" / SEEDED_ID, model)
    cases_file = tmp_path / "cases.csv"
    with cases_file.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=(
            "case_id", "storm_id", "init_time", "forecast_hours", "output_interval_hours",
        ))
        writer.writeheader()
        for case_id, sid, horizon in ((SEEDED_ID, STORM, 12),
                                     (UNSEEDED_ID, "2024002S10100", 6)):
            writer.writerow(dict(case_id=case_id, storm_id=sid, init_time=INIT,
                                 forecast_hours=horizon, output_interval_hours=6))
    ibtracs_file = tmp_path / "ibtracs.csv"
    _write_truth(ibtracs_file)
    return dict(root=root, output=tmp_path / "tracks", cases_file=cases_file,
                ibtracs_file=ibtracs_file)


def test_serial_tracks_all_five_native_formats_and_preserves_missing_seed(forecasts):
    summary = batch.run_tracking(**forecasts)
    assert summary["cases"] == 10
    assert summary["planned_points"] == 15
    assert summary["tracked_points"] == 10
    assert summary["point_status_counts"] == {"no_seed": 5, "ok": 10}
    rows = _csv(forecasts["output"] / "tracks.csv")
    keys = [(row["model"], row["forecast_case_id"], int(row["lead_hours"])) for row in rows]
    assert keys == sorted(keys)
    tracked = [row for row in rows if row["status"] == "ok"]
    assert {row["case_id"] for row in tracked} == {"2024001N20110-CE01"}
    assert {row["nominal_lead_hours"] for row in tracked} == {"24"}
    assert {row["init_time"] for row in tracked} == {INIT}
    assert {row["valid_time"] for row in tracked} == {
        "2024-08-01T06:00:00Z", "2024-08-01T12:00:00Z",
    }
    assert [float(row["latitude"]) for row in tracked] == [21, 22] * 5
    assert [float(row["longitude"]) for row in tracked] == [111, 112] * 5
    assert all(float(row["msl_hpa"]) == 980 for row in tracked)
    assert summary["source"]["parameters"] == {
        "search_radius_km": 445.0, "diagnostic_radius_km": 278.0,
        "vorticity_threshold_s": 5e-5,
    }


def test_no_seed_does_not_open_forecast_files(forecasts, monkeypatch):
    def forbidden_open(*args, **kwargs):
        pytest.fail("a no-seed case opened a forecast")
    monkeypatch.setattr(batch, "WeatherHubForecast", forbidden_open)
    summary = batch.run_tracking(**forecasts, models=["graphcast"], case_ids=[UNSEEDED_ID])
    assert summary["case_status_counts"] == {"no_seed": 1}


def test_one_invalid_input_is_isolated_and_keeps_all_planned_points(forecasts):
    broken = forecasts["root"] / "run_fuxi" / "outputs" / "fuxi" / "cases" / SEEDED_ID
    (broken / "upper.nc").unlink()
    summary = batch.run_tracking(**forecasts)
    assert summary["planned_points"] == 15
    assert summary["point_status_counts"] == {
        "input_error": 1, "no_seed": 5, "ok": 8, "terminated": 1,
    }
    cases = _csv(forecasts["output"] / "cases.csv")
    error = next(row for row in cases if row["model"] == "fuxi" and row["status"] == "input_error")
    assert error["tracked_points"] == "0"
    assert "FileNotFoundError" in error["reason"]


def test_resume_reuses_success_and_retries_transient_input_errors(forecasts, monkeypatch):
    original = batch.WeatherHubForecast
    def temporary_failure(case_dir, *args):
        if "graphcast" in str(case_dir):
            raise OSError("temporary read error")
        return original(case_dir, *args)
    monkeypatch.setattr(batch, "WeatherHubForecast", temporary_failure)
    first = batch.run_tracking(**forecasts)
    assert first["case_status_counts"]["input_error"] == 1
    monkeypatch.setattr(batch, "WeatherHubForecast", original)
    second = batch.run_tracking(**forecasts, resume=True)
    assert second["resumed_cases"] == 9
    assert second["tracked_points"] == 10
    third = batch.run_tracking(**forecasts, resume=True)
    assert third["resumed_cases"] == 10


def test_resume_rejects_changed_parameters_or_source(forecasts):
    batch.run_tracking(**forecasts)
    with pytest.raises(ValueError, match="already exist"):
        batch.run_tracking(**forecasts)
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        batch.run_tracking(**forecasts, resume=True, config=TrackerConfig(search_radius_km=400))
    source = (forecasts["root"] / "run_fuxi" / "outputs" / "fuxi" / "cases"
              / SEEDED_ID / "surface.nc")
    source.touch()
    with pytest.raises(ValueError, match="fingerprint mismatch for fuxi"):
        batch.run_tracking(**forecasts, resume=True)


def test_future_best_track_positions_cannot_change_prediction(forecasts):
    batch.run_tracking(**forecasts)
    before = (forecasts["output"] / "tracks.csv").read_bytes()
    _write_truth(forecasts["ibtracs_file"], future_latitude=-70)
    forecasts["output"] = forecasts["output"].parent / "changed_future"
    batch.run_tracking(**forecasts)
    assert (forecasts["output"] / "tracks.csv").read_bytes() == before


def test_model_alias_and_selection_validation(forecasts):
    original = forecasts["root"] / "run_pangu"
    (original / "job.json").write_text(json.dumps({"model": "pangu_weather"}))
    (original / "outputs" / "pangu").rename(original / "outputs" / "pangu_weather")
    summary = batch.run_tracking(**forecasts, models=["pangu_weather"], case_ids=[SEEDED_ID])
    assert summary["models"]["pangu"]["tracked_points"] == 2
    with pytest.raises(ValueError, match="unknown forecast case"):
        batch.run_tracking(**forecasts, case_ids=["does-not-exist"])


@pytest.mark.parametrize("workers", [0, -1, True, 1.5])
def test_invalid_worker_count(forecasts, workers):
    with pytest.raises(ValueError, match="positive integer"):
        batch.run_tracking(**forecasts, workers=workers)


def test_spawned_workers_match_serial_and_can_resume_with_different_worker_count(forecasts):
    serial = batch.run_tracking(**forecasts)
    expected = (forecasts["output"] / "tracks.csv").read_bytes()
    forecasts["output"] = forecasts["output"].parent / "parallel"
    parallel = batch.run_tracking(**forecasts, workers=2)
    assert (forecasts["output"] / "tracks.csv").read_bytes() == expected
    assert parallel["case_status_counts"] == serial["case_status_counts"]
    assert parallel["sampled_process_tree_peak_rss_mib"] > 0
    assert parallel["worker_peak_rss_mib"]
    resumed = batch.run_tracking(**forecasts, workers=1, resume=True)
    assert resumed["resumed_cases"] == 10
    assert (forecasts["output"] / "tracks.csv").read_bytes() == expected


def test_parallel_worker_input_failure_keeps_other_cases_and_checkpointing(forecasts):
    broken = forecasts["root"] / "run_fuxi" / "outputs" / "fuxi" / "cases" / SEEDED_ID
    (broken / "upper.nc").unlink()
    summary = batch.run_tracking(**forecasts, workers=2)
    assert summary["input_error_cases"] == 1
    assert summary["tracked_points"] == 8
    assert len(list((forecasts["output"] / "checkpoints").glob("*/*.json"))) == 10


def test_repaired_input_error_is_retried_with_new_file_fingerprint(forecasts):
    path = (forecasts["root"] / "run_fuxi" / "outputs" / "fuxi" / "cases"
            / SEEDED_ID / "upper.nc")
    original = path.read_bytes()
    path.unlink()
    first = batch.run_tracking(**forecasts)
    assert first["input_error_cases"] == 1
    path.write_bytes(original)
    repaired = batch.run_tracking(**forecasts, resume=True, workers=2)
    assert repaired["input_error_cases"] == 0
    assert repaired["tracked_points"] == 10
    assert repaired["resumed_cases"] == 9
