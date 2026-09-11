"""Command-line boundary for the Cloudfall execution engine."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from cloudfall.inventory import PlatformInventory
from cloudfall.validation import ConfigValidationError, validate_config

from cloudfall_engine.ansible_inventory import render_ansible_inventory
from cloudfall_engine.artifact import ArtifactBuildError, build_artifact

if TYPE_CHECKING:
    from collections.abc import Sequence


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cloudfall-engine")
    commands = parser.add_subparsers(dest="command", required=True)

    inventory_parser = commands.add_parser(
        "inventory", help="generate execution inventory"
    )
    inventory_commands = inventory_parser.add_subparsers(
        dest="inventory_command", required=True
    )
    render_parser = inventory_commands.add_parser(
        "render", help="render Ansible JSON inventory"
    )
    render_parser.add_argument("config_directory", type=Path)
    render_parser.add_argument(
        "--schemas",
        type=Path,
        default=Path("config/schemas/v1"),
        help="versioned schema directory (default: config/schemas/v1)",
    )
    render_parser.add_argument(
        "--output",
        type=Path,
        help="write inventory JSON to this file instead of stdout",
    )

    artifact_parser = commands.add_parser(
        "artifact", help="build release artifacts"
    )
    artifact_commands = artifact_parser.add_subparsers(
        dest="artifact_command", required=True
    )
    build_parser = artifact_commands.add_parser(
        "build", help="clone, package, and hash one component release"
    )
    build_parser.add_argument("config_directory", type=Path)
    build_parser.add_argument("component")
    build_parser.add_argument(
        "--ref",
        required=True,
        help="git ref (branch, tag, or commit) to package",
    )
    build_parser.add_argument(
        "--schemas",
        type=Path,
        default=Path("config/schemas/v1"),
        help="versioned schema directory (default: config/schemas/v1)",
    )
    build_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("tmp/artifacts"),
        help="artifact output directory (default: tmp/artifacts)",
    )
    return parser


def _render_inventory(arguments: argparse.Namespace) -> int:
    try:
        state = validate_config(
            Path(arguments.config_directory), Path(arguments.schemas)
        )
    except ConfigValidationError as error:
        sys.stderr.write(f"{json.dumps(error.as_dict(), sort_keys=True)}\n")
        return 2

    inventory = PlatformInventory.from_state(state)
    rendered = render_ansible_inventory(inventory)
    serialized = f"{json.dumps(rendered, sort_keys=True)}\n"
    if arguments.output is None:
        sys.stdout.write(serialized)
    else:
        output_path = Path(arguments.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(serialized, encoding="utf-8")
        result = {"output": str(output_path), "status": "ok"}
        sys.stdout.write(f"{json.dumps(result, sort_keys=True)}\n")
    return 0


def _build_artifact(arguments: argparse.Namespace) -> int:
    try:
        state = validate_config(
            Path(arguments.config_directory), Path(arguments.schemas)
        )
    except ConfigValidationError as error:
        sys.stderr.write(f"{json.dumps(error.as_dict(), sort_keys=True)}\n")
        return 2

    inventory = PlatformInventory.from_state(state)
    try:
        built = build_artifact(
            inventory,
            arguments.component,
            arguments.ref,
            Path(arguments.output_dir),
            Path(arguments.schemas),
        )
    except ArtifactBuildError as error:
        sys.stderr.write(f"{json.dumps(error.as_dict(), sort_keys=True)}\n")
        return 2
    except ConfigValidationError as error:
        sys.stderr.write(f"{json.dumps(error.as_dict(), sort_keys=True)}\n")
        return 2
    sys.stdout.write(f"{json.dumps(built.as_dict(), sort_keys=True)}\n")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run one engine command and return a process exit code."""
    arguments = _parser().parse_args(argv)
    if arguments.command == "inventory":
        return _render_inventory(arguments)
    if arguments.command == "artifact":
        return _build_artifact(arguments)
    message = "argparse accepted an unsupported engine command"
    raise RuntimeError(message)


def run() -> None:
    """Installed engine console-script entry point."""
    raise SystemExit(main())
