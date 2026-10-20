"""Failure-oriented checks for the external inference-data audit."""

import numpy as np
import pytest
from netCDF4 import Dataset

from scripts.audit_inference_data import UNITS, inspect_netcdf, products, timing_leads


@pytest.fixture
def surface_task(tmp_path):
    path = tmp_path / "surface.nc"
    with Dataset(path, "w") as ds:
        ds.createDimension("valid_time", 2)
        ds.createDimension("latitude", 721)
        ds.createDimension("longitude", 1440)
        ds.status = "complete"
        ds.forecast_id = "test-forecast"
        for name, values, units in (
            ("latitude", 90 - np.arange(721) / 4, "degrees_north"),
            ("longitude", np.arange(1440) / 4, "degrees_east"),
            ("lead_time", [6, 12], "hours"),
            ("valid_time", [6, 12], "hours since 2022-01-01 00:00:00"),
        ):
            dim = "valid_time" if name == "lead_time" else name
            var = ds.createVariable(name, "f8", (dim,))
            var.units = units
            var[:] = values
        var = ds.createVariable("forecast_reference_time", "f8")
        var.units = "hours since 2022-01-01 00:00:00"
        var[...] = 0
        for name in products("pangu")["surface.nc"]:
            var = ds.createVariable(name, "f4", ("valid_time", "latitude", "longitude"),
                                    zlib=True, chunksizes=(1, 181, 360))
            var.units = UNITS[name]
            var[:] = 1
    return {"model": "pangu", "case": {"case_id": "test-case",
            "init_time": "2022-01-01T00:00:00Z", "forecast_hours": "12"},
            "path": str(path), "forecast_id": "test-forecast", "mode": "structure"}


def test_structure_checks_exact_time_axis(surface_task):
    assert inspect_netcdf(surface_task)["errors"] == []
    with Dataset(surface_task["path"], "a") as ds:
        ds["lead_time"][1] = 6
    assert "invalid lead_time coordinate" in inspect_netcdf(surface_task)["errors"]


def test_structure_rejects_incomplete_forecast(surface_task):
    with Dataset(surface_task["path"], "a") as ds:
        ds.status = "running"
        ds["msl"].units = "hPa"
    errors = inspect_netcdf(surface_task)["errors"]
    assert "NetCDF status is not complete" in errors
    assert "msl: unexpected units" in errors


def test_sampling_detects_nonfinite_and_masked_values(surface_task):
    task = {**surface_task, "mode": "sample"}
    valid = inspect_netcdf(task)
    assert valid["errors"] == []
    assert valid["sampled_leads"] == 2
    assert valid["sample_values"] == 4 * 2 * 4
    with Dataset(surface_task["path"], "a") as ds:
        ds["msl"][:] = np.nan
        ds["u10"][:] = np.ma.masked
    invalid = inspect_netcdf(task)
    assert invalid["statistics"]["msl"]["nonfinite"] == 8
    assert invalid["statistics"]["u10"]["masked"] == 8
    assert len(invalid["errors"]) == 2


def test_structure_rejects_truncated_file(surface_task):
    with open(surface_task["path"], "r+b") as handle:
        handle.truncate(128)
    assert inspect_netcdf(surface_task)["errors"]


def test_fuxi_timing_column_is_accepted():
    assert timing_leads([{"lead_time_hours": "6"}, {"lead_time_hours": "12"}]) == [6, 12]
