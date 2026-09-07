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

from cloudfall.agent_tools import AgentConfig
from cloudfall.audit import AuditStatus, audit_inventory
from cloudfall.dashboard import build_dashboard
from cloudfall.domain import ReleaseId, ResourceId
from cloudfall.importer import (
    ImportTargets,
    RenderImportError,
    import_render_blueprint,
)
from cloudfall.inventory import PlatformInventory
from cloudfall.lifecycle import (
    DeployOptions,
    EngineContext,
    LifecycleError,
    deploy,
    health,
    restart,
    rollback,
)
from cloudfall.migrate import MigrateError, MigrateOptions, execute_migration
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

    _add_lifecycle_parsers(commands)
    _add_import_parsers(commands)
    _add_migrate_parser(commands)
    return parser


def _add_migrate_parser(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    migrate_parser = commands.add_parser(
        "migrate",
        help="run the resumable end-to-end migration plan",
    )
    migrate_parser.add_argument("state_directory", type=Path)
    migrate_parser.add_argument(
        "--schemas",
        type=Path,
        default=Path("state/schemas/v1"),
        help="versioned schema directory (default: state/schemas/v1)",
    )
    migrate_parser.add_argument(
        "--engine",
        type=Path,
        default=Path("engine"),
        help="engine directory containing ansible contracts (default: engine)",
    )
    migrate_parser.add_argument(
        "--inventory-file",
        type=Path,
        default=Path("tmp/ansible-inventory.json"),
        help="rendered inventory path (default: tmp/ansible-inventory.json)",
    )
    migrate_parser.add_argument(
        "--observed",
        type=Path,
        default=Path("tmp/observed"),
        help="server observation directory (default: tmp/observed)",
    )
    migrate_parser.add_argument(
        "--service-observed",
        type=Path,
        default=Path("tmp/observed-services"),
        help="domain observation directory (default: tmp/observed-services)",
    )
    migrate_parser.add_argument(
        "--deployments",
        type=Path,
        default=Path("tmp/deployments"),
        help="domain receipt directory (default: tmp/deployments)",
    )
    migrate_parser.add_argument(
        "--receipts",
        type=Path,
        default=Path("tmp/releases"),
        help="release receipt directory (default: tmp/releases)",
    )
    migrate_parser.add_argument(
        "--artifacts",
        type=Path,
        default=Path("tmp/artifacts"),
        help="artifact directory (default: tmp/artifacts)",
    )
    migrate_parser.add_argument(
        "--plan-file",
        type=Path,
        default=Path("tmp/migrate/plan.json"),
        help="persisted plan location (default: tmp/migrate/plan.json)",
    )
    migrate_parser.add_argument(
        "--build",
        action="append",
        default=[],
        metavar="COMPONENT=GIT_REF",
        help="build this component from a git ref (repeatable)",
    )
    migrate_parser.add_argument(
        "--release",
        action="append",
        default=[],
        metavar="COMPONENT=RELEASE_ID",
        help="deploy this component from an already-built release",
    )
    migrate_parser.add_argument(
        "--env-file",
        action="append",
        default=[],
        metavar="COMPONENT=PATH",
        help="environment file passed to this component's deployment",
    )
    migrate_parser.add_argument(
        "--yes",
        action="store_true",
        help="execute the plan; without this flag only the plan is shown",
    )
    migrate_parser.add_argument(
        "--restart",
        action="store_true",
        help="discard the persisted plan and start over",
    )


def _add_lifecycle_parsers(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    deploy_parser = commands.add_parser(
        "deploy", help="deploy one built component release"
    )
    _add_lifecycle_arguments(deploy_parser)
    deploy_parser.add_argument(
        "--release",
        required=True,
        help="release id produced by cloudfall-engine artifact build",
    )
    deploy_parser.add_argument(
        "--artifacts",
        type=Path,
        default=Path("tmp/artifacts"),
        help="artifact directory (default: tmp/artifacts)",
    )
    deploy_parser.add_argument(
        "--env-file",
        type=Path,
        help="optional controller-side environment file for the component",
    )
    deploy_parser.add_argument(
        "--receipts",
        type=Path,
        default=Path("tmp/releases"),
        help="release receipt directory (default: tmp/releases)",
    )

    rollback_parser = commands.add_parser(
        "rollback", help="switch one component back to an existing release"
    )
    _add_lifecycle_arguments(rollback_parser)
    rollback_parser.add_argument(
        "--release",
        required=True,
        help="existing release id to activate",
    )

    restart_parser = commands.add_parser(
        "restart", help="restart one component behind its health check"
    )
    _add_lifecycle_arguments(restart_parser)

    health_parser = commands.add_parser(
        "health", help="probe one component's declared health check"
    )
    _add_lifecycle_arguments(health_parser)


def _add_import_parsers(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    import_parser = commands.add_parser(
        "import", help="import external platform definitions"
    )
    import_commands = import_parser.add_subparsers(
        dest="import_command", required=True
    )
    render_parser = import_commands.add_parser(
        "render", help="map a render.yaml blueprint onto Cloudfall state"
    )
    render_parser.add_argument("blueprint", type=Path)
    render_parser.add_argument(
        "--project",
        required=True,
        help="Cloudfall project id (also the project's Linux user)",
    )
    render_parser.add_argument(
        "--server",
        required=True,
        help="declared server id that receives every imported resource",
    )
    render_parser.add_argument(
        "--output",
        type=Path,
        default=Path("tmp/import/state"),
        help="state fragment output directory (default: tmp/import/state)",
    )
    render_parser.add_argument(
        "--env-dir",
        type=Path,
        default=Path("tmp/import/env"),
        help="environment file output directory (default: tmp/import/env)",
    )
    render_parser.add_argument(
        "--schemas",
        type=Path,
        default=Path("state/schemas/v1"),
        help="versioned schema directory (default: state/schemas/v1)",
    )


def _add_lifecycle_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("state_directory", type=Path)
    parser.add_argument("component")
    parser.add_argument(
        "--schemas",
        type=Path,
        default=Path("state/schemas/v1"),
        help="versioned schema directory (default: state/schemas/v1)",
    )
    parser.add_argument(
        "--engine",
        type=Path,
        default=Path("engine"),
        help="engine directory containing ansible contracts (default: engine)",
    )
    parser.add_argument(
        "--inventory-file",
        type=Path,
        default=Path("tmp/ansible-inventory.json"),
        help="rendered inventory path (default: tmp/ansible-inventory.json)",
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""
    arguments = _parser().parse_args(argv)
    if arguments.command == "import":
        return _run_import_render(arguments)
    state_directory = Path(arguments.state_directory)
    schema_directory = Path(arguments.schemas)
    try:
        state = validate_state(state_directory, schema_directory)
        return _dispatch(arguments, state, schema_directory)
    except StateValidationError as error:
        sys.stderr.write(f"{json.dumps(error.as_dict(), sort_keys=True)}\n")
        return 2


def _run_import_render(arguments: Namespace) -> int:
    try:
        targets = ImportTargets(
            project_id=ResourceId.from_boundary(arguments.project),
            server_id=ResourceId.from_boundary(arguments.server),
            state_directory=Path(arguments.output),
            environment_directory=Path(arguments.env_dir),
        )
        result = import_render_blueprint(
            Path(arguments.blueprint), targets, Path(arguments.schemas)
        )
    except (RenderImportError, StateValidationError) as error:
        sys.stderr.write(f"{json.dumps(error.as_dict(), sort_keys=True)}\n")
        return 2
    _write_json(result.as_dict())
    return 0


def _dispatch(
    arguments: Namespace, state: ValidatedState, schema_directory: Path
) -> int:
    handlers: dict[str, Callable[[Namespace, ValidatedState, Path], int]] = {
        "audit": _run_audit,
        "dashboard:build": _run_dashboard_build,
        "deploy": _run_deploy,
        "health": _run_health,
        "inventory:show": _run_inventory_show,
        "migrate": _run_migrate,
        "restart": _run_restart,
        "rollback": _run_rollback,
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
    top_level = {"audit", "deploy", "health", "migrate", "restart", "rollback"}
    if arguments.command in top_level:
        return str(arguments.command)
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


def _engine_context(arguments: Namespace, schema_directory: Path) -> EngineContext:
    return EngineContext(
        state_directory=Path(arguments.state_directory),
        schema_directory=schema_directory,
        engine_directory=Path(arguments.engine),
        inventory_file=Path(arguments.inventory_file),
    )


def _lifecycle_exit(error: LifecycleError) -> int:
    sys.stderr.write(f"{json.dumps(error.as_dict(), sort_keys=True)}\n")
    return 1 if error.code == "lifecycle_execution_failed" else 2


def _run_deploy(
    arguments: Namespace, _state: ValidatedState, schema_directory: Path
) -> int:
    try:
        result = deploy(
            _engine_context(arguments, schema_directory),
            ResourceId.from_boundary(arguments.component),
            ReleaseId.from_boundary(arguments.release),
            Path(arguments.artifacts),
            DeployOptions(
                environment_file=(
                    Path(arguments.env_file)
                    if arguments.env_file is not None
                    else None
                ),
                receipt_directory=Path(arguments.receipts),
            ),
        )
    except LifecycleError as error:
        return _lifecycle_exit(error)
    _write_json(result.as_dict())
    return 0


def _run_rollback(
    arguments: Namespace, _state: ValidatedState, schema_directory: Path
) -> int:
    try:
        result = rollback(
            _engine_context(arguments, schema_directory),
            ResourceId.from_boundary(arguments.component),
            ReleaseId.from_boundary(arguments.release),
        )
    except LifecycleError as error:
        return _lifecycle_exit(error)
    _write_json(result.as_dict())
    return 0


def _run_restart(
    arguments: Namespace, _state: ValidatedState, schema_directory: Path
) -> int:
    try:
        result = restart(
            _engine_context(arguments, schema_directory),
            ResourceId.from_boundary(arguments.component),
        )
    except LifecycleError as error:
        return _lifecycle_exit(error)
    _write_json(result.as_dict())
    return 0


def _run_health(
    arguments: Namespace, _state: ValidatedState, schema_directory: Path
) -> int:
    try:
        result = health(
            _engine_context(arguments, schema_directory),
            ResourceId.from_boundary(arguments.component),
        )
    except LifecycleError as error:
        return _lifecycle_exit(error)
    _write_json(result.as_dict())
    return 0 if result.healthy else 1


def _run_migrate(
    arguments: Namespace, _state: ValidatedState, schema_directory: Path
) -> int:
    try:
        builds = _key_value_pairs(arguments.build, "--build")
        releases = _key_value_pairs(arguments.release, "--release")
        environment_files = {
            component: Path(value)
            for component, value in _key_value_pairs(
                arguments.env_file, "--env-file"
            ).items()
        }
    except ValueError as error:
        payload = {
            "status": "error",
            "error": {"code": "invalid_argument", "message": str(error)},
        }
        sys.stderr.write(f"{json.dumps(payload, sort_keys=True)}\n")
        return 2
    config = AgentConfig(
        state_directory=Path(arguments.state_directory),
        schema_directory=schema_directory,
        engine_directory=Path(arguments.engine),
        inventory_file=Path(arguments.inventory_file),
        observed_directory=Path(arguments.observed),
        service_observed_directory=Path(arguments.service_observed),
        deployments_directory=Path(arguments.deployments),
        releases_directory=Path(arguments.receipts),
        artifacts_directory=Path(arguments.artifacts),
    )
    options = MigrateOptions(
        plan_file=Path(arguments.plan_file),
        builds=builds,
        releases=releases,
        environment_files=environment_files,
        execute=arguments.yes,
        restart=arguments.restart,
    )
    try:
        result = execute_migration(config, options)
    except MigrateError as error:
        sys.stderr.write(f"{json.dumps(error.as_dict(), sort_keys=True)}\n")
        return 2
    _write_json(result)
    status = str(result["status"])
    if status in {"ok", "plan"}:
        return 0
    if status == "paused":
        return 3
    return 1


def _key_value_pairs(entries: list[str], option: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for entry in entries:
        component, separator, value = entry.partition("=")
        if not separator or not component or not value:
            message = f"{option} expects COMPONENT=VALUE, got {entry!r}"
            raise ValueError(message)
        pairs[component] = value
    return pairs


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
