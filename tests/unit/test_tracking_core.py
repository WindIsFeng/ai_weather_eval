"""Physical and behavioral checks for forecast-only tropical cyclone tracking."""

from dataclasses import FrozenInstanceError, replace

import numpy as np
import pytest

from ai_weather_eval.tracking.core import TrackerConfig, relative_vorticity, track_forecast
from ai_weather_eval.tracking.reader import ForecastWindow

EARTH_RADIUS_M = 6_371_000.0


class SyntheticReader:
    """Return analytic forecast snapshots without any best-track information."""

    def __init__(self, windows):
        self.windows = windows
        self.lead_hours = tuple(6 * (index + 1) for index in range(len(windows)))
        self.calls = []

    def read_window(self, index, latitude, longitude, radius_km):
        self.calls.append((index, latitude, longitude, radius_km))
        window = self.windows[index]
        if isinstance(window, Exception):
            raise window
        return window


def vortex_window(
    center_latitude=20.0,
    center_longitude=130.0,
    *,
    latitude=None,
    longitude=None,
    omega=None,
    pressure_centers=None,
    wind_scale_degrees=None,
):
    """Construct a pressure bowl and rigidly rotating, hemispheric wind field."""
    if latitude is None:
        latitude = np.arange(center_latitude - 9.0, center_latitude + 9.01, 0.25)
    if longitude is None:
        longitude = np.arange(center_longitude - 10.0, center_longitude + 10.01, 0.25)
    latitude = np.asarray(latitude, dtype=float)
    longitude = np.asarray(longitude, dtype=float)
    lat, lon = np.meshgrid(latitude, longitude, indexing="ij")
    dlon = (lon - center_longitude + 180.0) % 360.0 - 180.0
    dlat = lat - center_latitude
    if omega is None:
        omega = np.copysign(8e-5, center_latitude or 1.0)
    x = EARTH_RADIUS_M * np.deg2rad(dlon) * np.cos(np.deg2rad(center_latitude))
    y = EARTH_RADIUS_M * np.deg2rad(dlat)
    amplitude = 1.0
    if wind_scale_degrees is not None:
        amplitude = np.exp(-(dlon**2 + dlat**2) / wind_scale_degrees**2)
    u850 = -omega * y * amplitude
    v850 = omega * x * amplitude
    if pressure_centers is None:
        pressure_centers = [(center_latitude, center_longitude, 99_000.0)]
    pressure_bowls = []
    for pressure_lat, pressure_lon, minimum in pressure_centers:
        lon_offset = (lon - pressure_lon + 180.0) % 360.0 - 180.0
        pressure_bowls.append(minimum + 300.0 * ((lat - pressure_lat) ** 2 + lon_offset**2))
    msl = np.minimum.reduce(pressure_bowls)
    return ForecastWindow(
        latitude=latitude,
        longitude=longitude,
        msl=msl,
        u850=u850,
        v850=v850,
        u10=np.full_like(msl, 3.0),
        v10=np.full_like(msl, 4.0),
    )


def test_config_has_approved_defaults_and_is_immutable():
    config = TrackerConfig()
    assert config.search_radius_km == 445.0
    assert config.diagnostic_radius_km == 278.0
    assert config.vorticity_threshold_s == 5e-5
    with pytest.raises(FrozenInstanceError):
        config.search_radius_km = 500.0


@pytest.mark.parametrize("name", ["search_radius_km", "diagnostic_radius_km",
                                 "vorticity_threshold_s"])
@pytest.mark.parametrize("value", [0.0, -1.0, np.nan, np.inf])
def test_configuration_rejects_nonpositive_or_nonfinite_thresholds(name, value):
    with pytest.raises(ValueError):
        TrackerConfig(**{name: value})


def test_spherical_vorticity_includes_constant_zonal_wind_curvature():
    latitude = np.linspace(-60.0, 60.0, 241)
    longitude = np.linspace(170.0, 190.0, 41)
    u = np.full((latitude.size, longitude.size), 10.0)
    vort = relative_vorticity(latitude, longitude, u, np.zeros_like(u))
    expected = 10.0 * np.tan(np.deg2rad(latitude)) / EARTH_RADIUS_M
    np.testing.assert_allclose(vort[1:-1], np.broadcast_to(expected[1:-1, None], vort[1:-1].shape),
                               rtol=1e-4, atol=1e-10)


def test_spherical_vorticity_matches_solid_body_rotation_with_reversed_latitudes():
    latitude = np.linspace(60.0, -60.0, 241)
    longitude = np.linspace(-5.0, 5.0, 21)
    omega = 1e-4
    u = np.broadcast_to(omega * EARTH_RADIUS_M * np.cos(np.deg2rad(latitude[:, None])),
                        (latitude.size, longitude.size)).copy()
    vort = relative_vorticity(latitude, longitude, u, np.zeros_like(u))
    expected = 2 * omega * np.sin(np.deg2rad(latitude))
    np.testing.assert_allclose(vort[1:-1], np.broadcast_to(expected[1:-1, None], vort[1:-1].shape),
                               rtol=1e-4, atol=1e-10)


@pytest.mark.parametrize("hemisphere", [1.0, -1.0])
def test_tracks_moving_forecast_vortex_in_both_hemispheres(hemisphere):
    centers = [(hemisphere * 20.0, 130.0), (hemisphere * 20.5, 131.0),
               (hemisphere * 21.0, 132.0)]
    reader = SyntheticReader([vortex_window(lat, lon) for lat, lon in centers])
    points = track_forecast(reader, seed_latitude=hemisphere * 19.5, seed_longitude=129.5)
    assert [point["status"] for point in points] == ["ok"] * 3
    np.testing.assert_allclose([(p["latitude"], p["longitude"]) for p in points], centers)
    assert [point["lead_hours"] for point in points] == [6, 12, 18]
    assert all(point["msl_hpa"] == pytest.approx(990.0) for point in points)
    assert all(point["vmax10_ms"] == pytest.approx(5.0) for point in points)
    assert all(point["cyclonic_vorticity_s"] > 5e-5 for point in points)
    assert reader.calls[1][1:3] == centers[0]
    assert reader.calls[2][1:3] == centers[1]


def test_lower_pressure_candidate_without_nearby_vorticity_is_rejected():
    window = vortex_window(
        omega=1e-3,
        wind_scale_degrees=0.15,
        pressure_centers=[(20.0, 130.0, 99_500.0), (20.0, 133.75, 98_500.0)],
    )
    points = track_forecast(SyntheticReader([window]), seed_latitude=20.0, seed_longitude=130.0)
    assert points[0]["status"] == "ok"
    assert points[0]["longitude"] == 130.0
    assert points[0]["msl_hpa"] == 995.0


def test_equal_pressure_candidates_use_distance_before_coordinate_order():
    window = vortex_window(pressure_centers=[(20.0, 128.0, 99_000.0),
                                             (20.0, 130.5, 99_000.0)])
    points = track_forecast(SyntheticReader([window]), seed_latitude=20.0, seed_longitude=130.0)
    assert points[0]["longitude"] == 130.5


def test_equal_pressure_and_distance_tie_is_deterministic():
    window = vortex_window(pressure_centers=[(20.0, 129.0, 99_000.0),
                                             (20.0, 131.0, 99_000.0)])
    points = track_forecast(SyntheticReader([window]), seed_latitude=20.0, seed_longitude=130.0)
    assert points[0]["longitude"] == 129.0


@pytest.mark.parametrize("seed", [(None, None), (20.0, None), (None, 130.0)])
def test_missing_initial_position_never_reads_forecast(seed):
    reader = SyntheticReader([AssertionError("no forecast read is allowed")] * 3)
    points = track_forecast(reader, seed_latitude=seed[0], seed_longitude=seed[1])
    assert [point["status"] for point in points] == ["no_seed"] * 3
    assert not reader.calls
    assert all(point["latitude"] is None and point["longitude"] is None for point in points)


@pytest.mark.parametrize("failure", ["flat_pressure", "weak_vorticity", "opposite_rotation",
                                    "missing_pressure", "out_of_range"])
def test_forecast_failure_stops_track_without_reacquiring_later_vortex(failure):
    first = vortex_window()
    failed = first
    if failure == "flat_pressure":
        failed = replace(first, msl=np.full_like(first.msl, 100_000.0))
    elif failure == "weak_vorticity":
        failed = replace(first, u850=np.zeros_like(first.u850), v850=np.zeros_like(first.v850))
    elif failure == "opposite_rotation":
        failed = replace(first, u850=-first.u850, v850=-first.v850)
    elif failure == "missing_pressure":
        failed = replace(first, msl=np.full_like(first.msl, np.nan))
    elif failure == "out_of_range":
        # Within the requested search-plus-diagnostic window, but outside 445 km.
        failed = vortex_window(center_longitude=135.0)
    reader = SyntheticReader([first, failed, first])
    points = track_forecast(reader, seed_latitude=20.0, seed_longitude=130.0)
    assert [point["status"] for point in points] == ["ok", "track_lost", "terminated"]
    assert len(reader.calls) == 2
    for point in points[1:]:
        assert point["reason"]
        assert point["latitude"] is None
        assert point["msl_hpa"] is None


@pytest.mark.parametrize("field", ["u850", "v850", "u10", "v10"])
def test_nonfinite_diagnostic_winds_are_explicit_track_loss(field):
    window = vortex_window()
    values = getattr(window, field).copy()
    values[len(window.latitude) // 2, len(window.longitude) // 2] = np.nan
    points = track_forecast(SyntheticReader([replace(window, **{field: values})]),
                            seed_latitude=20.0, seed_longitude=130.0)
    assert points[0]["status"] == "track_lost"
    assert points[0]["reason"] == "missing_diagnostic_data"


def test_nonfinite_winds_outside_diagnostic_disk_do_not_invalidate_center():
    window = vortex_window()
    u10 = window.u10.copy()
    # Inside the disk's bounding rectangle, but over 300 km from the center.
    iy = np.flatnonzero(window.latitude == 22.25)[0]
    ix = np.flatnonzero(window.longitude == 132.25)[0]
    u10[iy, ix] = np.nan
    points = track_forecast(SyntheticReader([replace(window, u10=u10)]),
                            seed_latitude=20.0, seed_longitude=130.0)
    assert points[0]["status"] == "ok"


def test_wind_diagnostic_uses_only_the_278_km_disk():
    window = vortex_window()
    u10 = window.u10.copy()
    iy = np.flatnonzero(window.latitude == 22.25)[0]
    ix = np.flatnonzero(window.longitude == 132.25)[0]
    u10[iy, ix] = 500.0
    points = track_forecast(SyntheticReader([replace(window, u10=u10)]),
                            seed_latitude=20.0, seed_longitude=130.0)
    assert points[0]["vmax10_ms"] == pytest.approx(5.0)


def test_configuration_overrides_change_search_and_vorticity_threshold():
    window = vortex_window(center_longitude=133.0)
    reader = SyntheticReader([window])
    assert track_forecast(reader, seed_latitude=20.0, seed_longitude=130.0)[0]["status"] == "ok"
    restricted = track_forecast(SyntheticReader([window]), seed_latitude=20.0,
                                seed_longitude=130.0,
                                config=TrackerConfig(search_radius_km=250.0))
    assert restricted[0]["status"] == "track_lost"
    strict = track_forecast(SyntheticReader([window]), seed_latitude=20.0,
                           seed_longitude=130.0,
                           config=TrackerConfig(vorticity_threshold_s=1.0))
    assert strict[0]["status"] == "track_lost"


def test_equatorial_candidate_uses_seed_hemisphere_for_rotation():
    window = vortex_window(center_latitude=0.0, omega=-8e-5)
    points = track_forecast(SyntheticReader([window]), seed_latitude=-0.5, seed_longitude=130.0)
    assert points[0]["status"] == "ok"
    assert points[0]["latitude"] == 0.0
    assert points[0]["cyclonic_vorticity_s"] > 5e-5


def test_reader_error_isolated_as_input_error_and_remaining_leads_terminated():
    reader = SyntheticReader([vortex_window(), OSError("truncated forecast"), vortex_window()])
    points = track_forecast(reader, seed_latitude=20.0, seed_longitude=130.0)
    assert [point["status"] for point in points] == ["ok", "input_error", "terminated"]
    assert "truncated forecast" in points[1]["reason"]
    assert len(reader.calls) == 2


def test_antimeridian_track_has_normalized_longitudes_and_short_steps():
    longitude = np.arange(170.0, 191.0, 0.25)
    reader = SyntheticReader([vortex_window(center_longitude=lon, longitude=longitude)
                              for lon in (179.5, 180.5, 181.5)])
    points = track_forecast(reader, seed_latitude=20.0, seed_longitude=179.0)
    assert [point["status"] for point in points] == ["ok"] * 3
    np.testing.assert_allclose([point["longitude"] for point in points], [179.5, -179.5, -178.5])


def test_native_latitude_order_does_not_change_track():
    window = vortex_window()
    reversed_window = ForecastWindow(
        latitude=window.latitude[::-1], longitude=window.longitude,
        **{field: getattr(window, field)[::-1] for field in ("msl", "u850", "v850", "u10", "v10")},
    )
    forward = track_forecast(SyntheticReader([window]), seed_latitude=20.0, seed_longitude=130.0)
    backward = track_forecast(SyntheticReader([reversed_window]), seed_latitude=20.0,
                             seed_longitude=130.0)
    assert forward[0]["status"] == backward[0]["status"] == "ok"
    for field in ("latitude", "longitude", "msl_hpa", "vmax10_ms", "cyclonic_vorticity_s"):
        assert forward[0][field] == pytest.approx(backward[0][field])


def test_forecast_only_tracking_has_no_later_best_track_dependency():
    windows = [vortex_window(20.0, 130.0), vortex_window(20.5, 131.0)]
    # Attach deliberately incompatible future observations: the tracker only receives the seed.
    reader_a = SyntheticReader(windows)
    reader_b = SyntheticReader(windows)
    reader_a.best_track = [(20.0, 130.0), (21.0, 132.0)]
    reader_b.best_track = [(20.0, 130.0), (-50.0, -20.0)]
    assert track_forecast(reader_a, seed_latitude=20.0, seed_longitude=130.0) == track_forecast(
        reader_b, seed_latitude=20.0, seed_longitude=130.0,
    )
