"""The agent's surface on a brownfield repository.

The loop is fixed: observe, decide, preview, approve, run, verify. An
agent reaches the first three steps and the last two of them only through
what the team declared, and it does not reach the approval at all. There
is no tool here that changes a host: proposing runs check mode, and the
approval that follows is a command a person runs.

The tool list is the catalog. A playbook the team has not declared as an
operation is not reachable, which is the point of having a catalog rather
than a shell.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from cloudfall.ansible_api import InventorySource, inventory_from_config, read_inventory
from cloudfall.ansible_reader import read_fleet
from cloudfall.audit import audit_inventory
from cloudfall.catalog import CATALOG_DIRECTORY, load_catalog
from cloudfall.decision import (
    DECISION_DIRECTORY,
    DecisionStore,
    ProposalRequest,
    Targets,
    propose,
)
from cloudfall.domain import ResourceId
from cloudfall.inventory import PlatformInventory
from cloudfall.observation import load_observations
from cloudfall.observe import ObservationRequest, collect_observations
from cloudfall.validation import SchemaCatalog

if TYPE_CHECKING:
    from collections.abc import Mapping

    from cloudfall.catalog import Operation, OperationCatalog

APPROVAL_COMMAND = "cloudfall operations approve"
"""The command a person runs; no tool here can stand in for it."""

_ERROR_INVENTORY_UNDECLARED = "fleet_inventory_undeclared"


@dataclass(frozen=True, slots=True)
class FleetConfig:
    """Filesystem contract for one agent-facing brownfield repository."""

    repository: Path
    schema_directory: Path
    engine_directory: Path
    inventory: Path | None = None
    operations_directory: Path = Path(CATALOG_DIRECTORY)
    decisions_directory: Path = Path(DECISION_DIRECTORY)
    observed_directory: Path = Path("tmp/observed")

    def source(self) -> InventorySource:
        """Return the inventory to read, as the team's own config names it."""
        if self.inventory is not None:
            return InventorySource(self.repository / self.inventory)
        declared = inventory_from_config(self.repository)
        if declared is None:
            message = (
                f"{self.repository} names no inventory: add an "
                "ansible.cfg with an inventory setting, or pass --inventory"
            )
            raise FleetToolError(_ERROR_INVENTORY_UNDECLARED, message)
        return declared

    def observations(self) -> Path:
        """Return the snapshot directory inside the repository."""
        return self.repository / self.observed_directory


class FleetToolError(RuntimeError):
    """Fail-fast agent surface error with a stable machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        """Record the failure code and human-readable detail."""
        self.code = code
        self.detail = message
        super().__init__(f"{code}: {message}")

    def as_dict(self) -> dict[str, object]:
        """Serialize the error envelope for system boundaries."""
        return {
            "status": "error",
            "error": {"code": self.code, "message": self.detail},
        }


@dataclass(frozen=True, slots=True)
class FleetToolset:
    """Every tool an agent may reach on a brownfield repository."""

    config: FleetConfig

    def catalog(self) -> OperationCatalog:
        """Return the declared operations: the agent's whole tool list."""
        return load_catalog(
            self.config.repository,
            self.config.schema_directory,
            self.config.repository / self.config.operations_directory,
        )

    def operations(self) -> dict[str, object]:
        """List what the team has declared an agent may run."""
        return self.catalog().as_dict()

    def operation(self, operation_id: str) -> dict[str, object]:
        """Show one declared operation with its inputs and verify step."""
        operation = self.catalog().get(ResourceId.from_boundary(operation_id))
        return {"status": "ok", "operation": operation.as_dict()}

    def fleet(self) -> dict[str, object]:
        """Read the fleet from the team's inventory."""
        read = read_fleet(
            read_inventory(self.config.source()), self.config.schema_directory
        )
        return {
            "status": "ok",
            "ansible": read.as_dict(),
            "inventory": PlatformInventory.from_state(read.config).as_dict(),
        }

    def observe(self) -> dict[str, object]:
        """Collect one read-only snapshot per declared server."""
        read = read_fleet(
            read_inventory(self.config.source()), self.config.schema_directory
        )
        inventory = PlatformInventory.from_state(read.config)
        request = ObservationRequest(
            inventory_sources=(self.config.source().value,),
            output_directory=self.config.observations().resolve(),
            engine_directory=self.config.engine_directory,
            configuration=_configuration(self.config.repository),
        )
        result = collect_observations(
            inventory,
            request,
            self.config.repository / "tmp" / "cloudfall",
        )
        return result.as_dict()

    def audit(self) -> dict[str, object]:
        """Compare the declared fleet with the snapshots on disk."""
        read = read_fleet(
            read_inventory(self.config.source()), self.config.schema_directory
        )
        observations = load_observations(
            self.config.observations(), self.config.schema_directory
        )
        report = audit_inventory(
            PlatformInventory.from_state(read.config), observations
        )
        return report.as_dict()

    def propose(
        self,
        operation_id: str,
        target: str | None = None,
        inputs: Mapping[str, str] | None = None,
    ) -> dict[str, object]:
        """Run one operation in check mode and record what it would do.

        Nothing changes: the record this leaves is what a person approves,
        with the command to do it.
        """
        operation = self.catalog().get(ResourceId.from_boundary(operation_id))
        request = ProposalRequest(
            operation=operation,
            targets=Targets(
                scope=operation.targets,
                pattern=target or None,
            ),
            inputs=dict(inputs) if inputs else {},
            repository=self.config.repository,
            observations=self.config.observations(),
        )
        decision = propose(request, self.store())
        payload = decision.as_dict()
        payload["approval"] = {
            "required": True,
            "command": (
                f"{APPROVAL_COMMAND} {decision.decision_id.value} --yes"
            ),
            "note": (
                "a person approves this; no tool on this server runs it"
            ),
        }
        return payload

    def decisions(self) -> dict[str, object]:
        """List what was proposed, what check mode showed, and who approved."""
        store = self.store()
        return {
            "status": "ok",
            "directory": str(store.directory),
            "decisions": [decision.as_document() for decision in store.list()],
        }

    def store(self) -> DecisionStore:
        """Return the decision record store for this repository."""
        return DecisionStore(
            directory=self.config.repository / self.config.decisions_directory,
            catalog=SchemaCatalog(self.config.schema_directory),
        )


def operation_tool_name(operation: Operation) -> str:
    """Return the MCP tool name one declared operation is exposed under."""
    return f"operation_{operation.operation_id.value.replace('-', '_')}"


def operation_tool_description(operation: Operation) -> str:
    """Describe one operation the way a client's tool list should read it."""
    lines = [
        operation.description
        if operation.description is not None
        else f"Run the {operation.operation_id} operation",
        "",
        f"Risk: {operation.risk.value}. Targets: {operation.targets.value}.",
        "Calling this runs check mode only and records what it would change; "
        f"a person approves the result with `{APPROVAL_COMMAND}`.",
    ]
    if operation.inputs:
        declared = ", ".join(
            f"{declared_input.name}: {declared_input.type.value}"
            + ("" if declared_input.required else " (optional)")
            for declared_input in operation.inputs
        )
        lines.append(f"Inputs: {declared}.")
    if operation.preconditions:
        lines.append(
            "Preconditions: "
            + ", ".join(condition.value for condition in operation.preconditions)
            + "."
        )
    if operation.verify is not None:
        lines.append(f"Verified by: {operation.verify.playbook}.")
    return "\n".join(lines)


def _configuration(repository: Path) -> Path | None:
    candidate = repository / "ansible.cfg"
    return candidate if candidate.is_file() else None
