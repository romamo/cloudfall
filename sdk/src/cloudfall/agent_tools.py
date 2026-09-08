"""Typed agent-facing operations behind the MCP boundary.

Every method returns a JSON-serializable envelope instead of raising, so
agents always receive structured results. Read-only evidence tools are
exposed freely; anything that changes servers demands an explicit
confirmation handshake and always produces receipts.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from cloudfall.audit import audit_inventory
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
    build_release_artifact,
    deploy,
    health,
    migrate_data,
    restart,
    rollback,
    run_engine_playbook,
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

if TYPE_CHECKING:
    from cloudfall.validation import ValidatedState

_CODE_INVALID_ARGUMENT = "invalid_argument"


@dataclass(frozen=True, slots=True)
class AgentConfig:
    """Filesystem contract for one agent-facing server instance."""

    state_directory: Path
    schema_directory: Path
    engine_directory: Path
    inventory_file: Path
    observed_directory: Path
    service_observed_directory: Path
    deployments_directory: Path
    releases_directory: Path
    artifacts_directory: Path
    data_migrations_directory: Path = Path("tmp/data-migrations")

    def context(self) -> EngineContext:
        """Return the engine execution context shared by mutating tools."""
        return EngineContext(
            state_directory=self.state_directory,
            schema_directory=self.schema_directory,
            engine_directory=self.engine_directory,
            inventory_file=self.inventory_file,
        )


class AgentToolset:
    """Structured operations offered to AI agents."""

    def __init__(self, config: AgentConfig) -> None:
        """Bind the toolset to one filesystem contract."""
        self._config = config

    def validate(self) -> dict[str, object]:
        """Validate declared state and return the structured summary."""
        try:
            return self._state().as_dict()
        except StateValidationError as error:
            return error.as_dict()

    def inventory(self) -> dict[str, object]:
        """Return the non-secret platform inventory."""
        try:
            state = self._state()
        except StateValidationError as error:
            return error.as_dict()
        return {
            "status": "ok",
            "inventory": PlatformInventory.from_state(state).as_dict(),
        }

    def audit(self) -> dict[str, object]:
        """Compare desired state with collected server observations."""
        try:
            state = self._state()
            observations = load_observations(
                self._config.observed_directory, self._config.schema_directory
            )
        except StateValidationError as error:
            return error.as_dict()
        report = audit_inventory(
            PlatformInventory.from_state(state), observations
        )
        return report.as_dict()

    def services_status(self) -> dict[str, object]:
        """Derive the evidence-based public service lifecycle."""
        try:
            state = self._state()
            observations = load_observations(
                self._config.observed_directory, self._config.schema_directory
            )
        except StateValidationError as error:
            return error.as_dict()
        receipts = (
            load_deployment_receipts(
                self._config.deployments_directory,
                self._config.schema_directory,
            )
            if self._config.deployments_directory.is_dir()
            else DeploymentReceiptSet.empty()
        )
        domain_observations = (
            load_domain_observations(
                self._config.service_observed_directory,
                self._config.schema_directory,
            )
            if self._config.service_observed_directory.is_dir()
            else DomainObservationSet.empty()
        )
        view = build_operations_view(
            PlatformInventory.from_state(state),
            observations,
            receipts,
            domain_observations,
            generated_at=UtcTimestamp.now(),
        )
        return {
            "status": "ok",
            "health": view.health.value,
            "services": [domain.as_dict() for domain in view.domains],
        }

    def component_health(self, component: str) -> dict[str, object]:
        """Probe one component's declared health check on its servers."""
        try:
            component_id = ResourceId.from_boundary(component)
        except (TypeError, ValueError) as error:
            return _invalid_argument(error)
        try:
            return health(self._config.context(), component_id).as_dict()
        except LifecycleError as error:
            return error.as_dict()

    def inspect_servers(self) -> dict[str, object]:
        """Collect read-only server evidence into the observation directory."""
        observed = self._config.observed_directory.resolve()
        try:
            run_engine_playbook(
                self._config.context(),
                "inspect.yml",
                {"cloudfall_inspect_output_directory": str(observed)},
            )
        except LifecycleError as error:
            return error.as_dict()
        return {"status": "ok", "observed": str(observed)}

    def inspect_services(self) -> dict[str, object]:
        """Collect DNS, TLS, origin, and public route evidence."""
        try:
            state = self._state()
        except StateValidationError as error:
            return error.as_dict()
        paths = inspect_domains(
            PlatformInventory.from_state(state),
            self._config.service_observed_directory,
            SocketDomainNetworkClient(),
            observed_at=EvidenceTimestamp.now(),
        )
        return {
            "status": "ok",
            "observations": [str(path) for path in paths],
        }

    def import_render(
        self,
        blueprint: str,
        project: str,
        server: str,
        output_directory: str,
        environment_directory: str,
    ) -> dict[str, object]:
        """Map a Render blueprint onto Cloudfall state fragments."""
        try:
            targets = ImportTargets(
                project_id=ResourceId.from_boundary(project),
                server_id=ResourceId.from_boundary(server),
                state_directory=self._path(output_directory),
                environment_directory=self._path(environment_directory),
            )
        except (TypeError, ValueError) as error:
            return _invalid_argument(error)
        try:
            result = import_render_blueprint(
                self._path(blueprint), targets, self._config.schema_directory
            )
        except (RenderImportError, StateValidationError) as error:
            return error.as_dict()
        return result.as_dict()

    def build_artifact(self, component: str, git_ref: str) -> dict[str, object]:
        """Build one hashed release artifact from the component repository."""
        try:
            component_id = ResourceId.from_boundary(component)
        except (TypeError, ValueError) as error:
            return _invalid_argument(error)
        try:
            return build_release_artifact(
                self._config.context(),
                component_id,
                git_ref,
                self._config.artifacts_directory,
            )
        except LifecycleError as error:
            return error.as_dict()

    def deploy_component(
        self,
        component: str,
        release: str,
        environment_file: str | None = None,
        *,
        confirm: bool = False,
    ) -> dict[str, object]:
        """Deploy one built release behind the engine's health gate."""
        gate = _confirmation_gate(
            confirm,
            "deploy",
            f"deploy release {release} of component {component} to its "
            "declared servers with automatic rollback on a failed health "
            "check",
        )
        if gate is not None:
            return gate
        try:
            component_id = ResourceId.from_boundary(component)
            release_id = ReleaseId.from_boundary(release)
        except (TypeError, ValueError) as error:
            return _invalid_argument(error)
        try:
            result = deploy(
                self._config.context(),
                component_id,
                release_id,
                self._config.artifacts_directory,
                DeployOptions(
                    environment_file=(
                        self._path(environment_file)
                        if environment_file is not None
                        else None
                    ),
                    receipt_directory=self._config.releases_directory,
                ),
            )
        except LifecycleError as error:
            return error.as_dict()
        return result.as_dict()

    def rollback_component(
        self, component: str, release: str, *, confirm: bool = False
    ) -> dict[str, object]:
        """Activate an already-retained release behind the health gate."""
        gate = _confirmation_gate(
            confirm,
            "rollback",
            f"switch component {component} back to retained release "
            f"{release} and restart it",
        )
        if gate is not None:
            return gate
        try:
            component_id = ResourceId.from_boundary(component)
            release_id = ReleaseId.from_boundary(release)
        except (TypeError, ValueError) as error:
            return _invalid_argument(error)
        try:
            result = rollback(self._config.context(), component_id, release_id)
        except LifecycleError as error:
            return error.as_dict()
        return result.as_dict()

    def restart_component(
        self, component: str, *, confirm: bool = False
    ) -> dict[str, object]:
        """Restart one component and require its declared health check."""
        gate = _confirmation_gate(
            confirm,
            "restart",
            f"restart component {component} on its declared servers",
        )
        if gate is not None:
            return gate
        try:
            component_id = ResourceId.from_boundary(component)
        except (TypeError, ValueError) as error:
            return _invalid_argument(error)
        try:
            result = restart(self._config.context(), component_id)
        except LifecycleError as error:
            return error.as_dict()
        return result.as_dict()

    def migrate_database(
        self,
        service: str,
        database: str,
        source_url_file: str,
        *,
        confirm: bool = False,
    ) -> dict[str, object]:
        """Dump an external database and restore it into a declared service."""
        gate = _confirmation_gate(
            confirm,
            "data-migration",
            (
                f"dump the database behind {source_url_file} and restore it "
                f"into declared database {database} on service {service} "
                "with per-table row-count verification"
            ),
        )
        if gate is not None:
            return gate
        try:
            service_id = ResourceId.from_boundary(service)
        except (TypeError, ValueError) as error:
            return _invalid_argument(error)
        try:
            return migrate_data(
                self._config.context(),
                service_id,
                database,
                Path(source_url_file),
                self._config.data_migrations_directory,
            )
        except LifecycleError as error:
            return error.as_dict()

    def converge_baseline(self, *, confirm: bool = False) -> dict[str, object]:
        """Converge every declared server to the managed baseline."""
        return self._converge(
            confirm,
            "converge-baseline",
            "run baseline.yml: bootstrap, UTC, SSH hardening, unattended "
            "upgrades, and the declared firewall on every server",
            "baseline.yml",
            {},
        )

    def converge_services(self, *, confirm: bool = False) -> dict[str, object]:
        """Converge every declared infrastructure service."""
        return self._converge(
            confirm,
            "converge-services",
            "run services.yml: install and configure every declared "
            "infrastructure service",
            "services.yml",
            {},
        )

    def converge_domains(
        self, *, issue_certificates: bool = False, confirm: bool = False
    ) -> dict[str, object]:
        """Converge every declared public domain route."""
        return self._converge(
            confirm,
            "converge-domains",
            "run domains.yml: render every declared domain route"
            + (
                " and issue missing certificates"
                if issue_certificates
                else " without certificate issuance"
            ),
            "domains.yml",
            {
                "cloudfall_domains_issue_certificates": issue_certificates,
                "cloudfall_domains_receipt_directory": str(
                    self._config.deployments_directory.resolve()
                ),
            },
        )

    def migrate(  # noqa: PLR0913 - boundary signature mirrors the CLI.
        self,
        builds: dict[str, str] | None = None,
        releases: dict[str, str] | None = None,
        environment_files: dict[str, str] | None = None,
        data_migrations: dict[str, str] | None = None,
        plan_file: str = "tmp/migrate/plan.json",
        *,
        restart_plan: bool = False,
        confirm: bool = False,
    ) -> dict[str, object]:
        """Preview or execute the resumable end-to-end migration plan."""
        options = MigrateOptions(
            plan_file=self._path(plan_file),
            builds=builds if builds is not None else {},
            releases=releases if releases is not None else {},
            environment_files={
                component: self._path(value)
                for component, value in (
                    environment_files if environment_files is not None else {}
                ).items()
            },
            data_migrations={
                database: self._path(value)
                for database, value in (
                    data_migrations if data_migrations is not None else {}
                ).items()
            },
            execute=confirm,
            restart=restart_plan,
        )
        try:
            result = execute_migration(self._config, options)
        except (MigrateError, StateValidationError) as error:
            return error.as_dict()
        if not confirm:
            result["instruction"] = (
                "review the plan and call this tool again with confirm=true "
                "to execute it; interrupted runs resume automatically"
            )
        return result

    def _converge(
        self,
        confirm: bool,  # noqa: FBT001 - explicit gate flag at the boundary.
        action: str,
        detail: str,
        playbook: str,
        extra_vars: dict[str, object],
    ) -> dict[str, object]:
        gate = _confirmation_gate(confirm, action, detail)
        if gate is not None:
            return gate
        try:
            run_engine_playbook(self._config.context(), playbook, extra_vars)
        except LifecycleError as error:
            return error.as_dict()
        return {"status": "ok", "action": action}

    def _state(self) -> ValidatedState:
        return validate_state(
            self._config.state_directory, self._config.schema_directory
        )

    def _path(self, value: str) -> Path:
        return Path(value)


def _confirmation_gate(
    confirm: bool,  # noqa: FBT001 - explicit gate flag at the boundary.
    action: str,
    detail: str,
) -> dict[str, object] | None:
    if confirm:
        return None
    return {
        "status": "confirmation-required",
        "action": action,
        "wouldRun": detail,
        "instruction": (
            "review the description and call this tool again with "
            "confirm=true to execute"
        ),
    }


def _invalid_argument(error: Exception) -> dict[str, object]:
    return {
        "status": "error",
        "error": {"code": _CODE_INVALID_ARGUMENT, "message": str(error)},
    }
