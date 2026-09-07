"""Agent-facing component lifecycle operations.

The SDK orchestrates deployments through the engine's stable process
boundaries: the ``cloudfall_engine`` command-line contract for inventory
rendering and the engine's playbook contract for execution. It never
imports Ansible or engine internals.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from jsonschema.exceptions import ValidationError

from cloudfall.inventory import PlatformInventory
from cloudfall.validation import SchemaCatalog, validate_state

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from cloudfall.domain import ReleaseId, ResourceId
    from cloudfall.inventory import ComponentInventory

_ARTIFACT_SCHEMA = "artifact.schema.json"
_STEP_TIMEOUT_SECONDS = 3600
_OUTPUT_TAIL_CHARACTERS = 2000
_ERROR_COMPONENT_MISSING = "lifecycle_component_missing"
_ERROR_ARTIFACT_MISSING = "lifecycle_artifact_missing"
_ERROR_ARTIFACT_METADATA = "lifecycle_artifact_metadata_invalid"
_ERROR_ARTIFACT_IDENTITY = "lifecycle_artifact_identity_mismatch"
_ERROR_ARTIFACT_DIGEST = "lifecycle_artifact_digest_mismatch"
_ERROR_ANSIBLE_MISSING = "lifecycle_ansible_missing"
_ERROR_ENGINE_MISSING = "lifecycle_engine_missing"
_ERROR_EXECUTION_FAILED = "lifecycle_execution_failed"
_ERROR_RECEIPT_MISSING = "lifecycle_receipt_missing"


class LifecycleError(RuntimeError):
    """Fail-fast lifecycle error with a stable machine-readable code."""

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
class ExecutionStep:
    """One subprocess invocation with explicit environment additions."""

    description: str
    argv: tuple[str, ...]
    environment: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    """Ordered subprocess steps for one lifecycle action."""

    action: str
    component_id: ResourceId
    release: ReleaseId | None
    steps: tuple[ExecutionStep, ...]


@dataclass(frozen=True, slots=True)
class DeployOptions:
    """Optional deployment inputs."""

    environment_file: Path | None = None
    receipt_directory: Path | None = None


@dataclass(frozen=True, slots=True)
class VerifiedArtifact:
    """A release artifact whose metadata and digest have been verified."""

    archive_path: Path
    metadata_path: Path
    sha256: str


@dataclass(frozen=True, slots=True)
class LifecycleResult:
    """Structured outcome of one lifecycle action."""

    action: str
    component_id: ResourceId
    release: ReleaseId | None
    servers: tuple[ResourceId, ...]
    healthy: bool
    detail: str | None
    receipt: Path | None

    def as_dict(self) -> dict[str, object]:
        """Serialize the result for agent consumers."""
        result: dict[str, object] = {
            "status": "ok" if self.healthy else "unhealthy",
            "action": self.action,
            "component": self.component_id.value,
            "servers": [server.value for server in self.servers],
            "healthy": self.healthy,
        }
        if self.release is not None:
            result["release"] = self.release.value
        if self.detail is not None:
            result["detail"] = self.detail
        if self.receipt is not None:
            result["receipt"] = str(self.receipt)
        return result


@dataclass(frozen=True, slots=True)
class EngineContext:
    """Filesystem contract shared by every lifecycle action."""

    state_directory: Path
    schema_directory: Path
    engine_directory: Path
    inventory_file: Path

    def playbook(self, name: str) -> Path:
        """Return the path of one engine playbook, requiring it to exist."""
        path = self.engine_directory / "ansible" / "playbooks" / name
        if not path.is_file():
            detail = f"engine playbook does not exist: {path}"
            raise LifecycleError(_ERROR_ENGINE_MISSING, detail)
        return path

    def ansible_environment(self) -> dict[str, str]:
        """Return environment additions for engine playbook execution."""
        return {
            "ANSIBLE_CONFIG": str(
                self.engine_directory / "ansible" / "ansible.cfg"
            )
        }


def verify_artifact(
    artifact_directory: Path,
    component_id: ResourceId,
    release: ReleaseId,
    schema_directory: Path,
) -> VerifiedArtifact:
    """Verify a built artifact's metadata, identity, and digest."""
    component_directory = artifact_directory / component_id.value
    archive_path = component_directory / f"{release.value}.tar.gz"
    metadata_path = component_directory / f"{release.value}.json"
    if not archive_path.is_file() or not metadata_path.is_file():
        detail = (
            "artifact archive or metadata does not exist for "
            f"{component_id}/{release.value} under {artifact_directory}"
        )
        raise LifecycleError(_ERROR_ARTIFACT_MISSING, detail)

    raw = cast(
        "object", json.loads(metadata_path.read_text(encoding="utf-8"))
    )
    if not isinstance(raw, dict) or not all(
        isinstance(key, str) for key in raw
    ):
        detail = f"artifact metadata is not an object: {metadata_path}"
        raise LifecycleError(_ERROR_ARTIFACT_METADATA, detail)
    metadata = cast("Mapping[str, object]", raw)
    try:
        SchemaCatalog(schema_directory).validate_named(
            _ARTIFACT_SCHEMA, metadata
        )
    except ValidationError as error:
        detail = f"artifact metadata is invalid: {error.message}"
        raise LifecycleError(_ERROR_ARTIFACT_METADATA, detail) from error

    spec = cast("Mapping[str, object]", metadata["spec"])
    if (
        spec.get("component") != component_id.value
        or spec.get("release") != release.value
    ):
        detail = (
            "artifact metadata identifies "
            f"{spec.get('component')}/{spec.get('release')}, not "
            f"{component_id}/{release.value}"
        )
        raise LifecycleError(_ERROR_ARTIFACT_IDENTITY, detail)

    declared_digest = spec.get("archiveSha256")
    actual_digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    if actual_digest != declared_digest:
        detail = (
            f"artifact digest {actual_digest} does not match declared "
            f"metadata digest {declared_digest}"
        )
        raise LifecycleError(_ERROR_ARTIFACT_DIGEST, detail)
    return VerifiedArtifact(
        archive_path=archive_path,
        metadata_path=metadata_path,
        sha256=actual_digest,
    )


def plan_deploy(
    context: EngineContext,
    component_id: ResourceId,
    release: ReleaseId,
    artifact: VerifiedArtifact,
    options: DeployOptions,
) -> ExecutionPlan:
    """Compose the subprocess steps for one deployment."""
    extra_vars: dict[str, object] = {
        "cloudfall_deploy_component_id": component_id.value,
        "cloudfall_deploy_release": release.value,
        "cloudfall_deploy_artifact_archive": str(artifact.archive_path),
        "cloudfall_deploy_artifact_sha256": artifact.sha256,
    }
    if options.environment_file is not None:
        extra_vars["cloudfall_deploy_environment_file"] = str(
            options.environment_file
        )
    if options.receipt_directory is not None:
        extra_vars["cloudfall_deploy_receipt_directory"] = str(
            options.receipt_directory
        )
    return ExecutionPlan(
        action="deploy",
        component_id=component_id,
        release=release,
        steps=(
            _render_inventory_step(context),
            _playbook_step(context, "deploy.yml", extra_vars),
        ),
    )


def plan_rollback(
    context: EngineContext,
    component_id: ResourceId,
    release: ReleaseId,
) -> ExecutionPlan:
    """Compose the subprocess steps for one explicit rollback."""
    extra_vars: dict[str, object] = {
        "cloudfall_deploy_component_id": component_id.value,
        "cloudfall_deploy_release": release.value,
    }
    return ExecutionPlan(
        action="rollback",
        component_id=component_id,
        release=release,
        steps=(
            _render_inventory_step(context),
            _playbook_step(context, "rollback.yml", extra_vars),
        ),
    )


def plan_restart(
    context: EngineContext, component_id: ResourceId
) -> ExecutionPlan:
    """Compose the subprocess steps for one health-gated restart."""
    extra_vars: dict[str, object] = {
        "cloudfall_deploy_component_id": component_id.value,
    }
    return ExecutionPlan(
        action="restart",
        component_id=component_id,
        release=None,
        steps=(
            _render_inventory_step(context),
            _playbook_step(context, "restart.yml", extra_vars),
        ),
    )


def plan_health(
    context: EngineContext, component_id: ResourceId
) -> ExecutionPlan:
    """Compose the subprocess steps for one read-only health check."""
    extra_vars: dict[str, object] = {
        "cloudfall_deploy_component_id": component_id.value,
    }
    return ExecutionPlan(
        action="health",
        component_id=component_id,
        release=None,
        steps=(
            _render_inventory_step(context),
            _playbook_step(context, "health.yml", extra_vars),
        ),
    )


def execute_plan(plan: ExecutionPlan) -> None:
    """Run every step of a plan, failing fast on the first error."""
    _execute_steps(plan.steps)


def run_engine_playbook(
    context: EngineContext,
    playbook_name: str,
    extra_vars: Mapping[str, object],
) -> None:
    """Render inventory and run one engine playbook contract."""
    _execute_steps(
        (
            _render_inventory_step(context),
            _playbook_step(context, playbook_name, extra_vars),
        )
    )


def build_release_artifact(
    context: EngineContext,
    component_id: ResourceId,
    git_ref: str,
    artifact_directory: Path,
) -> dict[str, object]:
    """Build one release artifact through the engine's CLI contract."""
    argv = (
        sys.executable,
        "-m",
        "cloudfall_engine",
        "artifact",
        "build",
        str(context.state_directory),
        component_id.value,
        "--ref",
        git_ref,
        "--schemas",
        str(context.schema_directory),
        "--output-dir",
        str(artifact_directory),
    )
    step = ExecutionStep(
        description="build release artifact",
        argv=argv,
        environment={},
    )
    output = _execute_step_with_output(step)
    parsed = cast("object", json.loads(output))
    if not isinstance(parsed, dict) or not all(
        isinstance(key, str) for key in parsed
    ):
        detail = "artifact builder returned a non-object payload"
        raise LifecycleError(_ERROR_EXECUTION_FAILED, detail)
    return cast("dict[str, object]", parsed)


def _execute_steps(steps: tuple[ExecutionStep, ...]) -> None:
    for step in steps:
        _execute_step_with_output(step)


def _execute_step_with_output(step: ExecutionStep) -> str:
    environment = {**os.environ, **step.environment}
    try:
        completed = subprocess.run(  # noqa: S603 - argv from typed values.
            list(step.argv),
            check=True,
            capture_output=True,
            text=True,
            timeout=_STEP_TIMEOUT_SECONDS,
            env=environment,
        )
    except subprocess.CalledProcessError as error:
        tail = f"{error.stdout}\n{error.stderr}"[-_OUTPUT_TAIL_CHARACTERS:]
        detail = f"{step.description} failed: {tail.strip()}"
        raise LifecycleError(_ERROR_EXECUTION_FAILED, detail) from error
    except subprocess.TimeoutExpired as error:
        detail = (
            f"{step.description} exceeded {_STEP_TIMEOUT_SECONDS} seconds"
        )
        raise LifecycleError(_ERROR_EXECUTION_FAILED, detail) from error
    return completed.stdout


def deploy(
    context: EngineContext,
    component_id: ResourceId,
    release: ReleaseId,
    artifact_directory: Path,
    options: DeployOptions | None = None,
) -> LifecycleResult:
    """Deploy one verified release behind the engine's health gate."""
    resolved_options = options if options is not None else DeployOptions()
    component = _component(context, component_id)
    artifact = verify_artifact(
        artifact_directory, component_id, release, context.schema_directory
    )
    plan = plan_deploy(
        context,
        component_id,
        release,
        artifact,
        resolved_options,
    )
    execute_plan(plan)
    receipt = None
    if resolved_options.receipt_directory is not None:
        receipt = (
            resolved_options.receipt_directory / f"{component_id.value}.json"
        )
        if not receipt.is_file():
            detail = (
                "deployment reported success but wrote no release receipt: "
                f"{receipt}"
            )
            raise LifecycleError(_ERROR_RECEIPT_MISSING, detail)
    return LifecycleResult(
        action="deploy",
        component_id=component_id,
        release=release,
        servers=component.server_ids,
        healthy=True,
        detail=None,
        receipt=receipt,
    )


def rollback(
    context: EngineContext,
    component_id: ResourceId,
    release: ReleaseId,
) -> LifecycleResult:
    """Switch one component back to an existing release, health gated."""
    component = _component(context, component_id)
    plan = plan_rollback(context, component_id, release)
    execute_plan(plan)
    return LifecycleResult(
        action="rollback",
        component_id=component_id,
        release=release,
        servers=component.server_ids,
        healthy=True,
        detail=None,
        receipt=None,
    )


def restart(
    context: EngineContext, component_id: ResourceId
) -> LifecycleResult:
    """Restart one component and require its declared health check."""
    component = _component(context, component_id)
    plan = plan_restart(context, component_id)
    execute_plan(plan)
    return LifecycleResult(
        action="restart",
        component_id=component_id,
        release=None,
        servers=component.server_ids,
        healthy=True,
        detail=None,
        receipt=None,
    )


def health(
    context: EngineContext, component_id: ResourceId
) -> LifecycleResult:
    """Probe one component's declared health check on every server."""
    component = _component(context, component_id)
    plan = plan_health(context, component_id)
    healthy = True
    detail: str | None = None
    try:
        execute_plan(plan)
    except LifecycleError as error:
        if error.code != _ERROR_EXECUTION_FAILED:
            raise
        healthy = False
        detail = error.detail
    return LifecycleResult(
        action="health",
        component_id=component_id,
        release=None,
        servers=component.server_ids,
        healthy=healthy,
        detail=detail,
        receipt=None,
    )


def _component(
    context: EngineContext, component_id: ResourceId
) -> ComponentInventory:
    state = validate_state(context.state_directory, context.schema_directory)
    inventory = PlatformInventory.from_state(state)
    component = next(
        (
            candidate
            for candidate in inventory.components
            if candidate.resource_id == component_id
        ),
        None,
    )
    if component is None:
        detail = f"component does not exist: {component_id}"
        raise LifecycleError(_ERROR_COMPONENT_MISSING, detail)
    return component


def _render_inventory_step(context: EngineContext) -> ExecutionStep:
    return ExecutionStep(
        description="render Ansible inventory",
        argv=(
            sys.executable,
            "-m",
            "cloudfall_engine",
            "inventory",
            "render",
            str(context.state_directory),
            "--schemas",
            str(context.schema_directory),
            "--output",
            str(context.inventory_file),
        ),
        environment={},
    )


def _playbook_step(
    context: EngineContext,
    playbook_name: str,
    extra_vars: Mapping[str, object],
) -> ExecutionStep:
    binary = shutil.which("ansible-playbook")
    if binary is None:
        detail = "ansible-playbook is not installed on this controller"
        raise LifecycleError(_ERROR_ANSIBLE_MISSING, detail)
    playbook = context.playbook(playbook_name)
    return ExecutionStep(
        description=f"run {playbook_name}",
        argv=(
            binary,
            "--inventory",
            str(context.inventory_file),
            "--extra-vars",
            json.dumps(dict(extra_vars), sort_keys=True),
            str(playbook),
        ),
        environment=context.ansible_environment(),
    )
