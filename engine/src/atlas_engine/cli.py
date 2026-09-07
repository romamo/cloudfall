"""Command-line boundary for the Atlas execution engine."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from atlas.inventory import PlatformInventory
from atlas.validation import StateValidationError, validate_state

from atlas_engine.ansible_inventory import render_ansible_inventory

if TYPE_CHECKING:
    from collections.abc import Sequence


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="atlas-engine")
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
    render_parser.add_argument("state_directory", type=Path)
    render_parser.add_argument(
        "--schemas",
        type=Path,
        default=Path("state/schemas/v1"),
        help="versioned schema directory (default: state/schemas/v1)",
    )
    render_parser.add_argument(
        "--output",
        type=Path,
        help="write inventory JSON to this file instead of stdout",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Render engine output and return a process exit code."""
    arguments = _parser().parse_args(argv)
    if arguments.command != "inventory" or arguments.inventory_command != "render":
        message = "argparse accepted an unsupported engine command"
        raise RuntimeError(message)

    try:
        state = validate_state(Path(arguments.state_directory), Path(arguments.schemas))
    except StateValidationError as error:
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


def run() -> None:
    """Installed engine console-script entry point."""
    raise SystemExit(main())
