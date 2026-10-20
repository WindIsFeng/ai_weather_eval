"""Integration checks for native, bounded forecast reads."""

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from netCDF4 import Dataset, date2num

from ai_weather_eval.tracking import reader
from ai_weather_eval.tracking.core import track_forecast
from ai_weather_eval.tracking.reader import WeatherHubForecast

INIT = datetime(2023, 8, 1, tzinfo=timezone.utc)
MODELS = ("graphcast", "fengwu", "fuxi", "pangu", "aurora")


def make_forecast(path, model="pangu", *, ascending=False, negative_longitudes=False):
    path.mkdir()
    latitude = np.arange(-20, 21, dtype=float)
    if not ascending:
        latitude = latitude[::-1]
    longitude = np.arange(-180, 180) if negative_longitudes else np.arange(360)
    levels = [500, 850, 1000] if model == "aurora" else [1000, 850, 500]
    for upper in (False, True):
        filename = ("atmospheric.nc" if model == "aurora" else "upper.nc") if upper else (
            "surface.nc"
        )
        with Dataset(path / filename, "w") as ds:
            ds.status = "complete"
            ds.createDimension("valid_time", 2)
            ds.createDimension("latitude", len(latitude))
            ds.createDimension("longitude", len(longitude))
            if upper:
                ds.createDimension("level", len(levels))
                level = ds.createVariable("level", "f4", ("level",))
                level.units = "hPa"
                level[:] = levels
            for name, values, units in (
                ("latitude", latitude, "degrees_north"),
                ("longitude", longitude, "degrees_east"),
            ):
                coordinate = ds.createVariable(name, "f4", (name,))
                coordinate.units = units
                coordinate[:] = values
            lead = ds.createVariable("lead_time", "i4", ("valid_time",))
            lead.units = "hours"
            lead[:] = [6, 12]
            for name, dimensions, values in (
                ("forecast_reference_time", (), INIT.replace(tzinfo=None)),
                ("valid_time", ("valid_time",), [
                    (INIT + timedelta(hours=hour)).replace(tzinfo=None) for hour in (6, 12)
                ]),
            ):
                variable = ds.createVariable(name, "f8", dimensions)
                variable.units = "hours since 1970-01-01 00:00:00"
                variable[:] = date2num(values, variable.units)
            dimensions = ("valid_time", "level", "latitude", "longitude") if upper else (
                "valid_time", "latitude", "longitude"
            )
            names = ("u", "v") if upper else (
                "msl", "10u" if model == "aurora" else "u10",
                "10v" if model == "aurora" else "v10"
            )
            tile = latitude[:, None] * 1000 + (longitude % 360)[None, :]
            for name in names:
                var = ds.createVariable(name, "f4", dimensions, fill_value=-9999)
                var.units = "Pa" if name == "msl" else "m s-1"
                if upper:
                    var[:] = np.array(levels)[None, :, None, None] + tile[None, None, :, :]
                else:
                    var[:] = tile[None, :, :]
    return path


@pytest.mark.parametrize("model", MODELS)
def test_all_model_layouts_read_native_fields_and_850_level(tmp_path, model):
    path = make_forecast(tmp_path / model, model)
    with WeatherHubForecast(path, model, INIT, 12) as forecast:
        assert forecast.lead_hours == (6, 12)
        window = forecast.read_window(1, 5, 120, 150)
        assert np.all(np.diff(window.latitude) < 0)
        assert np.all(np.diff(window.longitude) > 0)
        expected = window.latitude[:, None] * 1000 + window.longitude[None, :]
        np.testing.assert_array_equal(window.msl, expected)
        np.testing.assert_array_equal(window.u850, expected + 850)
        np.testing.assert_array_equal(window.v850, expected + 850)
        np.testing.assert_array_equal(window.u10, expected)
        np.testing.assert_array_equal(window.v10, expected)
        assert len(window.latitude) < 15
        assert len(window.longitude) < 15


@pytest.mark.parametrize("negative_longitudes", [False, True])
def test_seam_and_latitude_order_preserve_correct_values(tmp_path, negative_longitudes):
    path = make_forecast(
        tmp_path / "seam", ascending=True, negative_longitudes=negative_longitudes
    )
    center = 179 if negative_longitudes else 359
    with WeatherHubForecast(path, "pangu", INIT.isoformat(), 12) as forecast:
        window = forecast.read_window(0, 0, center, 112)
    np.testing.assert_array_equal(window.longitude, np.arange(center - 3, center + 4))
    assert np.all(np.diff(window.latitude) > 0)
    expected = window.latitude[:, None] * 1000 + (window.longitude % 360)[None, :]
    np.testing.assert_array_equal(window.msl, expected)


def test_field_reads_are_single_lead_single_level_contiguous_local_slices(tmp_path, monkeypatch):
    path = make_forecast(tmp_path / "local")
    reads = []
    original = reader._values

    def record(variable, key=slice(None)):
        if "latitude" in variable.dimensions and "longitude" in variable.dimensions:
            reads.append((variable.name, key))
        return original(variable, key)

    monkeypatch.setattr(reader, "_values", record)
    with WeatherHubForecast(path, "pangu", INIT, 12) as forecast:
        assert reads == []  # Opening files must read only coordinate vectors.
        for ds in (forecast._surface, forecast._upper):
            for name in ("msl", "u10", "v10") if ds is forecast._surface else ("u", "v"):
                assert ds.variables[name].get_var_chunk_cache()[0] == 2 * 1024 * 1024
        forecast.read_window(1, 0, 359, 112)
    assert len(reads) == 10  # Five fields, each split once across the seam.
    for name, key in reads:
        assert key[0] == 1
        if name in ("u", "v"):
            assert key[1] == 1
        assert all(isinstance(item, slice) for item in key[-2:])
        assert all(item.stop - item.start < 15 for item in key[-2:])


def test_missing_cells_become_nan_and_two_grid_cell_halo_is_retained(tmp_path):
    path = make_forecast(tmp_path / "missing")
    with Dataset(path / "surface.nc", "a") as ds:
        ds["msl"][0, 20, 0] = np.ma.masked
    with WeatherHubForecast(path, "pangu", INIT, 12) as forecast:
        window = forecast.read_window(0, 0, 0, 0.01)
        np.testing.assert_array_equal(window.latitude, [2, 1, 0, -1, -2])
        np.testing.assert_array_equal(window.longitude, [-2, -1, 0, 1, 2])
        assert np.isnan(window.msl[2, 2])


@pytest.mark.parametrize("filename,name,value,message", [
    ("surface.nc", "lead_time", [0, 6], "lead_time"),
    ("upper.nc", "lead_time", [6, 18], "lead_time"),
    ("surface.nc", "forecast_reference_time", 0, "forecast_reference_time"),
    ("upper.nc", "valid_time", [0, 6], "valid_time"),
    ("upper.nc", "latitude", np.arange(41), "grids disagree"),
    ("upper.nc", "level", [1000, 925, 500], "850 hPa"),
    ("surface.nc", "longitude", np.arange(360) ** 2, "regular"),
])
def test_bad_time_axes_grids_or_pressure_levels_are_rejected(
    tmp_path, filename, name, value, message
):
    path = make_forecast(tmp_path / "bad")
    with Dataset(path / filename, "a") as ds:
        ds[name][:] = value
    with pytest.raises(ValueError, match=message):
        with WeatherHubForecast(path, "pangu", INIT, 12):
            pass


@pytest.mark.parametrize("filename,name,attribute,value,message", [
    ("upper.nc", "u", "units", "knots", "invalid u"),
    ("surface.nc", "msl", "units", "hPa", "invalid msl"),
    ("surface.nc", "lead_time", "units", "seconds", "lead_time"),
    ("upper.nc", "level", "units", "Pa", "pressure level"),
    ("surface.nc", "valid_time", "calendar", "360_day", "calendar"),
])
def test_incompatible_units_or_calendars_are_rejected(
    tmp_path, filename, name, attribute, value, message
):
    path = make_forecast(tmp_path / "units")
    with Dataset(path / filename, "a") as ds:
        setattr(ds[name], attribute, value)
    with pytest.raises(ValueError, match=message):
        with WeatherHubForecast(path, "pangu", INIT, 12):
            pass


def test_incomplete_status_is_rejected_and_context_cleanup_is_safe(tmp_path):
    path = make_forecast(tmp_path / "incomplete")
    with Dataset(path / "upper.nc", "a") as ds:
        ds.status = "running"
    forecast = WeatherHubForecast(path, "pangu", INIT, 12)
    with pytest.raises(ValueError, match="not complete"):
        with forecast:
            pass
    assert forecast._stack is None


def test_time_units_can_differ_between_files_without_changing_valid_instants(tmp_path):
    path = make_forecast(tmp_path / "units")
    with Dataset(path / "upper.nc", "a") as ds:
        for name in ("forecast_reference_time", "valid_time"):
            ds[name][:] = ds[name][...] * 3600
            ds[name].units = "seconds since 1970-01-01 00:00:00"
    with WeatherHubForecast(path, "pangu", INIT, 12) as forecast:
        assert forecast.read_window(0, 0, 100, 100).u850.shape[0] == 5


def test_reader_requires_open_context_and_valid_window_arguments(tmp_path):
    path = make_forecast(tmp_path / "context")
    forecast = WeatherHubForecast(path, "pangu", INIT, 12)
    with pytest.raises(RuntimeError, match="context manager"):
        forecast.read_window(0, 0, 0, 100)
    with forecast:
        with pytest.raises(IndexError):
            forecast.read_window(2, 0, 0, 100)
        with pytest.raises(ValueError, match="center or radius"):
            forecast.read_window(0, 91, 0, 100)
    with pytest.raises(RuntimeError, match="context manager"):
        forecast.read_window(0, 0, 0, 100)


def test_actual_netcdf_reader_tracks_moving_cyclone_across_periodic_seam(tmp_path):
    path = make_forecast(tmp_path / "cyclone")
    with Dataset(path / "surface.nc", "a") as surface, Dataset(path / "upper.nc", "a") as upper:
        latitude, longitude = np.meshgrid(surface["latitude"][:], surface["longitude"][:],
                                         indexing="ij")
        for index, center_longitude in enumerate((359, 0)):
            dy = np.radians(latitude - 10) * 6_371_000
            dx = np.radians((longitude - center_longitude + 180) % 360 - 180)
            dx = dx * 6_371_000 * np.cos(np.radians(10))
            surface["msl"][index] = 99000 + 1e-8 * (dx**2 + dy**2)
            surface["u10"][index], surface["v10"][index] = 25, 0
            upper["u"][index] = -6e-5 * dy[None, :, :]
            upper["v"][index] = 6e-5 * dx[None, :, :]
    with WeatherHubForecast(path, "pangu", INIT, 12) as forecast:
        points = track_forecast(forecast, seed_latitude=10, seed_longitude=358)
    assert [point["status"] for point in points] == ["ok", "ok"]
    assert [point["latitude"] for point in points] == [10, 10]
    assert [point["longitude"] for point in points] == [-1, 0]
    assert all(point["msl_hpa"] == 990 for point in points)
    assert all(point["vmax10_ms"] == 25 for point in points)


@pytest.mark.parametrize("hours", [0, -6, 7, 12.5])
def test_forecast_duration_must_contain_whole_six_hour_steps(tmp_path, hours):
    with pytest.raises(ValueError, match="positive multiple"):
        WeatherHubForecast(tmp_path, "pangu", INIT, hours)
