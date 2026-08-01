"""Command-line interface for the evaluation workflows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from ai_weather_eval import __version__
from ai_weather_eval.config import ConfigError, load_yaml, validate_experiment


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
    catalog_build = catalog_subparsers.add_parser("build", help="Build the landfall case catalog")
    catalog_build.add_argument("--config", type=Path, required=True)
    catalog_build.set_defaults(handler=_pending_workflow)

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

