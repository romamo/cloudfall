"""Alert-driven operator loop: watch, diagnose, propose, execute, verify.

The operator is deliberately deterministic. It never invents actions: every
proposal maps a declared alert onto an existing engine entry point, carries
the evidence that justifies it, and waits for an explicit approval before
anything mutates. Outcomes are verified against the same alert evidence the
proposal came from and every state change lands in a schema-validated
receipt.
"""

from __future__ import annotations

import hashlib
import json
import ssl
import time
import urllib.request
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, cast

from cloudfall.domain import AlertSeverity, ResourceId
from cloudfall.lifecycle import LifecycleError, run_engine_playbook

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

    from cloudfall.inventory import PlatformInventory
    from cloudfall.lifecycle import EngineContext
    from cloudfall.validation import SchemaCatalog

_PROPOSAL_SCHEMA = "operator-proposal.schema.json"
_FINGERPRINT_LENGTH = 16
_ERROR_FEED_UNREACHABLE = "operator_feed_unreachable"
_ERROR_FEED_INVALID = "operator_feed_invalid"
_ERROR_PROPOSAL_EXISTS = "operator_proposal_exists"
_ERROR_PROPOSAL_MISSING = "operator_proposal_missing"
_ERROR_PROPOSAL_NOT_OPEN = "operator_proposal_not_open"
_ERROR_OPERATION_UNSUPPORTED = "operator_operation_unsupported"
_ERROR_GATEWAY_UNDECLARED = "operator_gateway_undeclared"


class OperatorError(RuntimeError):
    """Structured operator failure suitable for CLI and agent consumers."""

    def __init__(self, code: str, message: str) -> None:
        """Capture a stable error code alongside the human message."""
        super().__init__(message)
        self.code = code
        self.message = message

    def as_dict(self) -> dict[str, object]:
        """Serialize the failure for structured output."""
        return {"code": self.code, "message": self.message}


class ProposalStatus(StrEnum):
    """Receipted lifecycle of one operator proposal."""

    PROPOSED = "proposed"
    EXECUTED = "executed"
    VERIFIED = "verified"
    FAILED = "failed"


class OperationKind(StrEnum):
    """Existing engine entry points the operator may propose."""

    CONVERGE_SERVICES = "converge-services"


_BLOCKING_STATUSES = frozenset(
    {ProposalStatus.PROPOSED, ProposalStatus.EXECUTED, ProposalStatus.FAILED}
)


@dataclass(frozen=True, slots=True)
class OperatorAlert:
    """One firing declared alert with the labels the operator acts on."""

    name: str
    cloudfall_rule: ResourceId
    severity: AlertSeverity
    environment: ResourceId
    server: ResourceId
    service: ResourceId
    active_at: str
    fingerprint: str

    def as_dict(self) -> dict[str, object]:
        """Serialize the alert for the proposal receipt."""
        return {
            "name": self.name,
            "cloudfallRule": self.cloudfall_rule.value,
            "severity": self.severity.value,
            "environment": self.environment.value,
            "server": self.server.value,
            "service": self.service.value,
            "activeAt": self.active_at,
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True, slots=True)
class ProposalOutcome:
    """What actually happened after an approval."""

    executed_at: str
    verified_at: str | None
    result: str
    detail: str

    def as_dict(self) -> dict[str, object]:
        """Serialize the outcome for the proposal receipt."""
        return {
            "executedAt": self.executed_at,
            "verifiedAt": self.verified_at,
            "result": self.result,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class OperatorProposal:
    """One evidence-backed remediation proposal."""

    resource_id: ResourceId
    created_at: str
    status: ProposalStatus
    alert: OperatorAlert
    diagnosis_summary: str
    evidence: tuple[str, ...]
    operation_kind: OperationKind
    command: tuple[str, ...]
    outcome: ProposalOutcome | None

    def as_document(self) -> dict[str, object]:
        """Serialize the proposal as a schema-valid receipt document."""
        spec: dict[str, object] = {
            "createdAt": self.created_at,
            "status": self.status.value,
            "alert": self.alert.as_dict(),
            "diagnosis": {
                "summary": self.diagnosis_summary,
                "evidence": list(self.evidence),
            },
            "operation": {
                "kind": self.operation_kind.value,
                "server": self.alert.server.value,
                "service": self.alert.service.value,
                "command": list(self.command),
            },
        }
        if self.outcome is not None:
            spec["outcome"] = self.outcome.as_dict()
        return {
            "apiVersion": "cloudfall/v1",
            "kind": "OperatorProposal",
            "metadata": {
                "id": self.resource_id.value,
                "description": self.diagnosis_summary[:500],
            },
            "spec": spec,
        }


class AlertFeed(Protocol):
    """Source of firing declared alerts."""

    def fetch(self) -> tuple[OperatorAlert, ...]:
        """Return the currently firing actionable alerts."""
        ...


@dataclass(frozen=True, slots=True)
class GatewayAlertFeed:
    """mTLS client for the logging gateway's read-only alerts route."""

    url: str
    ca_path: Path
    certificate_path: Path
    key_path: Path
    timeout_seconds: float = 10.0

    def fetch(self) -> tuple[OperatorAlert, ...]:
        """Fetch firing alerts through the gateway."""
        context = ssl.create_default_context(cafile=str(self.ca_path))
        context.load_cert_chain(
            certfile=str(self.certificate_path), keyfile=str(self.key_path)
        )
        request = urllib.request.Request(self.url)  # noqa: S310 - declared https gateway
        try:
            with urllib.request.urlopen(  # noqa: S310 - declared https gateway
                request, timeout=self.timeout_seconds, context=context
            ) as response:
                body = response.read()
        except OSError as error:
            message = f"alert feed unreachable: {self.url}: {error}"
            raise OperatorError(_ERROR_FEED_UNREACHABLE, message) from error
        return parse_prometheus_alerts(body.decode("utf-8"))


def parse_prometheus_alerts(raw: str) -> tuple[OperatorAlert, ...]:
    """Parse a Prometheus alerts API payload into actionable alerts."""
    try:
        payload = cast("object", json.loads(raw))
    except json.JSONDecodeError as error:
        message = f"alert feed returned invalid JSON: {error.msg}"
        raise OperatorError(_ERROR_FEED_INVALID, message) from error
    if not isinstance(payload, dict) or payload.get("status") != "success":
        message = "alert feed returned a non-success payload"
        raise OperatorError(_ERROR_FEED_INVALID, message)
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("alerts"), list):
        message = "alert feed payload is missing data.alerts"
        raise OperatorError(_ERROR_FEED_INVALID, message)
    alerts: list[OperatorAlert] = []
    for item in cast("list[object]", data["alerts"]):
        if not isinstance(item, dict):
            continue
        alert = _actionable_alert(cast("Mapping[str, object]", item))
        if alert is not None:
            alerts.append(alert)
    return tuple(alerts)


def _actionable_alert(item: Mapping[str, object]) -> OperatorAlert | None:
    if item.get("state") != "firing":
        return None
    labels = item.get("labels")
    if not isinstance(labels, dict):
        return None
    required = ("alertname", "cloudfall_rule", "severity", "environment",
                "server", "service")
    if not all(isinstance(labels.get(key), str) for key in required):
        return None
    active_at = item.get("activeAt")
    if not isinstance(active_at, str):
        return None
    return OperatorAlert(
        name=cast("str", labels["alertname"]),
        cloudfall_rule=ResourceId.from_boundary(labels["cloudfall_rule"]),
        severity=AlertSeverity.from_boundary(labels["severity"]),
        environment=ResourceId.from_boundary(labels["environment"]),
        server=ResourceId.from_boundary(labels["server"]),
        service=ResourceId.from_boundary(labels["service"]),
        active_at=active_at,
        fingerprint=alert_fingerprint(cast("Mapping[str, object]", labels)),
    )


def alert_fingerprint(labels: Mapping[str, object]) -> str:
    """Return a stable short fingerprint over an alert's labels."""
    canonical = json.dumps(
        {key: labels[key] for key in sorted(labels)}, separators=(",", ":")
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return digest[:_FINGERPRINT_LENGTH]


@dataclass(frozen=True, slots=True)
class SkippedAlert:
    """One firing alert the operator refused to act on, with the reason."""

    name: str
    reason: str

    def as_dict(self) -> dict[str, object]:
        """Serialize for structured run output."""
        return {"name": self.name, "reason": self.reason}


def propose(
    alert: OperatorAlert,
    inventory: PlatformInventory,
    now: str,
) -> OperatorProposal | SkippedAlert:
    """Map a firing alert onto a declared remediation, or refuse."""
    declared = next(
        (
            service
            for service in inventory.services
            if service.resource_id == alert.service
            and service.server_id == alert.server
        ),
        None,
    )
    if declared is None:
        reason = (
            f"service {alert.service.value} is not declared on "
            f"server {alert.server.value}"
        )
        return SkippedAlert(name=alert.name, reason=reason)
    summary = (
        f"Declared alert {alert.cloudfall_rule.value} is firing: service "
        f"{alert.service.value} on server {alert.server.value} stopped "
        "satisfying its rule; converging declared services restores the "
        "declared state"
    )
    evidence = (
        f"alert {alert.name} firing since {alert.active_at}",
        f"labels: severity={alert.severity.value} "
        f"environment={alert.environment.value} "
        f"server={alert.server.value} service={alert.service.value}",
        f"fingerprint {alert.fingerprint}",
    )
    identifier = _proposal_identifier(alert, now)
    return OperatorProposal(
        resource_id=identifier,
        created_at=now,
        status=ProposalStatus.PROPOSED,
        alert=alert,
        diagnosis_summary=summary,
        evidence=evidence,
        operation_kind=OperationKind.CONVERGE_SERVICES,
        command=("cloudfall-engine", "playbook", "services.yml"),
        outcome=None,
    )


def _proposal_identifier(alert: OperatorAlert, now: str) -> ResourceId:
    stamp = (
        now.replace("-", "").replace(":", "").replace("+0000", "")
        .replace("t", "").replace("T", "").split(".")[0].lower()
    )
    return ResourceId.from_boundary(f"op-{stamp}-{alert.fingerprint}")


@dataclass(frozen=True, slots=True)
class ProposalStore:
    """Schema-validated proposal receipts in one directory."""

    directory: Path
    catalog: SchemaCatalog

    def save(self, proposal: OperatorProposal) -> Path:
        """Persist a new proposal, refusing to overwrite an existing one."""
        path = self._path(proposal.resource_id)
        if path.exists():
            message = f"proposal already exists: {path}"
            raise OperatorError(_ERROR_PROPOSAL_EXISTS, message)
        return self._write(path, proposal)

    def update(self, proposal: OperatorProposal) -> Path:
        """Persist a state change of an existing proposal."""
        path = self._path(proposal.resource_id)
        if not path.exists():
            message = f"proposal does not exist: {path}"
            raise OperatorError(_ERROR_PROPOSAL_MISSING, message)
        return self._write(path, proposal)

    def load(self, proposal_id: ResourceId) -> OperatorProposal:
        """Load and re-validate one proposal receipt."""
        path = self._path(proposal_id)
        if not path.is_file():
            message = f"proposal does not exist: {path}"
            raise OperatorError(_ERROR_PROPOSAL_MISSING, message)
        document = cast(
            "dict[str, object]", json.loads(path.read_text(encoding="utf-8"))
        )
        self.catalog.validate_named(_PROPOSAL_SCHEMA, document)
        return _proposal_from_document(document)

    def list(self) -> tuple[OperatorProposal, ...]:
        """Load every proposal receipt ordered by creation time."""
        self.directory.mkdir(parents=True, exist_ok=True)
        proposals = [
            self.load(ResourceId.from_boundary(path.stem))
            for path in sorted(self.directory.glob("*.json"))
        ]
        proposals.sort(key=lambda proposal: proposal.created_at)
        return tuple(proposals)

    def blocking_fingerprints(self) -> frozenset[str]:
        """Fingerprints already covered by an open or failed proposal."""
        return frozenset(
            proposal.alert.fingerprint
            for proposal in self.list()
            if proposal.status in _BLOCKING_STATUSES
        )

    def _path(self, proposal_id: ResourceId) -> Path:
        return self.directory / f"{proposal_id.value}.json"

    def _write(self, path: Path, proposal: OperatorProposal) -> Path:
        document = proposal.as_document()
        self.catalog.validate_named(_PROPOSAL_SCHEMA, document)
        self.directory.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(document, indent=2) + "\n", encoding="utf-8"
        )
        return path


def _proposal_from_document(document: Mapping[str, object]) -> OperatorProposal:
    metadata = cast("Mapping[str, object]", document["metadata"])
    spec = cast("Mapping[str, object]", document["spec"])
    raw_alert = cast("Mapping[str, object]", spec["alert"])
    diagnosis = cast("Mapping[str, object]", spec["diagnosis"])
    operation = cast("Mapping[str, object]", spec["operation"])
    raw_outcome = spec.get("outcome")
    outcome = None
    if isinstance(raw_outcome, dict):
        outcome = ProposalOutcome(
            executed_at=cast("str", raw_outcome["executedAt"]),
            verified_at=cast("str | None", raw_outcome["verifiedAt"]),
            result=cast("str", raw_outcome["result"]),
            detail=cast("str", raw_outcome["detail"]),
        )
    return OperatorProposal(
        resource_id=ResourceId.from_boundary(metadata["id"]),
        created_at=cast("str", spec["createdAt"]),
        status=ProposalStatus(cast("str", spec["status"])),
        alert=OperatorAlert(
            name=cast("str", raw_alert["name"]),
            cloudfall_rule=ResourceId.from_boundary(raw_alert["cloudfallRule"]),
            severity=AlertSeverity.from_boundary(raw_alert["severity"]),
            environment=ResourceId.from_boundary(raw_alert["environment"]),
            server=ResourceId.from_boundary(raw_alert["server"]),
            service=ResourceId.from_boundary(raw_alert["service"]),
            active_at=cast("str", raw_alert["activeAt"]),
            fingerprint=cast("str", raw_alert["fingerprint"]),
        ),
        diagnosis_summary=cast("str", diagnosis["summary"]),
        evidence=tuple(cast("list[str]", diagnosis["evidence"])),
        operation_kind=OperationKind(cast("str", operation["kind"])),
        command=tuple(cast("list[str]", operation["command"])),
        outcome=outcome,
    )


@dataclass(frozen=True, slots=True)
class RunReport:
    """Structured result of one operator pass."""

    proposed: tuple[str, ...]
    skipped: tuple[SkippedAlert, ...]
    open_proposals: int

    def as_dict(self) -> dict[str, object]:
        """Serialize for structured CLI output."""
        return {
            "proposed": list(self.proposed),
            "skipped": [skipped.as_dict() for skipped in self.skipped],
            "openProposals": self.open_proposals,
        }


def run_once(
    feed: AlertFeed,
    inventory: PlatformInventory,
    store: ProposalStore,
    now: str | None = None,
) -> RunReport:
    """One watch pass: fetch alerts and propose for anything new."""
    timestamp = now if now is not None else _utc_now()
    blocking = store.blocking_fingerprints()
    proposed: list[str] = []
    skipped: list[SkippedAlert] = []
    for alert in feed.fetch():
        if alert.fingerprint in blocking:
            continue
        result = propose(alert, inventory, timestamp)
        if isinstance(result, SkippedAlert):
            skipped.append(result)
            continue
        store.save(result)
        proposed.append(result.resource_id.value)
        blocking = blocking | {alert.fingerprint}
    open_count = sum(
        1
        for proposal in store.list()
        if proposal.status is ProposalStatus.PROPOSED
    )
    return RunReport(
        proposed=tuple(proposed),
        skipped=tuple(skipped),
        open_proposals=open_count,
    )


def engine_executor(
    context: EngineContext,
) -> Callable[[OperatorProposal], None]:
    """Executor running the proposal's engine entry point."""

    def _execute(proposal: OperatorProposal) -> None:
        if proposal.operation_kind is not OperationKind.CONVERGE_SERVICES:
            message = (
                "unsupported operation kind: "
                f"{proposal.operation_kind.value}"
            )
            raise OperatorError(_ERROR_OPERATION_UNSUPPORTED, message)
        run_engine_playbook(context, "services.yml", {})

    return _execute


def gateway_feed(
    inventory: PlatformInventory,
    ca_path: Path,
    certificate_path: Path,
    key_path: Path,
    url_override: str | None = None,
) -> GatewayAlertFeed:
    """Build the alert feed from the declared logging gateway."""
    url = url_override
    if url is None:
        stack = next(
            (
                stack
                for stack in inventory.logging_stacks
                if stack.alerting is not None
            ),
            None,
        )
        if stack is None:
            message = (
                "no LoggingStack declares alerting; pass an explicit "
                "gateway URL or declare an alerting block"
            )
            raise OperatorError(_ERROR_GATEWAY_UNDECLARED, message)
        url = (
            f"https://{stack.gateway.server_name.value}:"
            f"{stack.gateway.port.value}/api/v1/alerts"
        )
    return GatewayAlertFeed(
        url=url,
        ca_path=ca_path,
        certificate_path=certificate_path,
        key_path=key_path,
    )


@dataclass(frozen=True, slots=True)
class ApproveOptions:
    """Verification behavior for an approval."""

    verify_timeout_seconds: float = 180.0
    poll_interval_seconds: float = 10.0
    sleep: Callable[[float], None] = time.sleep


def approve(
    store: ProposalStore,
    proposal_id: ResourceId,
    executor: Callable[[OperatorProposal], None],
    feed: AlertFeed,
    options: ApproveOptions | None = None,
) -> OperatorProposal:
    """Execute an approved proposal and verify the alert resolves."""
    resolved_options = options if options is not None else ApproveOptions()
    verify_timeout_seconds = resolved_options.verify_timeout_seconds
    poll_interval_seconds = resolved_options.poll_interval_seconds
    sleep = resolved_options.sleep
    proposal = store.load(proposal_id)
    if proposal.status is not ProposalStatus.PROPOSED:
        message = (
            f"proposal {proposal_id.value} is {proposal.status.value}, "
            "only proposed proposals can be approved"
        )
        raise OperatorError(_ERROR_PROPOSAL_NOT_OPEN, message)
    executed_at = _utc_now()
    try:
        executor(proposal)
    except (OperatorError, LifecycleError) as error:
        failed = replace(
            proposal,
            status=ProposalStatus.FAILED,
            outcome=ProposalOutcome(
                executed_at=executed_at,
                verified_at=None,
                result="failed",
                detail=f"execution failed: {error}",
            ),
        )
        store.update(failed)
        raise
    executed = replace(
        proposal,
        status=ProposalStatus.EXECUTED,
        outcome=ProposalOutcome(
            executed_at=executed_at,
            verified_at=None,
            result="failed",
            detail="executed, verification pending",
        ),
    )
    store.update(executed)
    waited = 0.0
    while True:
        firing = {alert.fingerprint for alert in feed.fetch()}
        if proposal.alert.fingerprint not in firing:
            verified = replace(
                executed,
                status=ProposalStatus.VERIFIED,
                outcome=ProposalOutcome(
                    executed_at=executed_at,
                    verified_at=_utc_now(),
                    result="verified",
                    detail=(
                        "alert resolved after convergence; observed "
                        "outcome matches the declared state"
                    ),
                ),
            )
            store.update(verified)
            return verified
        if waited >= verify_timeout_seconds:
            failed = replace(
                executed,
                status=ProposalStatus.FAILED,
                outcome=ProposalOutcome(
                    executed_at=executed_at,
                    verified_at=_utc_now(),
                    result="failed",
                    detail=(
                        "alert still firing after "
                        f"{int(verify_timeout_seconds)}s; remediation did "
                        "not restore the declared state"
                    ),
                ),
            )
            store.update(failed)
            return failed
        sleep(poll_interval_seconds)
        waited += poll_interval_seconds


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
