"""Command-line boundary for Cloudfall operations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from argparse import Namespace
    from collections.abc import Callable, Sequence

    from cloudfall.operations import FleetOperations
    from cloudfall.validation import ValidatedState

from cloudfall.audit import AuditStatus, audit_inventory
from cloudfall.dashboard import build_dashboard
from cloudfall.inventory import PlatformInventory
from cloudfall.observation import load_observations
from cloudfall.operations import UtcTimestamp, build_operations_view
from cloudfall.service_evidence import (
    DeploymentReceiptSet,
    DomainObservationSet,
    EvidenceTimestamp,
    SocketDomainNetworkClient,
    inspect_domains,
    load_deployment_receipts,
    load_domain_observations,
)
from cloudfall.validation import StateValidationError, validate_state


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cloudfall")
    commands = parser.add_subparsers(dest="command", required=True)
    state_parser = commands.add_parser("state", help="operate on platform state")
    state_commands = state_parser.add_subparsers(dest="state_command", required=True)
    validate_parser = state_commands.add_parser(
        "validate", help="validate YAML state and resource references"
    )
    validate_parser.add_argument("state_directory", type=Path)
    validate_parser.add_argument(
        "--schemas",
        type=Path,
        default=Path("state/schemas/v1"),
        help="versioned schema directory (default: state/schemas/v1)",
    )

    inventory_parser = commands.add_parser(
        "inventory", help="query validated platform inventory"
    )
    inventory_commands = inventory_parser.add_subparsers(
        dest="inventory_command", required=True
    )
    show_parser = inventory_commands.add_parser(
        "show", help="show non-secret platform inventory"
    )
    show_parser.add_argument("state_directory", type=Path)
    show_parser.add_argument(
        "--schemas",
        type=Path,
        default=Path("state/schemas/v1"),
        help="versioned schema directory (default: state/schemas/v1)",
    )

    audit_parser = commands.add_parser(
        "audit", help="compare desired state with observed server snapshots"
    )
    audit_parser.add_argument("state_directory", type=Path)
    audit_parser.add_argument(
        "--observed",
        type=Path,
        required=True,
        help="directory containing observed-server JSON snapshots",
    )

    services_parser = commands.add_parser(
        "services", help="inspect and report public service lifecycles"
    )
    services_commands = services_parser.add_subparsers(
        dest="services_command", required=True
    )
    services_inspect_parser = services_commands.add_parser(
        "inspect", help="collect DNS, TLS, origin, and public route evidence"
    )
    services_inspect_parser.add_argument("state_directory", type=Path)
    services_inspect_parser.add_argument(
        "--output",
        type=Path,
        default=Path("tmp/observed-services"),
        help="service observation directory (default: tmp/observed-services)",
    )
    services_inspect_parser.add_argument(
        "--schemas",
        type=Path,
        default=Path("state/schemas/v1"),
        help="versioned schema directory (default: state/schemas/v1)",
    )
    services_status_parser = services_commands.add_parser(
        "status", help="derive service lifecycle status from current evidence"
    )
    services_status_parser.add_argument("state_directory", type=Path)
    services_status_parser.add_argument(
        "--observed",
        type=Path,
        required=True,
        help="directory containing observed-server JSON snapshots",
    )
    services_status_parser.add_argument(
        "--service-observed",
        type=Path,
        default=Path("tmp/observed-services"),
        help="domain observation directory (default: tmp/observed-services)",
    )
    services_status_parser.add_argument(
        "--deployments",
        type=Path,
        default=Path("tmp/deployments"),
        help="deployment receipt directory (default: tmp/deployments)",
    )
    services_status_parser.add_argument(
        "--schemas",
        type=Path,
        default=Path("state/schemas/v1"),
        help="versioned schema directory (default: state/schemas/v1)",
    )
    audit_parser.add_argument(
        "--schemas",
        type=Path,
        default=Path("state/schemas/v1"),
        help="versioned schema directory (default: state/schemas/v1)",
    )

    dashboard_parser = commands.add_parser(
        "dashboard", help="build operations dashboard artifacts"
    )
    dashboard_commands = dashboard_parser.add_subparsers(
        dest="dashboard_command", required=True
    )
    dashboard_build_parser = dashboard_commands.add_parser(
        "build", help="build a static read-only operations dashboard"
    )
    dashboard_build_parser.add_argument("state_directory", type=Path)
    dashboard_build_parser.add_argument(
        "--observed",
        type=Path,
        required=True,
        help="directory containing observed-server JSON snapshots",
    )
    dashboard_build_parser.add_argument(
        "--service-observed",
        type=Path,
        default=Path("tmp/observed-services"),
        help="domain observation directory (default: tmp/observed-services)",
    )
    dashboard_build_parser.add_argument(
        "--deployments",
        type=Path,
        default=Path("tmp/deployments"),
        help="deployment receipt directory (default: tmp/deployments)",
    )
    dashboard_build_parser.add_argument(
        "--output",
        type=Path,
        default=Path("tmp/dashboard"),
        help="dashboard output directory (default: tmp/dashboard)",
    )
    dashboard_build_parser.add_argument(
        "--schemas",
        type=Path,
        default=Path("state/schemas/v1"),
        help="versioned schema directory (default: state/schemas/v1)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""
    arguments = _parser().parse_args(argv)
    state_directory = Path(arguments.state_directory)
    schema_directory = Path(arguments.schemas)
    try:
        state = validate_state(state_directory, schema_directory)
        return _dispatch(arguments, state, schema_directory)
    except StateValidationError as error:
        sys.stderr.write(f"{json.dumps(error.as_dict(), sort_keys=True)}\n")
        return 2


def _dispatch(
    arguments: Namespace, state: ValidatedState, schema_directory: Path
) -> int:
    handlers: dict[str, Callable[[Namespace, ValidatedState, Path], int]] = {
        "audit": _run_audit,
        "dashboard:build": _run_dashboard_build,
        "inventory:show": _run_inventory_show,
        "services:inspect": _run_services_inspect,
        "services:status": _run_services_status,
        "state:validate": _run_state_validate,
    }
    key = _command_key(arguments)
    try:
        handler = handlers[key]
    except KeyError as error:
        message = f"argparse accepted an unsupported command: {key}"
        raise RuntimeError(message) from error
    return handler(arguments, state, schema_directory)


def _command_key(arguments: Namespace) -> str:
    if arguments.command == "audit":
        return "audit"
    subcommand = getattr(arguments, f"{arguments.command}_command", None)
    return f"{arguments.command}:{subcommand}"


def _run_state_validate(
    _arguments: Namespace, state: ValidatedState, _schema_directory: Path
) -> int:
    _write_json(state.as_dict())
    return 0


def _run_inventory_show(
    _arguments: Namespace, state: ValidatedState, _schema_directory: Path
) -> int:
    _write_json(
        {
            "status": "ok",
            "inventory": PlatformInventory.from_state(state).as_dict(),
        }
    )
    return 0


def _run_audit(
    arguments: Namespace, state: ValidatedState, schema_directory: Path
) -> int:
    observations = load_observations(Path(arguments.observed), schema_directory)
    report = audit_inventory(PlatformInventory.from_state(state), observations)
    _write_json(report.as_dict())
    if report.status is AuditStatus.COMPLIANT:
        return 0
    if report.status is AuditStatus.DRIFT:
        return 1
    return 3


def _run_services_inspect(
    arguments: Namespace, state: ValidatedState, _schema_directory: Path
) -> int:
    paths = inspect_domains(
        PlatformInventory.from_state(state),
        Path(arguments.output),
        SocketDomainNetworkClient(),
        observed_at=EvidenceTimestamp.now(),
    )
    _write_json(
        {
            "status": "ok",
            "observations": [str(path) for path in paths],
        }
    )
    return 0


def _run_services_status(
    arguments: Namespace, state: ValidatedState, schema_directory: Path
) -> int:
    operations = _service_operations(arguments, state, schema_directory)
    _write_json(
        {
            "status": "ok",
            "services": [domain.as_dict() for domain in operations.domains],
        }
    )
    return 0


def _run_dashboard_build(
    arguments: Namespace, state: ValidatedState, schema_directory: Path
) -> int:
    operations = _service_operations(arguments, state, schema_directory)
    artifacts = build_dashboard(operations, Path(arguments.output))
    _write_json(
        {
            "status": "ok",
            "health": operations.health.value,
            "tasks": len(operations.tasks),
            "dashboard": artifacts.as_dict(),
        }
    )
    return 0


def _service_operations(
    arguments: Namespace, state: ValidatedState, schema_directory: Path
) -> FleetOperations:
    observations = load_observations(Path(arguments.observed), schema_directory)
    deployment_receipts = _optional_deployment_receipts(
        Path(arguments.deployments), schema_directory
    )
    domain_observations = _optional_domain_observations(
        Path(arguments.service_observed), schema_directory
    )
    return build_operations_view(
        PlatformInventory.from_state(state),
        observations,
        deployment_receipts,
        domain_observations,
        generated_at=UtcTimestamp.now(),
    )


def _write_json(payload: object) -> None:
    sys.stdout.write(f"{json.dumps(payload, sort_keys=True)}\n")


def run() -> None:
    """Installed console-script entry point."""
    raise SystemExit(main())


def _optional_deployment_receipts(
    directory: Path, schema_directory: Path
) -> DeploymentReceiptSet:
    return (
        load_deployment_receipts(directory, schema_directory)
        if directory.is_dir()
        else DeploymentReceiptSet.empty()
    )


def _optional_domain_observations(
    directory: Path, schema_directory: Path
) -> DomainObservationSet:
    return (
        load_domain_observations(directory, schema_directory)
        if directory.is_dir()
        else DomainObservationSet.empty()
    )
