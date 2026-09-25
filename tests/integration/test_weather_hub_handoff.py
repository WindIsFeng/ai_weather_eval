"""Checks for the frozen Weather Hub inference handoff."""

import csv
from datetime import datetime, timedelta

import pandas as pd

from ai_weather_eval.preprocessing.forecast_matching import build_forecast_case_schedule
from scripts.build_weather_hub_handoff import ROOT, VERSION, build_handoff, nearest_utc_hour


def _rows(path):
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_handoff_covers_each_frozen_event_at_minimal_six_hour_horizon(tmp_path):
    manifest = build_handoff(tmp_path)
    schedule = _rows(tmp_path / f"weather_hub_hourly_schedule_{VERSION}.csv")
    hub = _rows(tmp_path / f"weather_hub_cases_{VERSION}.csv")
    index = _rows(tmp_path / f"weather_hub_case_index_{VERSION}.csv")
    source = _rows(
        ROOT / "data" / "catalogs" / f"coastal_impact_forecast_cases_{VERSION}.csv"
    )

    assert manifest["event_count"] == 114
    assert manifest["case_count"] == 570
    assert len({row["case_id"] for row in hub}) == 570
    assert hub[0]["init_time"] == "2022-01-31T15:00:00Z"
    assert hub[0]["forecast_hours"] == "24"
    assert [row["case_id"] for row in hub] == [row["forecast_case_id"] for row in source]
    assert [row["forecast_case_id"] for row in index] == [row["case_id"] for row in hub]
    assert [row["forecast_case_id"] for row in schedule] == [row["case_id"] for row in hub]
    for case, link, plan, original in zip(hub, index, schedule, source, strict=True):
        init = _time(case["init_time"])
        observed = _time(original["reference_time"])
        hours = int(case["forecast_hours"])
        assert _time(link["reference_time"]) == observed
        assert link["event_case_id"] == original["case_id"]
        assert _time(plan["init_time"]) == init
        assert plan["schedule_status"] == "matched"
        assert init.minute == init.second == init.microsecond == 0
        assert abs(float(plan["cycle_offset_hours"])) <= 0.5
        assert init == nearest_utc_hour(_time(plan["target_init_time"]))
        assert float(link["actual_lead_hours"]) == float(plan["actual_lead_hours"])
        assert hours % 6 == 0
        assert init + timedelta(hours=hours - 6) < observed
        assert observed <= init + timedelta(hours=hours)
        assert _time(link["forecast_end_time"]) == init + timedelta(hours=hours)


def test_hourly_schedule_agrees_with_project_planner(tmp_path):
    build_handoff(tmp_path)
    generated = _rows(tmp_path / f"weather_hub_hourly_schedule_{VERSION}.csv")
    events = pd.read_csv(
        ROOT / "data" / "catalogs" / f"coastal_impact_events_{VERSION}.csv",
        keep_default_na=False,
        na_values=[""],
    )
    expected = build_forecast_case_schedule(
        events,
        nominal_lead_hours=[24, 48, 72, 96, 120],
        standard_cycle_hours_utc=list(range(24)),
        maximum_cycle_offset_hours=0.5,
    )
    by_id = expected.set_index("forecast_case_id")
    for row in generated:
        forecast_case_id = row["forecast_case_id"]
        assert _time(row["init_time"]) == by_id.loc[forecast_case_id, "init_time"]
        assert abs(
            float(row["actual_lead_hours"])
            - by_id.loc[forecast_case_id, "actual_lead_hours"]
        ) < 1e-9


def test_nearest_hour_breaks_half_hour_tie_earlier():
    target = _time("2022-01-31T15:30:00Z")
    assert nearest_utc_hour(target) == _time("2022-01-31T15:00:00Z")


def test_committed_handoff_is_reproducible(tmp_path):
    generated = build_handoff(tmp_path)
    committed = ROOT / "data" / "handoffs"
    for name in generated["artifacts"]:
        assert (tmp_path / name).read_bytes() == (committed / name).read_bytes()
