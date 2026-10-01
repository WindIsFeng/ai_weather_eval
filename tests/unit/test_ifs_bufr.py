"""Regression checks against three compact messages from the uploaded archive."""

from pathlib import Path

import pytest

from ai_weather_eval.preprocessing.ifs_bufr import (
    decode_message,
    nullable_number,
    position,
    split_bufr_messages,
)


ec = pytest.importorskip("eccodes")
SAMPLES = Path(__file__).resolve().parents[2] / "data" / "samples" / "ifs_bufr"


def sample(name: str) -> bytes:
    return (SAMPLES / f"{name}.bin").read_bytes()


def decode(name: str):
    return decode_message(sample(name), message_id=name, source_file=f"{name}.bin")


def repack(name: str, key: str, value: int) -> bytes:
    handle = ec.codes_new_from_message(sample(name))
    try:
        ec.codes_set(handle, "unpack", 1)
        ec.codes_set(handle, key, value)
        ec.codes_set(handle, "pack", 1)
        return ec.codes_get_message(handle)
    finally:
        ec.codes_release(handle)


def test_observed_position_does_not_create_a_forecast():
    # ENRIQUE 2021-06-30 06Z contains a storm observation but no model track.
    metadata, rows = decode("no_forecast")
    assert metadata["storm_name"] == "ENRIQUE"
    assert metadata["member_number"] == 52
    assert metadata["observed_latitude"] == pytest.approx(24.1)
    assert metadata["observed_longitude"] == pytest.approx(-109.4)
    assert metadata["analysis_latitude"] is None
    assert metadata["forecast_status"] == "no_positive_forecast"
    assert metadata["positive_position_count"] == 0
    assert metadata["max_valid_lead_hours"] == 0
    assert len(rows) == 1
    assert rows[0]["lead_hours"] == 0
    for field in ("latitude", "longitude", "pressure_hpa", "max_wind_ms", "max_wind_kt"):
        assert rows[0][field] is None
    assert all(value is None for key, value in rows[0].items() if key.startswith("radius_"))


def test_missing_analysis_and_interior_lead_preserve_later_forecast():
    # CHOI-WAN 2021-06-04 18Z has no analysis, and a missing T+30 before valid T+36.
    metadata, rows = decode("missing_leads")
    by_lead = {row["lead_hours"]: row for row in rows}
    assert metadata["storm_name"] == "CHOI-WAN"
    assert metadata["observed_latitude"] == pytest.approx(23.0)
    assert metadata["observed_longitude"] == pytest.approx(122.4)
    assert metadata["analysis_latitude"] is None
    assert metadata["analysis_longitude"] is None
    assert by_lead[0]["latitude"] is None
    assert by_lead[0]["longitude"] is None
    assert list(by_lead) == [0, 6, 12, 18, 24, 30, 36]
    assert metadata["positive_position_count"] == 5
    assert metadata["max_valid_lead_hours"] == 36
    assert metadata["forecast_status"] == "forecast_available"
    assert by_lead[6]["pressure_hpa"] == pytest.approx(1000.0)
    assert by_lead[6]["max_wind_ms"] == pytest.approx(15.9)
    assert by_lead[6]["max_wind_kt"] == pytest.approx(15.9 * 3600 / 1852)
    assert by_lead[6]["radius_18ms_se_km"] == 0.0
    assert by_lead[12]["radius_18ms_se_km"] == pytest.approx(35.2)
    assert by_lead[12]["radius_18ms_sw_km"] == pytest.approx(53.7)
    missing = by_lead[30]
    for field in ("latitude", "longitude", "pressure_hpa", "max_wind_ms", "max_wind_kt"):
        assert missing[field] is None
    assert all(value is None for key, value in missing.items() if key.startswith("radius_"))
    assert by_lead[36]["latitude"] == pytest.approx(30.4)
    assert by_lead[36]["valid_time"] == "2021-06-06T06:00:00+00:00"


def test_analysis_units_and_all_three_wind_radius_quadrants():
    # IDA 2021-08-29 18Z distinguishes the observation, analysis, and maximum-wind location.
    metadata, rows = decode("strong_storm")
    analysis = rows[0]
    assert metadata["observed_latitude"] == pytest.approx(29.2)
    assert metadata["analysis_latitude"] == pytest.approx(29.1)
    assert analysis["latitude"] == pytest.approx(29.1)
    assert analysis["longitude"] == pytest.approx(-90.3)
    assert analysis["pressure_hpa"] == pytest.approx(956.0)
    assert analysis["max_wind_ms"] == pytest.approx(44.2)
    assert analysis["max_wind_kt"] == pytest.approx(44.2 * 3600 / 1852)
    expected = {
        18: (192.6, 168.5, 148.2, 135.2),
        26: (118.5, 101.9, 70.4, 53.7),
        33: (72.2, 72.2, 40.7, 0.0),
    }
    for threshold, radii in expected.items():
        for quadrant, radius in zip(("ne", "se", "sw", "nw"), radii, strict=True):
            assert analysis[f"radius_{threshold}ms_{quadrant}_km"] == pytest.approx(radius)
    assert [row["lead_hours"] for row in rows] == [0, 6, 12, 18, 24, 30]
    assert metadata["max_encoded_lead_hours"] == 30
    assert metadata["encoded_period_count"] == 6
    assert rows[-1]["valid_time"] == "2021-08-31T00:00:00+00:00"


def test_concatenated_messages_are_split_without_loss():
    first, second = sample("no_forecast"), sample("strong_storm")
    assert list(split_bufr_messages(first + second)) == [first, second]


@pytest.mark.parametrize(
    "data,reason",
    [
        (b"", "Empty"),
        (b"abcdefgh", "Invalid BUFR header"),
        (b"BUFR", "Invalid BUFR header"),
        (b"BUFR\x00\x00\x0b\x04xxx", "Truncated BUFR message"),
        (sample("strong_storm")[:-1], "Truncated BUFR message"),
        (sample("strong_storm")[:-4] + b"0000", "Missing BUFR terminator"),
        (sample("strong_storm") + b"trailing", "Invalid BUFR header"),
    ],
)
def test_malformed_or_truncated_input_is_rejected(data, reason):
    with pytest.raises(ValueError, match=reason):
        list(split_bufr_messages(data))


@pytest.mark.parametrize("value", [-1e100, 1e100, 2147483647, float("nan"), float("inf")])
def test_missing_sentinels_are_null_before_unit_conversion(value):
    assert nullable_number(value) is None


@pytest.mark.parametrize("longitude,expected", [(182.0, -178.0), (330.0, -30.0), (-90.3, -90.3)])
def test_longitude_conventions_normalize_to_signed_degrees(longitude, expected):
    assert position(29.1, longitude) == pytest.approx((29.1, expected))


@pytest.mark.parametrize(
    "key,value,reason",
    [
        ("#1#ensembleMemberNumber", 51, "Expected HRES member 52"),
        ("#1#windSpeedThreshold", 17, "Unexpected radius thresholds"),
        ("#2#timePeriod", 6, "Unexpected forecast periods"),
        ("#1#meteorologicalAttributeSignificance", 3, "Unexpected observed/analysis significance"),
        ("#1#bearingOrAzimuth", 90, "Unexpected wind-radius quadrant bearings"),
    ],
)
def test_unknown_product_semantics_are_rejected(key, value, reason):
    data = repack("strong_storm", key, value)
    with pytest.raises(ValueError, match=reason):
        decode_message(data, message_id="changed", source_file="changed.bin")
