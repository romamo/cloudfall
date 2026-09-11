"""MCP server exposing the Cloudfall agent toolset over stdio.

Requires the ``cloudfall[mcp]`` extra. Read-only evidence tools carry a
read-only annotation; anything that changes servers is annotated
destructive and demands the toolset's explicit confirmation handshake.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import TYPE_CHECKING

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from cloudfall.agent_tools import AgentConfig, AgentToolset

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    _Registration = tuple[
        Callable[..., str], str, str, ToolAnnotations | None
    ]

_INSTRUCTIONS = """\
Cloudfall manages declarative infrastructure state for dedicated Debian
servers. Read-only tools validate state and derive evidence; destructive
tools change servers and require calling twice: the first call returns a
confirmation-required preview, the second call with confirm=true executes.
Every mutation writes receipts; nothing reports success it cannot prove.
"""


def create_server(config: AgentConfig) -> MCPServer:
    """Create the MCP server and register every agent tool."""
    server = MCPServer(name="cloudfall", instructions=_INSTRUCTIONS)
    toolset = AgentToolset(config)
    registrations = (
        *_evidence_registrations(toolset),
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

    def validate_state() -> str:
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
            validate_state,
            "validate_state",
            "Validate declared platform state and resource references",
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


def _build_registrations(toolset: AgentToolset) -> tuple[_Registration, ...]:
    def import_render(
        blueprint: str,
        project: str,
        server_id: str,
        output_directory: str = "tmp/import/state",
        environment_directory: str = "tmp/import/env",
    ) -> str:
        return _dump(
            toolset.import_render(
                blueprint,
                project,
                server_id,
                output_directory,
                environment_directory,
            )
        )

    def import_render_api(
        api_key_file: str,
        project: str,
        server_id: str,
        output_directory: str = "tmp/import/state",
        environment_directory: str = "tmp/import/env",
    ) -> str:
        return _dump(
            toolset.import_render_api(
                api_key_file,
                project,
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
            "Map a render.yaml blueprint onto Cloudfall state fragments "
            "with a structured gap report",
            None,
        ),
        (
            import_render_api,
            "import_render_api",
            "Map a live Render workspace onto Cloudfall state fragments "
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
        return _dump(
            toolset.rollback_component(component, release, confirm=confirm)
        )

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
            "Restart one component behind its health check; requires "
            "confirm=true",
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
            "Converge every server to the managed baseline; requires "
            "confirm=true",
            destructive,
        ),
        (
            converge_services,
            "converge_services",
            "Converge every declared infrastructure service; requires "
            "confirm=true",
            destructive,
        ),
        (
            converge_domains,
            "converge_domains",
            "Converge every declared public domain route; requires "
            "confirm=true",
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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cloudfall-mcp",
        description=(
            "Expose the Cloudfall agent toolset over MCP stdio. Read-only"
            " evidence tools are available freely; every server-changing"
            " tool requires an explicit two-step confirmation handshake."
        ),
    )
    parser.add_argument(
        "--state",
        type=Path,
        required=True,
        help="declarative state directory to operate on",
    )
    parser.add_argument(
        "--schemas",
        type=Path,
        default=Path("state/schemas/v1"),
        help="JSON Schema directory used to validate state (default:"
        " %(default)s)",
    )
    parser.add_argument(
        "--engine",
        type=Path,
        default=Path("engine"),
        help="engine module directory containing the Ansible playbooks"
        " (default: %(default)s)",
    )
    parser.add_argument(
        "--inventory-file",
        type=Path,
        default=Path("tmp/ansible-inventory.json"),
        help="path where the rendered Ansible inventory is written"
        " (default: %(default)s)",
    )
    parser.add_argument(
        "--observed",
        type=Path,
        default=Path("tmp/observed"),
        help="directory holding read-only server observation snapshots"
        " (default: %(default)s)",
    )
    parser.add_argument(
        "--service-observed",
        type=Path,
        default=Path("tmp/observed-services"),
        help="directory holding DNS, TLS, origin, and public route evidence"
        " (default: %(default)s)",
    )
    parser.add_argument(
        "--deployments",
        type=Path,
        default=Path("tmp/deployments"),
        help="directory holding domain deployment receipts (default:"
        " %(default)s)",
    )
    parser.add_argument(
        "--releases",
        type=Path,
        default=Path("tmp/releases"),
        help="directory holding component release receipts (default:"
        " %(default)s)",
    )
    parser.add_argument(
        "--artifacts",
        type=Path,
        default=Path("tmp/artifacts"),
        help="directory holding built release artifacts (default:"
        " %(default)s)",
    )
    parser.add_argument(
        "--data-migrations",
        type=Path,
        default=Path("tmp/data-migrations"),
        help="directory holding data-migration receipts (default:"
        " %(default)s)",
    )
    parser.add_argument(
        "--secrets-dir",
        type=Path,
        default=Path("secrets"),
        help="sops-encrypted secrets directory (default: %(default)s)",
    )
    parser.add_argument(
        "--env-dir",
        type=Path,
        default=Path("tmp/env"),
        help="rendered environment file directory (default: %(default)s)",
    )
    parser.add_argument(
        "--backups",
        type=Path,
        default=Path("tmp/backups"),
        help="directory holding backup operation receipts (default:"
        " %(default)s)",
    )
    parser.add_argument(
        "--proposals",
        type=Path,
        default=Path("tmp/operator/proposals"),
        help="directory holding operator proposal receipts (default:"
        " %(default)s)",
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
    arguments = _parser().parse_args(argv)
    config = AgentConfig(
        state_directory=Path(arguments.state),
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
        proposals_directory=Path(arguments.proposals),
        gateway_ca_path=arguments.gateway_ca,
        gateway_certificate_path=arguments.gateway_cert,
        gateway_key_path=arguments.gateway_key,
    )
    create_server(config).run(transport="stdio")
    return 0


def run() -> None:
    """Installed console-script entry point."""
    raise SystemExit(main())
