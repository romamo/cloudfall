"""MCP server exposing the Cloudfall agent toolset over stdio.

Requires the ``cloudfall[mcp]`` extra. Read-only evidence tools carry a
read-only annotation; anything that changes servers is annotated
destructive and demands the toolset's explicit confirmation handshake.
The fleet-declaring tools (``add_ssh_key``, ``add_server_type``,
``add_server``) write resources into the project and are neither: they
never touch a server and never overwrite an existing resource.

``cloudfall init`` is deliberately CLI-only. The server is started inside
a project (``--project``, ``CLOUDFALL_PROJECT``, or the current directory)
and refuses to start outside one, so by the time an agent can call a tool
the project already exists; a tool that lays out a new project elsewhere
would escape the directory every other tool is confined to.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from cloudfall.agent_tools import AgentConfig, AgentToolset
from cloudfall.ansible_api import AnsibleReadError, inventory_from_config
from cloudfall.arguments import (
    StrictArgumentParser,
    parse_arguments,
    project_path_argument,
)
from cloudfall.catalog import CATALOG_DIRECTORY, RiskLevel
from cloudfall.decision import DECISION_DIRECTORY, DecisionError
from cloudfall.fleet_tools import (
    FleetConfig,
    FleetToolError,
    FleetToolset,
    operation_tool_description,
    operation_tool_name,
)
from cloudfall.observe import ObserveError
from cloudfall.project import (
    PROJECT_DIRECTORY_VARIABLE,
    ProjectError,
    is_project,
    project_context,
)
from cloudfall.resources import default_engine_directory, default_schema_directory
from cloudfall.validation import ConfigValidationError
from cloudfall.why import WhyError

if TYPE_CHECKING:
    import argparse
    from collections.abc import Callable, Mapping, Sequence

    _Registration = tuple[Callable[..., str], str, str, ToolAnnotations | None]

    from cloudfall.catalog import Operation

_FLEET_INSTRUCTIONS = """\
Cloudfall is the record for a fleet run through the team's own Ansible
repository. The tool list is the catalog the team declared: a playbook
they have not declared as an operation is not reachable here, and a raw
shell call is what this server exists to make unnecessary.

The loop is fixed: read the fleet, collect snapshots, pick an operation
and say why, then call it to preview it in check mode. Calling an
operation tool changes nothing: it runs the playbook in check mode and
records the operation, the targets, the evidence it was based on and the
diff it would produce. Approving that record is a command a person runs;
no tool here can do it, whatever reason is given for asking. When asked
why something was done, answer from the record with the why tool rather
than from memory.

Each operation tool carries the risk level the team declared, so read,
mutating and destructive are visible to this client's own gate.
"""

_INSTRUCTIONS = """\
Cloudfall manages declarative infrastructure state for dedicated Debian
servers. Read-only tools validate state and derive evidence; destructive
tools change servers and require calling twice: the first call returns a
confirmation-required preview, the second call with confirm=true executes.
Every mutation writes receipts; nothing reports success it cannot prove.
The add_ssh_key, add_server_type, and add_server tools declare the fleet:
they write schema-validated resource files into the project (never onto a
server), re-validate the whole project afterwards, and remove what they
wrote when that validation fails. They never overwrite an existing
resource. The project itself is laid out beforehand with `cloudfall init`
on the CLI; this server always runs inside an existing project.
"""


def create_fleet_server(config: FleetConfig) -> MCPServer:
    """Create the server for a brownfield repository: the catalog is the list.

    Every declared operation becomes one tool carrying its risk level as
    annotations, so a client gates on risk without Cloudfall's help. No
    tool here changes a host: an operation tool runs check mode and
    records what it would do, and the approval is a command a person runs.
    """
    server = MCPServer(name="cloudfall", instructions=_FLEET_INSTRUCTIONS)
    toolset = FleetToolset(config)
    for handler, name, description, tool_annotations in (
        *_fleet_registrations(toolset),
        *_operation_registrations(toolset),
    ):
        server.add_tool(
            handler,
            name=name,
            description=description,
            annotations=tool_annotations,
        )
    return server


def _fleet_result(call: Callable[[], dict[str, object]]) -> str:
    """Return Cloudfall's own error envelope rather than an opaque crash.

    A declaration an agent can fix is worth more than a tool error it
    cannot read, so every failure this surface knows about comes back as
    data with its code intact.
    """
    try:
        return _dump(call())
    except (
        AnsibleReadError,
        ConfigValidationError,
        DecisionError,
        FleetToolError,
        ObserveError,
        WhyError,
    ) as error:
        return _dump(error.as_dict())


def _fleet_registrations(toolset: FleetToolset) -> tuple[_Registration, ...]:
    read_only = ToolAnnotations(read_only_hint=True)

    def list_operations() -> str:
        return _fleet_result(toolset.operations)

    def show_operation(operation: str) -> str:
        return _fleet_result(lambda: toolset.operation(operation))

    def show_fleet() -> str:
        return _fleet_result(toolset.fleet)

    def observe_fleet() -> str:
        return _fleet_result(toolset.observe)

    def audit_fleet() -> str:
        return _fleet_result(toolset.audit)

    def list_decisions() -> str:
        return _fleet_result(toolset.decisions)

    def why(
        host: str = "", operation: str = "", since: str = "", until: str = ""
    ) -> str:
        return _fleet_result(
            lambda: toolset.why(
                host or None, operation or None, since or None, until or None
            )
        )

    return (
        (
            list_operations,
            "list_operations",
            "List the operations the team declared, with risk and inputs",
            read_only,
        ),
        (
            show_operation,
            "show_operation",
            "Show one declared operation in full",
            read_only,
        ),
        (
            show_fleet,
            "show_fleet",
            "Read the fleet from the team's own Ansible inventory",
            read_only,
        ),
        (
            observe_fleet,
            "observe_fleet",
            "Collect one read-only snapshot per declared server",
            read_only,
        ),
        (
            audit_fleet,
            "audit_fleet",
            "Compare the declared fleet with the snapshots on disk",
            read_only,
        ),
        (
            list_decisions,
            "list_decisions",
            "List what was proposed, what check mode showed, and who approved",
            read_only,
        ),
        (
            why,
            "why",
            "Answer why the agent did that: the decisions the record holds "
            "for a host, an operation or a time window (ISO 8601), each told "
            "as what it saw, proposed, showed, who approved and how it ended",
            read_only,
        ),
    )


def _operation_registrations(toolset: FleetToolset) -> tuple[_Registration, ...]:
    """Expose each declared operation as its own risk-annotated tool."""
    return tuple(
        (
            _operation_handler(toolset, operation),
            operation_tool_name(operation),
            operation_tool_description(operation),
            _operation_annotations(operation),
        )
        for operation in toolset.catalog().operations
    )


def _operation_handler(
    toolset: FleetToolset, operation: Operation
) -> Callable[..., str]:
    operation_id = operation.operation_id.value

    def propose_operation(
        target: str = "", inputs: dict[str, str] | None = None
    ) -> str:
        return _fleet_result(
            lambda: toolset.propose(operation_id, target or None, inputs)
        )

    return propose_operation


def _operation_annotations(operation: Operation) -> ToolAnnotations:
    """Carry the declared risk level into the client's own gate.

    Every operation tool is read-only in effect, because calling one runs
    check mode; the destructive hint says what approving it would mean.
    """
    if operation.risk is RiskLevel.DESTRUCTIVE:
        return ToolAnnotations(read_only_hint=True, destructive_hint=True)
    return ToolAnnotations(read_only_hint=True, destructive_hint=False)


def create_server(config: AgentConfig) -> MCPServer:
    """Create the MCP server and register every agent tool."""
    server = MCPServer(name="cloudfall", instructions=_INSTRUCTIONS)
    toolset = AgentToolset(config)
    registrations = (
        *_evidence_registrations(toolset),
        *_authoring_registrations(toolset),
        *_build_registrations(toolset),
        *_mutation_registrations(toolset),
        *_backup_registrations(toolset),
        *_operator_registrations(toolset),
    )
    for handler, name, description, tool_annotations in registrations:
        server.add_tool(
            handler,
            name=name,
            description=description,
            annotations=tool_annotations,
        )
    return server


def _evidence_registrations(
    toolset: AgentToolset,
) -> tuple[_Registration, ...]:
    read_only = ToolAnnotations(read_only_hint=True)

    def validate_config() -> str:
        return _dump(toolset.validate())

    def show_inventory() -> str:
        return _dump(toolset.inventory())

    def audit_servers() -> str:
        return _dump(toolset.audit())

    def services_status() -> str:
        return _dump(toolset.services_status())

    def component_health(component: str) -> str:
        return _dump(toolset.component_health(component))

    def inspect_servers() -> str:
        return _dump(toolset.inspect_servers())

    def inspect_services() -> str:
        return _dump(toolset.inspect_services())

    return (
        (
            validate_config,
            "validate_config",
            "Validate declared config and resource references",
            read_only,
        ),
        (
            show_inventory,
            "show_inventory",
            "Return the non-secret typed platform inventory",
            read_only,
        ),
        (
            audit_servers,
            "audit_servers",
            "Compare desired state with collected server evidence",
            read_only,
        ),
        (
            services_status,
            "services_status",
            "Derive the evidence-based public service lifecycle",
            read_only,
        ),
        (
            component_health,
            "component_health",
            "Probe one component's declared health check on its servers",
            read_only,
        ),
        (
            inspect_servers,
            "inspect_servers",
            "Collect read-only server evidence snapshots over SSH",
            read_only,
        ),
        (
            inspect_services,
            "inspect_services",
            "Collect DNS, TLS, origin, and public route evidence",
            read_only,
        ),
    )


def _authoring_registrations(
    toolset: AgentToolset,
) -> tuple[_Registration, ...]:
    writes_project = ToolAnnotations(
        read_only_hint=False, destructive_hint=False, idempotent_hint=False
    )

    def add_ssh_key(
        key_file: str,
        owner: str,
        id: str | None = None,  # noqa: A002 - mirrors `cloudfall add --id`.
        environment: str = "production",
        description: str | None = None,
    ) -> str:
        return _dump(toolset.add_ssh_key(key_file, owner, id, environment, description))

    def add_server_type(
        id: str,  # noqa: A002 - mirrors the `cloudfall add server-type` id.
        description: str | None = None,
    ) -> str:
        return _dump(toolset.add_server_type(id, description))

    def add_server(  # noqa: PLR0913 - boundary signature mirrors the CLI.
        id: str,  # noqa: A002 - mirrors the `cloudfall add server` id.
        address: str,
        type: str = "debian-application",  # noqa: A002 - mirrors `--type`.
        environment: str = "production",
        hostname: str | None = None,
        ssh_user: str = "root",
        ssh_port: int = 22,
        description: str | None = None,
    ) -> str:
        return _dump(
            toolset.add_server(
                id,
                address,
                type,
                environment,
                hostname,
                ssh_user,
                ssh_port,
                description,
            )
        )

    return (
        (
            add_ssh_key,
            "add_ssh_key",
            "Declare an SshPublicKey resource read from a controller-side "
            "public key file (e.g. ~/.ssh/id_ed25519.pub); writes into the "
            "project and re-validates it, never overwrites",
            writes_project,
        ),
        (
            add_server_type,
            "add_server_type",
            "Declare a ServerType resource from the bundled Debian 13 "
            "baseline (no software RAID, default-deny firewall with "
            "22/80/443); writes into the project and re-validates it, "
            "never overwrites",
            writes_project,
        ),
        (
            add_server,
            "add_server",
            "Declare a Server resource reachable at an address, creating "
            "its baseline ServerType when the project lacks it; writes "
            "into the project and re-validates it, never overwrites",
            writes_project,
        ),
    )


def _build_registrations(toolset: AgentToolset) -> tuple[_Registration, ...]:
    def import_render(
        blueprint: str,
        application: str,
        server_id: str,
        output_directory: str = "tmp/import/config",
        environment_directory: str = "tmp/import/env",
    ) -> str:
        return _dump(
            toolset.import_render(
                blueprint,
                application,
                server_id,
                output_directory,
                environment_directory,
            )
        )

    def import_render_api(
        api_key_file: str,
        application: str,
        server_id: str,
        output_directory: str = "tmp/import/config",
        environment_directory: str = "tmp/import/env",
    ) -> str:
        return _dump(
            toolset.import_render_api(
                api_key_file,
                application,
                server_id,
                output_directory,
                environment_directory,
            )
        )

    def render_secrets(component: str) -> str:
        return _dump(toolset.render_secrets(component))

    def build_artifact(component: str, git_ref: str) -> str:
        return _dump(toolset.build_artifact(component, git_ref))

    return (
        (
            import_render,
            "import_render",
            "Map a render.yaml blueprint onto Cloudfall config fragments "
            "with a structured gap report",
            None,
        ),
        (
            import_render_api,
            "import_render_api",
            "Map a live Render workspace onto Cloudfall config fragments "
            "through the Render API; the api_key_file is a controller-side "
            "file containing only the API key",
            None,
        ),
        (
            render_secrets,
            "render_secrets",
            "Resolve a component's declared secret references from the "
            "sops-encrypted secrets directory into its 0600 environment "
            "file; the envelope carries key names and a hash, never values",
            None,
        ),
        (
            build_artifact,
            "build_artifact",
            "Clone a component repository at a git ref and package a "
            "hashed release artifact",
            None,
        ),
    )


def _mutation_registrations(
    toolset: AgentToolset,
) -> tuple[_Registration, ...]:
    destructive = ToolAnnotations(read_only_hint=False, destructive_hint=True)

    def deploy_component(
        component: str,
        release: str,
        environment_file: str | None = None,
        confirm: bool = False,  # noqa: FBT001, FBT002 - explicit agent gate.
    ) -> str:
        return _dump(
            toolset.deploy_component(
                component, release, environment_file, confirm=confirm
            )
        )

    def rollback_component(
        component: str,
        release: str,
        confirm: bool = False,  # noqa: FBT001, FBT002 - explicit agent gate.
    ) -> str:
        return _dump(toolset.rollback_component(component, release, confirm=confirm))

    def restart_component(
        component: str,
        confirm: bool = False,  # noqa: FBT001, FBT002 - explicit agent gate.
    ) -> str:
        return _dump(toolset.restart_component(component, confirm=confirm))

    def migrate_database(
        service: str,
        database: str,
        source_url_file: str,
        confirm: bool = False,  # noqa: FBT001, FBT002 - explicit agent gate.
    ) -> str:
        return _dump(
            toolset.migrate_database(
                service, database, source_url_file, confirm=confirm
            )
        )

    def converge_baseline(
        confirm: bool = False,  # noqa: FBT001, FBT002 - explicit agent gate.
    ) -> str:
        return _dump(toolset.converge_baseline(confirm=confirm))

    def converge_services(
        confirm: bool = False,  # noqa: FBT001, FBT002 - explicit agent gate.
    ) -> str:
        return _dump(toolset.converge_services(confirm=confirm))

    def converge_domains(
        issue_certificates: bool = False,  # noqa: FBT001, FBT002 - explicit.
        confirm: bool = False,  # noqa: FBT001, FBT002 - explicit agent gate.
    ) -> str:
        return _dump(
            toolset.converge_domains(
                issue_certificates=issue_certificates, confirm=confirm
            )
        )

    def migrate(  # noqa: PLR0913 - boundary signature mirrors the toolset.
        builds: dict[str, str] | None = None,
        releases: dict[str, str] | None = None,
        environment_files: dict[str, str] | None = None,
        data_migrations: dict[str, str] | None = None,
        plan_file: str = "tmp/migrate/plan.json",
        restart_plan: bool = False,  # noqa: FBT001, FBT002 - explicit gate.
        confirm: bool = False,  # noqa: FBT001, FBT002 - explicit agent gate.
    ) -> str:
        return _dump(
            toolset.migrate(
                builds,
                releases,
                environment_files,
                data_migrations,
                plan_file,
                restart_plan=restart_plan,
                confirm=confirm,
            )
        )

    return (
        (
            deploy_component,
            "deploy_component",
            "Deploy one built release with digest verification, a health "
            "gate, and automatic rollback; requires confirm=true",
            destructive,
        ),
        (
            rollback_component,
            "rollback_component",
            "Activate an already-retained release behind the health gate; "
            "requires confirm=true",
            destructive,
        ),
        (
            restart_component,
            "restart_component",
            "Restart one component behind its health check; requires confirm=true",
            destructive,
        ),
        (
            migrate_database,
            "migrate_database",
            "Dump an external PostgreSQL database and restore it into a "
            "declared service with per-table row-count verification; the "
            "source_url_file is a controller-side file containing only the "
            "connection URL; requires confirm=true",
            destructive,
        ),
        (
            converge_baseline,
            "converge_baseline",
            "Converge every server to the managed baseline; requires confirm=true",
            destructive,
        ),
        (
            converge_services,
            "converge_services",
            "Converge every declared infrastructure service; requires confirm=true",
            destructive,
        ),
        (
            converge_domains,
            "converge_domains",
            "Converge every declared public domain route; requires confirm=true",
            destructive,
        ),
        (
            migrate,
            "migrate",
            "Preview (without confirm) or execute (confirm=true) the "
            "resumable end-to-end migration plan: baseline, services, "
            "builds, deployments, routes, DNS verification, TLS, and a "
            "final compliance-proving evidence pass",
            destructive,
        ),
    )


def _backup_registrations(
    toolset: AgentToolset,
) -> tuple[_Registration, ...]:
    destructive = ToolAnnotations(read_only_hint=False, destructive_hint=True)

    def backup_service(
        service: str,
        confirm: bool = False,  # noqa: FBT001, FBT002 - explicit agent gate.
    ) -> str:
        return _dump(toolset.backup_service(service, confirm=confirm))

    def verify_backup(
        service: str,
        confirm: bool = False,  # noqa: FBT001, FBT002 - explicit agent gate.
    ) -> str:
        return _dump(toolset.verify_backup(service, confirm=confirm))

    return (
        (
            backup_service,
            "backup_service",
            "Run the declared backup for one service on its server, "
            "writing a schema-valid receipt; requires confirm=true",
            destructive,
        ),
        (
            verify_backup,
            "verify_backup",
            "Prove the newest backup of one service restores (dump "
            "restore-check or snapshot integrity), receipted; requires "
            "confirm=true",
            destructive,
        ),
    )


def _operator_registrations(
    toolset: AgentToolset,
) -> tuple[_Registration, ...]:
    read_only = ToolAnnotations(read_only_hint=True)
    destructive = ToolAnnotations(read_only_hint=False, destructive_hint=True)

    def operator_proposals() -> str:
        return _dump(toolset.operator_proposals())

    def operator_watch(
        drift: bool = False,  # noqa: FBT001, FBT002 - explicit agent flag.
    ) -> str:
        return _dump(toolset.operator_watch(drift=drift))

    def operator_approve(
        proposal: str,
        confirm: bool = False,  # noqa: FBT001, FBT002 - explicit agent gate.
    ) -> str:
        return _dump(toolset.operator_approve(proposal, confirm=confirm))

    return (
        (
            operator_proposals,
            "operator_proposals",
            "List every operator proposal receipt: trigger evidence, "
            "diagnosis, proposed operation, and outcome",
            read_only,
        ),
        (
            operator_watch,
            "operator_watch",
            "Run one operator watch pass: fetch firing declared alerts "
            "through the mTLS gateway (and audit for drift when "
            "drift=true) and write proposals for anything new",
            read_only,
        ),
        (
            operator_approve,
            "operator_approve",
            "Execute one operator proposal's declared remediation and "
            "verify its trigger evidence resolves; requires confirm=true",
            destructive,
        ),
    )


def _dump(payload: dict[str, object]) -> str:
    return json.dumps(payload, sort_keys=True)


def _parser() -> StrictArgumentParser:
    parser = StrictArgumentParser(
        prog="cloudfall-mcp",
        description=(
            "Expose the Cloudfall agent toolset over MCP stdio. Read-only"
            " evidence tools are available freely; every server-changing"
            " tool requires an explicit two-step confirmation handshake."
        ),
    )
    parser.add_argument(
        "--project",
        type=Path,
        default=None,
        help=(
            f"project directory to run in (default: ${PROJECT_DIRECTORY_VARIABLE}, "
            "else the current directory when it is a project)"
        ),
    )
    parser.add_argument(
        "--repository",
        type=Path,
        default=None,
        help=(
            "Ansible repository to serve instead of a project: the declared "
            "operations become the tool list, and no tool changes a host"
        ),
    )
    parser.add_argument(
        "--inventory",
        type=Path,
        default=None,
        help=(
            "inventory inside the repository (default: the one its ansible.cfg names)"
        ),
    )
    parser.add_argument(
        "--operations",
        type=project_path_argument,
        default=Path(CATALOG_DIRECTORY),
        help="directory holding the operation documents (default: %(default)s)",
    )
    parser.add_argument(
        "--decisions",
        type=project_path_argument,
        default=Path(DECISION_DIRECTORY),
        help="directory holding the decision records (default: %(default)s)",
    )
    parser.add_argument(
        "--schemas",
        type=Path,
        default=default_schema_directory(),
        help=(
            "JSON Schema directory used to validate the project (default: %(default)s)"
        ),
    )
    parser.add_argument(
        "--engine",
        type=Path,
        default=default_engine_directory(),
        help="engine module directory containing the Ansible playbooks"
        " (default: %(default)s)",
    )
    parser.add_argument(
        "--inventory-file",
        type=project_path_argument,
        default=Path("tmp/ansible-inventory.json"),
        help="path where the rendered Ansible inventory is written"
        " (default: %(default)s)",
    )
    parser.add_argument(
        "--observed",
        type=project_path_argument,
        default=Path("tmp/observed"),
        help="directory holding read-only server observation snapshots"
        " (default: %(default)s)",
    )
    parser.add_argument(
        "--service-observed",
        type=project_path_argument,
        default=Path("tmp/observed-services"),
        help="directory holding DNS, TLS, origin, and public route evidence"
        " (default: %(default)s)",
    )
    parser.add_argument(
        "--deployments",
        type=project_path_argument,
        default=Path("tmp/deployments"),
        help="directory holding domain deployment receipts (default: %(default)s)",
    )
    parser.add_argument(
        "--releases",
        type=project_path_argument,
        default=Path("tmp/releases"),
        help="directory holding component release receipts (default: %(default)s)",
    )
    parser.add_argument(
        "--artifacts",
        type=project_path_argument,
        default=Path("tmp/artifacts"),
        help="directory holding built release artifacts (default: %(default)s)",
    )
    parser.add_argument(
        "--data-migrations",
        type=project_path_argument,
        default=Path("tmp/data-migrations"),
        help="directory holding data-migration receipts (default: %(default)s)",
    )
    parser.add_argument(
        "--secrets-dir",
        type=Path,
        default=Path("secrets"),
        help="sops-encrypted secrets directory (default: %(default)s)",
    )
    parser.add_argument(
        "--env-dir",
        type=project_path_argument,
        default=Path("tmp/env"),
        help="rendered environment file directory (default: %(default)s)",
    )
    parser.add_argument(
        "--env-receipts",
        type=project_path_argument,
        default=Path("tmp/env-receipts"),
        help="rendered environment receipt directory (default: %(default)s)",
    )
    parser.add_argument(
        "--backups",
        type=project_path_argument,
        default=Path("tmp/backups"),
        help="directory holding backup operation receipts (default: %(default)s)",
    )
    parser.add_argument(
        "--proposals",
        type=project_path_argument,
        default=Path("tmp/operator/proposals"),
        help="directory holding operator proposal receipts (default: %(default)s)",
    )
    parser.add_argument(
        "--gateway-ca",
        type=Path,
        default=None,
        help="CA bundle for the logging gateway's alerts route",
    )
    parser.add_argument(
        "--gateway-cert",
        type=Path,
        default=None,
        help="client certificate for the logging gateway's alerts route",
    )
    parser.add_argument(
        "--gateway-key",
        type=Path,
        default=None,
        help="client key for the logging gateway's alerts route",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the MCP server over stdio."""
    arguments = parse_arguments(_parser(), argv)
    try:
        repository = _repository(arguments, os.environ)
        if repository is not None:
            _serve_fleet(arguments, repository)
            return 0
        with project_context(arguments.project, os.environ) as project_directory:
            _serve(arguments, project_directory)
    except (ProjectError, FleetToolError, ConfigValidationError) as error:
        sys.stderr.write(f"{_dump(error.as_dict())}\n")
        return 2
    return 0


def _repository(
    arguments: argparse.Namespace, environment: Mapping[str, str]
) -> Path | None:
    """Return the Ansible repository to serve, if this is a brownfield run.

    Precedence matches the CLI: an explicitly named repository, then the
    current directory when it is an Ansible control repository rather than
    a Cloudfall project.
    """
    if arguments.repository is not None:
        return Path(arguments.repository)
    if arguments.project is not None or environment.get(PROJECT_DIRECTORY_VARIABLE):
        return None
    current = Path.cwd()
    if is_project(current):
        return None
    return current if inventory_from_config(current) is not None else None


def _serve_fleet(arguments: argparse.Namespace, repository: Path) -> None:
    config = FleetConfig(
        repository=repository,
        schema_directory=Path(arguments.schemas),
        engine_directory=Path(arguments.engine),
        inventory=arguments.inventory,
        operations_directory=Path(arguments.operations),
        decisions_directory=Path(arguments.decisions),
        observed_directory=Path(arguments.observed),
    )
    create_fleet_server(config).run(transport="stdio")


def _serve(arguments: argparse.Namespace, project_directory: Path) -> None:
    config = AgentConfig(
        project_directory=project_directory,
        schema_directory=Path(arguments.schemas),
        engine_directory=Path(arguments.engine),
        inventory_file=Path(arguments.inventory_file),
        observed_directory=Path(arguments.observed),
        service_observed_directory=Path(arguments.service_observed),
        deployments_directory=Path(arguments.deployments),
        releases_directory=Path(arguments.releases),
        artifacts_directory=Path(arguments.artifacts),
        data_migrations_directory=Path(arguments.data_migrations),
        backups_directory=Path(arguments.backups),
        secrets_directory=Path(arguments.secrets_dir),
        environment_directory=Path(arguments.env_dir),
        environment_receipts_directory=Path(arguments.env_receipts),
        proposals_directory=Path(arguments.proposals),
        gateway_ca_path=arguments.gateway_ca,
        gateway_certificate_path=arguments.gateway_cert,
        gateway_key_path=arguments.gateway_key,
    )
    create_server(config).run(transport="stdio")


def run() -> None:
    """Installed console-script entry point."""
    raise SystemExit(main())
