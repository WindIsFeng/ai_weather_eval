"""Checks for the frozen Weather Hub inference handoff."""

import csv
from datetime import datetime, timedelta

from scripts.build_weather_hub_handoff import ROOT, VERSION, build_handoff


def _rows(path):
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_handoff_covers_each_frozen_event_at_minimal_six_hour_horizon(tmp_path):
    manifest = build_handoff(tmp_path)
    hub = _rows(tmp_path / f"weather_hub_cases_{VERSION}.csv")
    index = _rows(tmp_path / f"weather_hub_case_index_{VERSION}.csv")
    source = _rows(
        ROOT / "data" / "catalogs" / f"coastal_impact_forecast_cases_{VERSION}.csv"
    )

    assert manifest["event_count"] == 114
    assert manifest["case_count"] == 570
    assert len({row["case_id"] for row in hub}) == 570
    assert [row["case_id"] for row in hub] == [row["forecast_case_id"] for row in source]
    assert [row["forecast_case_id"] for row in index] == [row["case_id"] for row in hub]
    for case, link, original in zip(hub, index, source, strict=True):
        init = _time(case["init_time"])
        observed = _time(original["reference_time"])
        hours = int(case["forecast_hours"])
        assert _time(link["reference_time"]) == observed
        assert link["event_case_id"] == original["case_id"]
        assert hours % 6 == 0
        assert init + timedelta(hours=hours - 6) < observed
        assert observed <= init + timedelta(hours=hours)
        assert _time(link["forecast_end_time"]) == init + timedelta(hours=hours)


def test_committed_handoff_is_reproducible(tmp_path):
    generated = build_handoff(tmp_path)
    committed = ROOT / "data" / "handoffs"
    for name in generated["artifacts"]:
        assert (tmp_path / name).read_bytes() == (committed / name).read_bytes()
