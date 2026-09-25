"""Command-line boundary for the Cloudfall execution engine."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from cloudfall.arguments import (
    StrictArgumentParser,
    parse_arguments,
    project_path_argument,
)
from cloudfall.inventory import PlatformInventory
from cloudfall.output import begin_invocation
from cloudfall.project import (
    PROJECT_DIRECTORY_VARIABLE,
    ProjectError,
    project_context,
)
from cloudfall.resources import default_engine_directory, default_schema_directory
from cloudfall.validation import ConfigValidationError, validate_config

from cloudfall_engine.ansible_inventory import render_ansible_inventory
from cloudfall_engine.artifact import ArtifactBuildError, build_artifact
from cloudfall_engine.playbook import (
    PlaybookError,
    PlaybookRun,
    bundled_playbooks,
    execute_playbook,
    resolve_playbook,
)

if TYPE_CHECKING:
    import argparse
    from collections.abc import Sequence


def _add_project_directory_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--project",
        type=Path,
        default=None,
        help=(
            f"project directory to run in (default: ${PROJECT_DIRECTORY_VARIABLE}, "
            "else the current directory when it is a project)"
        ),
    )


def _parser() -> StrictArgumentParser:
    parser = StrictArgumentParser(prog="cloudfall-engine")
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
    _add_project_directory_argument(render_parser)
    render_parser.add_argument(
        "--schemas",
        type=Path,
        default=default_schema_directory(),
        help="versioned schema directory (default: bundled schemas)",
    )
    render_parser.add_argument(
        "--output",
        type=project_path_argument,
        help="write inventory JSON to this file instead of stdout",
    )

    artifact_parser = commands.add_parser("artifact", help="build release artifacts")
    artifact_commands = artifact_parser.add_subparsers(
        dest="artifact_command", required=True
    )
    build_parser = artifact_commands.add_parser(
        "build", help="clone, package, and hash one component release"
    )
    _add_project_directory_argument(build_parser)
    build_parser.add_argument("component")
    build_parser.add_argument(
        "--ref",
        required=True,
        help="git ref (branch, tag, or commit) to package",
    )
    build_parser.add_argument(
        "--schemas",
        type=Path,
        default=default_schema_directory(),
        help="versioned schema directory (default: bundled schemas)",
    )
    build_parser.add_argument(
        "--output-dir",
        type=project_path_argument,
        default=Path("tmp/artifacts"),
        help="artifact output directory (default: tmp/artifacts)",
    )

    playbook_parser = commands.add_parser(
        "playbook", help="run Ansible playbooks with the engine configuration"
    )
    playbook_commands = playbook_parser.add_subparsers(
        dest="playbook_command", required=True
    )
    list_parser = playbook_commands.add_parser(
        "list", help="list the playbooks bundled with the engine"
    )
    _add_engine_argument(list_parser)
    run_parser = playbook_commands.add_parser(
        "run", help="run one bundled playbook or a playbook file"
    )
    run_parser.add_argument(
        "playbook",
        help="bundled playbook name (see `playbook list`) or a playbook path",
    )
    _add_engine_argument(run_parser)
    run_parser.add_argument(
        "--inventory",
        type=project_path_argument,
        default=Path("tmp/ansible-inventory.json"),
        help="rendered inventory path (default: tmp/ansible-inventory.json)",
    )
    run_parser.add_argument(
        "--roles",
        type=Path,
        action="append",
        default=[],
        help="role directory searched before the bundled roles (repeatable)",
    )
    run_parser.add_argument(
        "--extra-vars",
        action="append",
        default=[],
        help="passed through to ansible-playbook unchanged (repeatable)",
    )
    run_parser.add_argument(
        "--tags",
        action="append",
        default=[],
        help="only run plays and tasks tagged with this value (repeatable)",
    )
    run_parser.add_argument("--limit", help="restrict the run to a host pattern")
    run_parser.add_argument("--check", action="store_true", help="run in check mode")
    run_parser.add_argument("--diff", action="store_true", help="show file diffs")
    run_parser.add_argument(
        "--syntax-check",
        action="store_true",
        help="only check the playbook syntax",
    )
    return parser


def _add_engine_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--engine",
        type=Path,
        default=default_engine_directory(),
        help="engine directory containing ansible contracts (default: bundled engine)",
    )


def _render_inventory(arguments: argparse.Namespace) -> int:
    try:
        state = validate_config(
            Path(arguments.project_directory), Path(arguments.schemas)
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
            Path(arguments.project_directory), Path(arguments.schemas)
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


def _list_playbooks(arguments: argparse.Namespace) -> int:
    try:
        names = bundled_playbooks(Path(arguments.engine))
    except PlaybookError as error:
        sys.stderr.write(f"{json.dumps(error.as_dict(), sort_keys=True)}\n")
        return 2
    result = {"engine": str(arguments.engine), "playbooks": list(names)}
    sys.stdout.write(f"{json.dumps(result, sort_keys=True)}\n")
    return 0


def _run_playbook(arguments: argparse.Namespace) -> int:
    engine_directory = Path(arguments.engine)
    try:
        run = PlaybookRun(
            playbook=resolve_playbook(arguments.playbook, engine_directory),
            inventory_file=Path(arguments.inventory),
            role_directories=tuple(Path(role) for role in arguments.roles),
            extra_vars=tuple(arguments.extra_vars),
            tags=tuple(arguments.tags),
            limit=arguments.limit,
            check=arguments.check,
            diff=arguments.diff,
            syntax_check=arguments.syntax_check,
        )
        return execute_playbook(run, engine_directory)
    except PlaybookError as error:
        sys.stderr.write(f"{json.dumps(error.as_dict(), sort_keys=True)}\n")
        return 2


def main(argv: Sequence[str] | None = None) -> int:
    """Run one engine command and return a process exit code."""
    # The shared parser reports usage errors through cloudfall's writer,
    # which stamps each document with its invocation.
    begin_invocation()
    arguments = parse_arguments(_parser(), argv)
    if arguments.command == "playbook":
        if arguments.playbook_command == "list":
            return _list_playbooks(arguments)
        return _run_playbook(arguments)
    try:
        with project_context(arguments.project, os.environ) as project_directory:
            arguments.project_directory = project_directory
            if arguments.command == "inventory":
                return _render_inventory(arguments)
            if arguments.command == "artifact":
                return _build_artifact(arguments)
    except ProjectError as error:
        sys.stderr.write(f"{json.dumps(error.as_dict(), sort_keys=True)}\n")
        return 2
    message = "argparse accepted an unsupported engine command"
    raise RuntimeError(message)


def run() -> None:
    """Installed engine console-script entry point."""
    raise SystemExit(main())
