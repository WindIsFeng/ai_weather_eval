"""Build the frozen coastal-impact cohort's Weather Hub inference handoff."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
VERSION = "2022_2024_v2026-09-22"
EVENTS = ROOT / "data" / "catalogs" / f"coastal_impact_events_{VERSION}.csv"
PLANS = ROOT / "data" / "catalogs" / f"coastal_impact_forecast_cases_{VERSION}.csv"
SOURCE_MANIFEST = ROOT / "data" / "manifests" / f"coastal_catalog_{VERSION}.json"
HOURLY_CONFIG = ROOT / "configs" / "experiments" / "global_landfall_2022_2024_hourly.yaml"
OUTPUT_DIR = ROOT / "data" / "handoffs"
STEP_HOURS = 6
MAX_HOUR_OFFSET = 0.5
NOMINAL_LEADS = {24, 48, 72, 96, 120}
COMMON_MAX_HOURS = 240  # GraphCast is the shortest of the five Weather Hub limits.

SCHEDULE_COLUMNS = (
    "forecast_case_id", "case_id", "storm_id", "reference_event", "reference_time",
    "reference_latitude", "reference_longitude", "nominal_lead_hours",
    "target_init_time", "init_time", "actual_lead_hours", "cycle_offset_hours",
    "schedule_status",
)
HUB_COLUMNS = (
    "case_id", "storm_id", "init_time", "forecast_hours",
    "output_interval_hours", "name", "basin",
)
INDEX_COLUMNS = (
    "forecast_case_id", "event_case_id", "storm_id", "reference_event",
    "reference_time", "reference_latitude", "reference_longitude",
    "nominal_lead_hours", "target_init_time", "actual_lead_hours",
    "cycle_offset_hours", "init_time", "forecast_hours", "forecast_end_time",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"timezone required: {value}")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_csv(path: Path, columns: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def forecast_hours(init_time: datetime, reference_time: datetime) -> int:
    """Return the first six-hour forecast lead that reaches the observed event."""
    lead_seconds = (reference_time - init_time).total_seconds()
    if lead_seconds <= 0:
        raise ValueError("reference_time must follow init_time")
    hours = math.ceil(lead_seconds / (STEP_HOURS * 3600)) * STEP_HOURS
    if hours > COMMON_MAX_HOURS:
        raise ValueError(f"forecast horizon {hours} h exceeds common model limit")
    return hours


def nearest_utc_hour(target: datetime) -> datetime:
    """Select the nearest exact UTC hour, using the earlier hour at a tie."""
    target = target.astimezone(timezone.utc)
    earlier = target.replace(minute=0, second=0, microsecond=0)
    later = earlier + timedelta(hours=1)
    return min((earlier, later), key=lambda hour: (abs(hour - target), hour))


def build_handoff(output_dir: Path = OUTPUT_DIR) -> dict[str, object]:
    source_manifest = json.loads(SOURCE_MANIFEST.read_text(encoding="utf-8"))
    for path in (EVENTS, PLANS):
        relative = path.relative_to(ROOT).as_posix()
        expected = source_manifest["artifacts"][relative]["sha256"]
        if _sha256(path) != expected:
            raise ValueError(f"frozen source hash mismatch: {relative}")
    sampling = yaml.safe_load(HOURLY_CONFIG.read_text(encoding="utf-8"))["forecast_sampling"]
    if (
        sampling["standard_cycle_hours_utc"] != list(range(24))
        or sampling["maximum_cycle_offset_hours"] != MAX_HOUR_OFFSET
        or set(sampling["nominal_lead_hours"]) != NOMINAL_LEADS
    ):
        raise ValueError("hourly experiment configuration does not match handoff rules")

    events = _read_csv(EVENTS)
    plans = _read_csv(PLANS)
    event_by_id = {event["case_id"]: event for event in events}
    if len(events) != 114 or len(event_by_id) != 114 or len(plans) != 570:
        raise ValueError("expected 114 unique events and 570 forecast cases")
    if any(event["qc_status"] != "passed" for event in events):
        raise ValueError("handoff includes an event that did not pass QC")

    schedule_rows: list[dict[str, str]] = []
    hub_rows: list[dict[str, str]] = []
    index_rows: list[dict[str, str]] = []
    leads_by_event: dict[str, set[int]] = {event_id: set() for event_id in event_by_id}
    seen_ids: set[str] = set()
    for plan in plans:
        forecast_case_id = plan["forecast_case_id"]
        event_id = plan["case_id"]
        if forecast_case_id in seen_ids:
            raise ValueError(f"duplicate forecast case ID: {forecast_case_id}")
        seen_ids.add(forecast_case_id)
        if event_id not in event_by_id:
            raise ValueError(f"unknown event ID: {event_id}")
        event = event_by_id[event_id]
        nominal = int(plan["nominal_lead_hours"])
        if nominal not in NOMINAL_LEADS or nominal in leads_by_event[event_id]:
            raise ValueError(f"unexpected or duplicate nominal lead: {forecast_case_id}")
        leads_by_event[event_id].add(nominal)
        if forecast_case_id != f"{event_id}-L{nominal:03d}":
            raise ValueError(f"forecast case ID does not match its event and lead: {forecast_case_id}")
        if plan["storm_id"] != event["storm_id"]:
            raise ValueError(f"storm ID mismatch: {forecast_case_id}")

        reference = _utc(plan["reference_time"])
        target = _utc(plan["target_init_time"])
        init = nearest_utc_hour(target)
        if reference != _utc(event["reference_time"]) or plan["reference_event"] != event["reference_event"]:
            raise ValueError(f"reference event mismatch: {forecast_case_id}")
        if target != reference - timedelta(hours=nominal):
            raise ValueError(f"target initialization mismatch: {forecast_case_id}")
        actual_hours = (reference - init).total_seconds() / 3600
        offset_hours = (init - target).total_seconds() / 3600
        if abs(offset_hours) > MAX_HOUR_OFFSET + 1e-12:
            raise ValueError(f"hourly cycle offset exceeds 30 minutes: {forecast_case_id}")

        hours = forecast_hours(init, reference)
        schedule_rows.append({
            **plan,
            "reference_time": _iso(reference),
            "target_init_time": _iso(target),
            "init_time": _iso(init),
            "actual_lead_hours": repr(actual_hours),
            "cycle_offset_hours": repr(offset_hours),
            "schedule_status": "matched",
        })
        hub_rows.append({
            "case_id": forecast_case_id,
            "storm_id": plan["storm_id"],
            "init_time": _iso(init),
            "forecast_hours": str(hours),
            "output_interval_hours": str(STEP_HOURS),
            "name": event["name"],
            "basin": event["basin"],
        })
        index_rows.append({
            "forecast_case_id": forecast_case_id,
            "event_case_id": event_id,
            "storm_id": plan["storm_id"],
            "reference_event": plan["reference_event"],
            "reference_time": _iso(reference),
            "reference_latitude": plan["reference_latitude"],
            "reference_longitude": plan["reference_longitude"],
            "nominal_lead_hours": str(nominal),
            "target_init_time": _iso(target),
            "actual_lead_hours": repr(actual_hours),
            "cycle_offset_hours": repr(offset_hours),
            "init_time": _iso(init),
            "forecast_hours": str(hours),
            "forecast_end_time": _iso(init + timedelta(hours=hours)),
        })

    if any(leads != NOMINAL_LEADS for leads in leads_by_event.values()):
        raise ValueError("each event must have exactly five planned nominal leads")

    output_dir.mkdir(parents=True, exist_ok=True)
    schedule_path = output_dir / f"weather_hub_hourly_schedule_{VERSION}.csv"
    cases_path = output_dir / f"weather_hub_cases_{VERSION}.csv"
    index_path = output_dir / f"weather_hub_case_index_{VERSION}.csv"
    manifest_path = output_dir / f"weather_hub_manifest_{VERSION}.json"
    _write_csv(schedule_path, SCHEDULE_COLUMNS, schedule_rows)
    _write_csv(cases_path, HUB_COLUMNS, hub_rows)
    _write_csv(index_path, INDEX_COLUMNS, index_rows)
    counts = Counter(int(row["forecast_hours"]) for row in hub_rows)
    manifest: dict[str, object] = {
        "source_release_id": source_manifest["release_id"],
        "weather_hub_contract_commit": "9d12a65727a75cb87796e5749a93cbb6d6d2cd50",
        "models": ["pangu", "fengwu", "fuxi", "graphcast", "aurora"],
        "initialization_rule": "nearest exact UTC hour to reference_time minus nominal_lead_hours; ties use earlier hour",
        "maximum_init_offset_hours": MAX_HOUR_OFFSET,
        "forecast_rule": "forecast_hours = ceil((reference_time - init_time) / 6 hours) * 6 hours",
        "output_interval_hours": STEP_HOURS,
        "event_count": len(events),
        "case_count": len(hub_rows),
        "unique_init_time_count": len({row["init_time"] for row in hub_rows}),
        "init_hour_counts": dict(sorted(Counter(row["init_time"][11:13] for row in hub_rows).items())),
        "forecast_hours_counts": {str(k): counts[k] for k in sorted(counts)},
        "sources": {
            path.relative_to(ROOT).as_posix(): _sha256(path)
            for path in (EVENTS, PLANS, HOURLY_CONFIG)
        },
        "artifacts": {
            schedule_path.name: {"rows": len(schedule_rows), "sha256": _sha256(schedule_path)},
            cases_path.name: {"rows": len(hub_rows), "sha256": _sha256(cases_path)},
            index_path.name: {"rows": len(index_rows), "sha256": _sha256(index_path)},
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()
    manifest = build_handoff(args.output_dir)
    print(f"Wrote {manifest['case_count']} Weather Hub cases to {args.output_dir}")


if __name__ == "__main__":
    main()
