"""Regressions for the deterministic and synoptic blocks in archived ECMWF CXML."""

from datetime import datetime, timezone

import pandas as pd
import pytest

from ai_weather_eval.preprocessing.ifs_cxml import decode_deterministic
from ai_weather_eval.preprocessing.ifs_matching import match_ifs_to_cohort

INIT = datetime(2022, 10, 22, 12, tzinfo=timezone.utc)
HEADER = """<header><productionCenter>ECMWF</productionCenter>
<baseTime>2022-10-22T12:00:00</baseTime></header>"""
OBSERVATION = """<disturbance ID="test"><cycloneNumber>19E</cycloneNumber>
<fix source="synoptic"><validTime>2022-10-22T12:00:00Z</validTime>
<latitude units="deg N">17.6</latitude><longitude units="deg W">-106.2</longitude>
</fix></disturbance>"""
FORECAST = """<disturbance ID="test"><cycloneNumber>19E</cycloneNumber>
<fix hour="6" source="model"><validTime>2022-10-22T18:00:00Z</validTime>
<latitude units="deg N">18.4</latitude><longitude units="deg E">-106.7</longitude>
<cycloneData><minimumPressure><pressure units="hPa">1001.0</pressure></minimumPressure>
<maximumWind><speed units="m/s">19.6</speed></maximumWind></cycloneData>
</fix></disturbance>"""


def xml() -> str:
    genesis = FORECAST.replace(">19E<", ">70<")
    return (
        f'<cxml>{HEADER}<data origin="ecmf" type="analysis">{OBSERVATION * 2}</data>'
        f'<data origin="ecmf" type="forecast">{FORECAST * 2}{genesis}</data>'
        f'<data origin="ecmf" type="ensembleForecast" member="0">{FORECAST}</data></cxml>'
    )


@pytest.mark.parametrize("mode", ["full", "double_quotes", "single_quotes"])
def test_synoptic_anchors_model_fixes_and_duplicates(tmp_path, mode):
    path = tmp_path / ("test.xml" if mode == "full" else "test.xml.prefix")
    content = xml()
    if mode != "full":
        boundary = 'member="0">'
        content = content[:content.index(boundary) + len(boundary)]
    path.write_text(content.replace('"', "'") if mode == "single_quotes" else content)
    messages, tracks = decode_deterministic(path, source_file=path.name, init=INIT)
    assert len(messages) == len(tracks) == 2
    assert messages[0]["cxml_synoptic_position_count"] == 1
    assert messages[0]["observed_longitude"] == pytest.approx(-106.2)
    assert messages[0]["analysis_longitude"] is None
    assert messages[0]["member_number"] is None
    assert tracks[0]["longitude"] == pytest.approx(-106.7)
    assert tracks[0]["pressure_hpa"] == pytest.approx(1001)
    assert tracks[0]["max_wind_ms"] == pytest.approx(19.6)

    reference = pd.Timestamp("2022-10-22T18:00:00Z")
    events = pd.DataFrame([{
        "case_id": "test", "storm_id": "sid", "reference_time": reference,
    }])
    cases = events.assign(forecast_case_id="test-L006", init_time=INIT, nominal_lead_hours=6)
    truth = pd.DataFrame([{
        "SID": "sid", "USA_ATCF_ID": "EP192022", "ISO_TIME": INIT,
        "TRACK_TYPE": "main", "LAT": 17.6, "LON": -106.2,
    }])
    result = match_ifs_to_cohort(pd.DataFrame(messages), pd.DataFrame(tracks), truth, events, cases)
    assert list(result["storm_links"].duplicate_status) == ["unique", "equivalent_duplicate"]
    assert result["case_overlap"].iloc[0].forecast_available
    assert result["case_overlap"].iloc[0].forecast_wind_available
    assert result["case_overlap"].iloc[0].forecast_pressure_available


def test_incomplete_cxml_prefix_is_not_a_deterministic_block(tmp_path):
    path = tmp_path / "test.xml.prefix"
    path.write_text(xml().split('type="ensembleForecast"')[0])
    with pytest.raises(ValueError, match="first ensemble block"):
        decode_deterministic(path, source_file=path.name, init=INIT)


def test_cxml_rejects_wrong_valid_time(tmp_path):
    path = tmp_path / "test.xml"
    path.write_text(xml().replace("22T18:00:00Z", "22T19:00:00Z"))
    with pytest.raises(ValueError, match="validTime"):
        decode_deterministic(path, source_file=path.name, init=INIT)
