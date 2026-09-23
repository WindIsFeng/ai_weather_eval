"""Design forecast cases around observed coastal-event reference times."""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd


REQUIRED_CASE_COLUMNS = (
    "case_id",
    "storm_id",
    "reference_event",
    "reference_time",
    "latitude",
    "longitude",
)
SCHEDULE_COLUMNS = (
    "forecast_case_id",
    "case_id",
    "storm_id",
    "reference_event",
    "reference_time",
    "reference_latitude",
    "reference_longitude",
    "nominal_lead_hours",
    "target_init_time",
    "init_time",
    "actual_lead_hours",
    "cycle_offset_hours",
    "schedule_status",
)


def build_forecast_case_schedule(
    cases: pd.DataFrame,
    *,
    nominal_lead_hours: Sequence[int],
    standard_cycle_hours_utc: Sequence[int],
    maximum_cycle_offset_hours: float,
) -> pd.DataFrame:
    """Return one auditable initialization plan per observed event and lead.

    The earlier cycle wins when two cycles are equally close to the target.
    Missing cycles remain in the table with an explicit status.
    """

    missing = sorted(set(REQUIRED_CASE_COLUMNS).difference(cases.columns))
    if missing:
        raise ValueError("Missing case column(s): " + ", ".join(missing))
    if cases["case_id"].duplicated().any():
        raise ValueError("case_id must be unique in the input case table")

    leads = tuple(nominal_lead_hours)
    cycles = tuple(standard_cycle_hours_utc)
    if not leads or leads != tuple(sorted(set(leads))) or any(
        not isinstance(lead, int) or lead <= 0 for lead in leads
    ):
        raise ValueError("nominal_lead_hours must contain unique positive values in order")
    if not cycles or cycles != tuple(sorted(set(cycles))) or any(
        not isinstance(hour, int) or not 0 <= hour < 24 for hour in cycles
    ):
        raise ValueError("standard_cycle_hours_utc must contain unique UTC hours in order")
    if maximum_cycle_offset_hours < 0:
        raise ValueError("maximum_cycle_offset_hours must be nonnegative")

    reference_times = pd.to_datetime(
        cases["reference_time"], utc=True, errors="raise", format="mixed"
    )
    if reference_times.isna().any():
        raise ValueError("reference_time must be present for every case")

    records: list[dict[str, object]] = []
    for case, reference_time in zip(cases.to_dict("records"), reference_times, strict=True):
        if case["reference_event"] not in {"landfall", "coastal_approach"}:
            raise ValueError(f"Invalid reference_event for {case['case_id']}")
        for lead in leads:
            target = reference_time - pd.Timedelta(hours=lead)
            midnight = target.normalize()
            candidates = [
                midnight + pd.Timedelta(days=day, hours=hour)
                for day in (-1, 0, 1)
                for hour in cycles
            ]
            init_time = min(candidates, key=lambda cycle: (abs(cycle - target), cycle))
            offset_hours = (init_time - target).total_seconds() / 3600.0
            matched = abs(offset_hours) <= maximum_cycle_offset_hours
            records.append(
                {
                    "forecast_case_id": f"{case['case_id']}-L{lead:03d}",
                    "case_id": case["case_id"],
                    "storm_id": case["storm_id"],
                    "reference_event": case["reference_event"],
                    "reference_time": reference_time,
                    "reference_latitude": case["latitude"],
                    "reference_longitude": case["longitude"],
                    "nominal_lead_hours": lead,
                    "target_init_time": target,
                    "init_time": init_time if matched else pd.NaT,
                    "actual_lead_hours": (
                        (reference_time - init_time).total_seconds() / 3600.0
                        if matched
                        else float("nan")
                    ),
                    "cycle_offset_hours": offset_hours if matched else float("nan"),
                    "schedule_status": "matched" if matched else "no_cycle_within_tolerance",
                }
            )
    return pd.DataFrame.from_records(records, columns=SCHEDULE_COLUMNS)
