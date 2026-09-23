"""Command-line interface for the evaluation workflows."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Sequence

import pandas as pd
import yaml

from ai_weather_eval import __version__
from ai_weather_eval.catalog.coastal_impact import (
    CoastalImpactSelection,
    build_coastal_impact_catalog,
)
from ai_weather_eval.catalog.coastline import CoastlineIndex
from ai_weather_eval.catalog.ibtracs import (
    ibtracs_quality_summary,
    load_ibtracs_csv,
    select_track_types,
)
from ai_weather_eval.config import ConfigError, load_yaml, validate_experiment
from ai_weather_eval.preprocessing.forecast_matching import build_forecast_case_schedule


def _add_workflow_command(subparsers: argparse._SubParsersAction, name: str, help_text: str) -> None:
    parser = subparsers.add_parser(name, help=help_text)
    parser.add_argument("--config", type=Path, required=True, help="Experiment YAML file")
    parser.set_defaults(handler=_pending_workflow)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="weather-eval", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    config_parser = subparsers.add_parser("config", help="Inspect project configuration")
    config_subparsers = config_parser.add_subparsers(dest="config_command", required=True)
    check_parser = config_subparsers.add_parser("check", help="Validate an experiment YAML file")
    check_parser.add_argument("--config", type=Path, required=True)
    check_parser.add_argument("--show", action="store_true", help="Print resolved configuration")
    check_parser.set_defaults(handler=_check_config)

    catalog_parser = subparsers.add_parser("catalog", help="Build and inspect case catalogs")
    catalog_subparsers = catalog_parser.add_subparsers(dest="catalog_command", required=True)
    catalog_build = catalog_subparsers.add_parser(
        "build", help="Build the coastal-impact case catalog"
    )
    catalog_build.add_argument("--config", type=Path, required=True)
    catalog_build.add_argument(
        "--output-dir",
        type=Path,
        help="New output directory; defaults to the external interim case-catalog store",
    )
    catalog_build.set_defaults(handler=_build_catalog)
    catalog_plan = catalog_subparsers.add_parser(
        "plan", help="Design event-relative forecast cases from a coastal catalog"
    )
    catalog_plan.add_argument("--config", type=Path, required=True)
    catalog_plan.add_argument("--cases-file", type=Path, required=True)
    catalog_plan.add_argument("--output", type=Path, required=True)
    catalog_plan.set_defaults(handler=_plan_forecast_cases)

    _add_workflow_command(subparsers, "ingest", "Normalize model forecast files")
    _add_workflow_command(subparsers, "verify", "Compute case-level verification metrics")
    _add_workflow_command(subparsers, "summarize", "Aggregate metrics and uncertainty")
    _add_workflow_command(subparsers, "plot", "Generate configured figures")
    _add_workflow_command(subparsers, "run", "Run the complete evaluation pipeline")
    return parser


def _check_config(args: argparse.Namespace) -> int:
    config = load_yaml(args.config)
    validate_experiment(config)
    print(f"Configuration is valid: {args.config}")
    if args.show:
        print(json.dumps(config, indent=2, ensure_ascii=False, default=str))
    return 0


def _pending_workflow(args: argparse.Namespace) -> int:
    config = load_yaml(args.config)
    validate_experiment(config)
    print(
        f"Workflow '{args.command}' is scaffolded but not implemented yet. "
        "The experiment configuration was validated successfully."
    )
    return 0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _selection_from_config(config: dict[str, object]) -> CoastalImpactSelection:
    period = config["period"]
    case_selection = config["case_selection"]
    return CoastalImpactSelection(
        start=pd.Timestamp(period["start"]),
        end=pd.Timestamp(period["end"]),
        coastal_peak_threshold_kt=float(case_selection["coastal_peak_threshold_kt"]),
        missing_radius_distance_km=float(case_selection["missing_radius_distance_km"]),
        episode_gap_hours=float(case_selection["episode_gap_hours"]),
        intensity_window_hours=float(case_selection["intensity_window_hours"]),
        maximum_track_gap_hours=float(case_selection["maximum_track_gap_hours"]),
        maximum_track_sample_spacing_km=float(
            case_selection["maximum_track_sample_spacing_km"]
        ),
        maximum_time_step_hours=float(case_selection["maximum_time_step_hours"]),
        distance_candidate_samples=int(case_selection["distance_candidate_samples"]),
        crossing_candidate_samples=int(case_selection["crossing_candidate_samples"]),
        primary_tiers=tuple(case_selection["primary_tiers"]),
    )


def _catalog_summary(cases: pd.DataFrame, points: pd.DataFrame) -> dict[str, object]:
    if cases.empty:
        return {"case_count": 0, "primary_case_count": 0, "storm_count": 0}

    def counts(column: str) -> dict[str, int]:
        return {
            str(key): int(value)
            for key, value in cases[column].value_counts(dropna=False).sort_index().items()
        }

    return {
        "case_count": int(len(cases)),
        "primary_case_count": int(cases["primary_sample"].sum()),
        "storm_count": int(cases["storm_id"].nunique()),
        "primary_storm_count": int(
            cases.loc[cases["primary_sample"], "storm_id"].nunique()
        ),
        "provisional_case_count": int(cases["provisional_track"].sum()),
        "provisional_primary_case_count": int(
            (cases["provisional_track"] & cases["primary_sample"]).sum()
        ),
        "final_main_case_count": int((~cases["provisional_track"]).sum()),
        "final_main_primary_case_count": int(
            ((~cases["provisional_track"]) & cases["primary_sample"]).sum()
        ),
        "landfall_case_count": int(cases["landfall_crossing"].sum()),
        "sea_only_case_count": int((~cases["landfall_crossing"]).sum()),
        "exit_associated_case_count": int(cases["coastline_exit"].sum()),
        "primary_landfall_case_count": int(
            (cases["primary_sample"] & cases["landfall_crossing"]).sum()
        ),
        "primary_sea_only_case_count": int(
            (cases["primary_sample"] & ~cases["landfall_crossing"]).sum()
        ),
        "tier_counts": counts("tier"),
        "reference_event_counts": counts("reference_event"),
        "basin_counts": counts("basin"),
        "selection_method_counts": counts("selection_method"),
        "dense_track_point_count": int(len(points)),
        "median_minimum_coast_distance_km": float(
            cases["minimum_coast_distance_km"].median()
        ),
        "median_r34_coverage_fraction": float(cases["r34_coverage_fraction"].median()),
    }


def _storm_catalog(cases: pd.DataFrame) -> pd.DataFrame:
    if cases.empty:
        return pd.DataFrame()
    tier_rank = {"A": 0, "B": 1, "C": 2, "D": 3}
    records: list[dict[str, object]] = []
    for storm_id, storm_cases in cases.groupby("storm_id", sort=True):
        highest_tier = min(storm_cases["tier"], key=lambda tier: tier_rank[str(tier)])
        records.append(
            {
                "storm_id": storm_id,
                "name": storm_cases["name"].iloc[0],
                "basins": "+".join(sorted(storm_cases["basin"].dropna().astype(str).unique())),
                "episode_count": int(len(storm_cases)),
                "primary_episode_count": int(storm_cases["primary_sample"].sum()),
                "highest_tier": highest_tier,
                "primary_sample": bool(storm_cases["primary_sample"].any()),
                "first_coastal_contact": storm_cases["episode_start"].min(),
                "last_coastal_contact": storm_cases["episode_end"].max(),
                "minimum_coast_distance_km": float(
                    storm_cases["minimum_coast_distance_km"].min()
                ),
                "lifetime_vmax_kt": float(storm_cases["lifetime_vmax_kt"].max()),
                "maximum_coastal_episode_vmax_kt": float(
                    storm_cases["coastal_episode_vmax_kt"].max()
                ),
                "any_coastline_crossing": bool(storm_cases["coastline_crossing"].any()),
                "any_coastline_exit": bool(storm_cases["coastline_exit"].any()),
                "provisional_track": bool(storm_cases["provisional_track"].any()),
                "qc_status": "pending",
            }
        )
    return pd.DataFrame(records).sort_values(
        ["first_coastal_contact", "storm_id"], kind="stable"
    ).reset_index(drop=True)


def _build_catalog(args: argparse.Namespace) -> int:
    config = load_yaml(args.config)
    validate_experiment(config)
    project_root = _project_root()
    ibtracs_config = load_yaml(project_root / "configs/datasets/ibtracs.yaml")
    coastline_config = load_yaml(project_root / "configs/datasets/coastline.yaml")

    ibtracs_path = Path(ibtracs_config["path"])
    coastline_path = Path(coastline_config["path"])
    expected_ibtracs_hash = ibtracs_config.get("sha256")
    actual_ibtracs_hash = _sha256(ibtracs_path)
    if expected_ibtracs_hash and actual_ibtracs_hash != expected_ibtracs_hash:
        raise ConfigError(
            "IBTrACS SHA256 does not match configs/datasets/ibtracs.yaml: "
            f"{actual_ibtracs_hash}"
        )

    tracks_all = load_ibtracs_csv(ibtracs_path)
    tracks = select_track_types(tracks_all, ibtracs_config["accepted_track_types"])
    coastline = CoastlineIndex.from_gshhg(
        coastline_path,
        sample_spacing_km=float(coastline_config["candidate_sample_spacing_km"]),
        minimum_polygon_area_km2=float(coastline_config["minimum_polygon_area_km2"]),
    )
    selection = _selection_from_config(config)
    cases, points = build_coastal_impact_catalog(tracks, coastline, selection)

    if args.output_dir:
        output_dir = args.output_dir.resolve()
    else:
        data_root = ibtracs_path.parents[2]
        run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + actual_ibtracs_hash[:8]
        output_dir = (
            data_root
            / "interim"
            / "case_catalog"
            / str(config["experiment"]["name"])
            / run_id
        )
    output_dir.mkdir(parents=True, exist_ok=False)

    primary = cases[cases["primary_sample"]].copy() if not cases.empty else cases.copy()
    final_main = cases[~cases["provisional_track"]].copy() if not cases.empty else cases.copy()
    final_main_primary = (
        cases[(~cases["provisional_track"]) & cases["primary_sample"]].copy()
        if not cases.empty
        else cases.copy()
    )
    storms = _storm_catalog(cases)
    primary_storms = (
        storms[storms["primary_sample"]].copy() if not storms.empty else storms.copy()
    )
    cases.to_csv(output_dir / "coastal_impact_cases.csv", index=False)
    primary.to_csv(output_dir / "primary_coastal_impact_cases.csv", index=False)
    final_main.to_csv(output_dir / "final_main_coastal_impact_cases.csv", index=False)
    final_main_primary.to_csv(
        output_dir / "final_main_primary_coastal_impact_cases.csv", index=False
    )
    storms.to_csv(output_dir / "selected_storms.csv", index=False)
    primary_storms.to_csv(output_dir / "primary_selected_storms.csv", index=False)
    points.to_csv(output_dir / "coastal_track_points.csv.gz", index=False, compression="gzip")

    summary = _catalog_summary(cases, points)
    (output_dir / "catalog_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (output_dir / "resolved_config.yaml").write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    manifest = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "distance_model": "WGS84 ellipsoidal geodesic (pyproj.Geod)",
        "land_sea_model": (
            "GSHHG level-1 polygon containment; wind-radius and distance-proxy "
            "contacts require a sea-based storm center; crossings are directional"
        ),
        "ibtracs": {
            "path": str(ibtracs_path),
            "version": ibtracs_config["version"],
            "subset": ibtracs_config["subset"],
            "sha256": actual_ibtracs_hash,
            "quality": ibtracs_quality_summary(tracks_all),
        },
        "coastline": {
            "path": str(coastline_path),
            "version": coastline_config["version"],
            "resolution": coastline_config["resolution"],
            "level": coastline_config["level"],
            "archive_sha256": coastline_config["archive_sha256"],
            "minimum_polygon_area_km2": coastline_config["minimum_polygon_area_km2"],
        },
        "summary": summary,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )

    print(f"Built coastal-impact catalog: {output_dir}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _plan_forecast_cases(args: argparse.Namespace) -> int:
    config = load_yaml(args.config)
    validate_experiment(config)
    sampling = config["forecast_sampling"]
    cases = pd.read_csv(args.cases_file, keep_default_na=False)
    try:
        schedule = build_forecast_case_schedule(
            cases,
            nominal_lead_hours=sampling["nominal_lead_hours"],
            standard_cycle_hours_utc=sampling["standard_cycle_hours_utc"],
            maximum_cycle_offset_hours=sampling["maximum_cycle_offset_hours"],
        )
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    args.output.parent.mkdir(parents=True, exist_ok=True)
    schedule.to_csv(args.output, index=False)
    print(f"Planned {len(schedule)} forecast cases: {args.output}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except ConfigError as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
