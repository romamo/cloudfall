"""Build a read-only operations view from desired and observed state."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, cast

from cloudfall.audit import AuditStatus, audit_inventory
from cloudfall.domain import AbsolutePath, FilesystemName, ResourceId, ServiceName

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from cloudfall.audit import AuditCheck, ServerAudit
    from cloudfall.inventory import DomainInventory, PlatformInventory, ServerInventory
    from cloudfall.observation import ObservationSet, ObservedServerSnapshot
    from cloudfall.service_evidence import (
        DeploymentReceipt,
        DeploymentReceiptSet,
        DomainObservationSet,
        ObservedDomainSnapshot,
    )

_TASK_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9:.-]{0,127}$")
_PERSISTENT_FILESYSTEMS = frozenset(
    {"btrfs", "ext2", "ext3", "ext4", "f2fs", "xfs", "zfs"}
)
_STALE_WARNING_AFTER = timedelta(hours=24)
_STALE_CRITICAL_AFTER = timedelta(days=7)
_FILESYSTEM_WARNING_BASIS_POINTS = 8_500
_FILESYSTEM_CRITICAL_BASIS_POINTS = 9_500
_MAX_BASIS_POINTS = 10_000
_SMART_WARNING_PERCENT_USED = 90
_SMART_CRITICAL_PERCENT_USED = 100
_MAX_PERCENT = 100
_MAX_UNSIGNED_BYTE = 255


class OperationsHealth(StrEnum):
    """Aggregate health shown by operations clients."""

    HEALTHY = "healthy"
    WARNING = "warning"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


class TaskSeverity(StrEnum):
    """Priority of an evidence-derived operations task."""

    CRITICAL = "critical"
    WARNING = "warning"
    UNKNOWN = "unknown"


class TaskKind(StrEnum):
    """Source category for an evidence-derived operations task."""

    DRIFT = "drift"
    FILESYSTEM = "filesystem"
    OBSERVATION = "observation"
    SERVICE = "service"
    SMART = "smart"


class TaskState(StrEnum):
    """Workflow state supported by the initial read-only task projection."""

    OPEN = "open"


class LifecycleEvidence(StrEnum):
    """Evidence-backed answer for one service lifecycle milestone."""

    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


class ConfigurationStatus(StrEnum):
    """Desired-versus-observed service configuration status."""

    COMPLIANT = "compliant"
    DRIFTED = "drifted"
    UNKNOWN = "unknown"


class RouteCheckStatus(StrEnum):
    """Result of one public service route check."""

    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class OperationsTaskId:
    """Stable identifier derived from a task's server, kind, and source."""

    value: str

    def __post_init__(self) -> None:
        """Reject task identifiers that are unsafe in JSON and URLs."""
        if not _TASK_ID_PATTERN.fullmatch(self.value):
            message = f"invalid operations task id: {self.value!r}"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class UtcTimestamp:
    """Timezone-aware UTC timestamp used by operations projections."""

    value: datetime

    def __post_init__(self) -> None:
        """Require an aware timestamp normalized to UTC."""
        if self.value.tzinfo is None or self.value.utcoffset() != timedelta(0):
            message = "operations timestamp must be timezone-aware UTC"
            raise ValueError(message)

    @classmethod
    def now(cls) -> UtcTimestamp:
        """Return the current UTC time."""
        return cls(datetime.now(UTC))

    @classmethod
    def from_boundary(cls, value: object) -> UtcTimestamp:
        """Parse a schema-validated RFC 3339 timestamp."""
        if not isinstance(value, str):
            message = "operations timestamp must be a string"
            raise TypeError(message)
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            message = "operations timestamp must include a timezone"
            raise ValueError(message)
        return cls(parsed.astimezone(UTC))

    def as_string(self) -> str:
        """Serialize the timestamp as canonical UTC RFC 3339."""
        return self.value.isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class ObservedByteCount:
    """Non-negative byte count collected from a server."""

    value: int

    def __post_init__(self) -> None:
        """Reject booleans and negative byte counts."""
        if isinstance(self.value, bool) or self.value < 0:
            message = f"invalid observed byte count: {self.value!r}"
            raise ValueError(message)

    @classmethod
    def from_boundary(cls, value: object) -> ObservedByteCount:
        """Coerce a validated observation field into a byte count."""
        if isinstance(value, bool) or not isinstance(value, int):
            message = "observed byte count must be an integer"
            raise TypeError(message)
        return cls(value)


@dataclass(frozen=True, slots=True)
class Utilization:
    """Filesystem utilization represented in integer basis points."""

    basis_points: int

    def __post_init__(self) -> None:
        """Keep utilization within zero and one hundred percent."""
        if not 0 <= self.basis_points <= _MAX_BASIS_POINTS:
            message = f"invalid utilization basis points: {self.basis_points!r}"
            raise ValueError(message)

    @classmethod
    def from_counts(
        cls, used: ObservedByteCount, available: ObservedByteCount
    ) -> Utilization:
        """Compute df-compatible utilization, excluding reserved blocks."""
        visible = used.value + available.value
        if visible == 0:
            return cls(0)
        rounded = (used.value * _MAX_BASIS_POINTS + visible // 2) // visible
        return cls(min(rounded, _MAX_BASIS_POINTS))

    @property
    def percent(self) -> float:
        """Return a display-ready percentage."""
        return self.basis_points / 100


@dataclass(frozen=True, slots=True)
class MonitoredFilesystem:
    """Operational filesystem capacity and utilization."""

    target: AbsolutePath
    filesystem: FilesystemName
    size: ObservedByteCount
    used: ObservedByteCount
    available: ObservedByteCount
    utilization: Utilization

    def as_dict(self) -> dict[str, object]:
        """Serialize filesystem monitoring data."""
        return {
            "target": self.target.value,
            "filesystem": self.filesystem.value,
            "sizeBytes": self.size.value,
            "usedBytes": self.used.value,
            "availableBytes": self.available.value,
            "usedPercent": self.utilization.percent,
        }


class SmartOverallHealth(StrEnum):
    """Normalized smartctl overall-health result."""

    PASSED = "passed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class NvmeCriticalWarning:
    """NVMe SMART critical-warning bit field."""

    value: int

    def __post_init__(self) -> None:
        """Keep the bit field within one unsigned byte."""
        if isinstance(self.value, bool) or not 0 <= self.value <= _MAX_UNSIGNED_BYTE:
            message = f"invalid NVMe critical warning: {self.value!r}"
            raise ValueError(message)

    @classmethod
    def from_boundary(cls, value: object) -> NvmeCriticalWarning:
        """Parse the schema-validated hexadecimal representation."""
        if not isinstance(value, str):
            message = "NVMe critical warning must be a hexadecimal string"
            raise TypeError(message)
        return cls(int(value, 16))

    @property
    def is_clear(self) -> bool:
        """Return whether no NVMe critical-warning bits are set."""
        return self.value == 0

    def as_string(self) -> str:
        """Serialize the warning as a two-digit hexadecimal value."""
        return f"0x{self.value:02x}"


@dataclass(frozen=True, slots=True)
class SmartDeviceEvidence:
    """Normalized operational SMART evidence for one NVMe namespace."""

    path: AbsolutePath
    model: str
    serial: str
    firmware: str
    smartctl_exit_code: int
    overall_health: SmartOverallHealth
    critical_warning: NvmeCriticalWarning
    reliability_degraded: bool
    temperature_celsius: int
    available_spare_percent: int
    available_spare_threshold_percent: int
    percentage_used: int
    data_units_read: int
    data_units_written: int
    power_cycles: int
    power_on_hours: int
    unsafe_shutdowns: int
    media_and_data_integrity_errors: int
    error_information_log_entries: int

    def __post_init__(self) -> None:
        """Enforce ranges not fully represented by primitive field types."""
        if not self.model or not self.serial or not self.firmware:
            message = "SMART device identity fields must not be empty"
            raise ValueError(message)
        if not 0 <= self.smartctl_exit_code <= _MAX_UNSIGNED_BYTE:
            message = f"invalid smartctl exit code: {self.smartctl_exit_code!r}"
            raise ValueError(message)
        for name, value in (
            ("available spare", self.available_spare_percent),
            ("available spare threshold", self.available_spare_threshold_percent),
        ):
            if not 0 <= value <= _MAX_PERCENT:
                message = f"invalid {name} percentage: {value!r}"
                raise ValueError(message)
        for name, value in (
            ("percentage used", self.percentage_used),
            ("data units read", self.data_units_read),
            ("data units written", self.data_units_written),
            ("power cycles", self.power_cycles),
            ("power-on hours", self.power_on_hours),
            ("unsafe shutdowns", self.unsafe_shutdowns),
            ("media errors", self.media_and_data_integrity_errors),
            ("error log entries", self.error_information_log_entries),
        ):
            if value < 0:
                message = f"invalid {name}: {value!r}"
                raise ValueError(message)

    @classmethod
    def from_boundary(cls, content: Mapping[str, object]) -> SmartDeviceEvidence:
        """Create typed SMART evidence from a validated observation object."""
        return cls(
            path=AbsolutePath.from_boundary(content.get("path")),
            model=_string(content, "model"),
            serial=_string(content, "serial"),
            firmware=_string(content, "firmware"),
            smartctl_exit_code=_integer(content, "smartctlExitCode"),
            overall_health=SmartOverallHealth(_string(content, "overallHealth")),
            critical_warning=NvmeCriticalWarning.from_boundary(
                content.get("criticalWarning")
            ),
            reliability_degraded=_boolean(content, "reliabilityDegraded"),
            temperature_celsius=_integer(content, "temperatureCelsius"),
            available_spare_percent=_integer(content, "availableSparePercent"),
            available_spare_threshold_percent=_integer(
                content, "availableSpareThresholdPercent"
            ),
            percentage_used=_integer(content, "percentageUsed"),
            data_units_read=_integer(content, "dataUnitsRead"),
            data_units_written=_integer(content, "dataUnitsWritten"),
            power_cycles=_integer(content, "powerCycles"),
            power_on_hours=_integer(content, "powerOnHours"),
            unsafe_shutdowns=_integer(content, "unsafeShutdowns"),
            media_and_data_integrity_errors=_integer(
                content, "mediaAndDataIntegrityErrors"
            ),
            error_information_log_entries=_integer(
                content, "errorInformationLogEntries"
            ),
        )

    @property
    def is_degraded(self) -> bool:
        """Return whether replacement-level SMART evidence is present."""
        return (
            self.overall_health is SmartOverallHealth.FAILED
            or not self.critical_warning.is_clear
            or self.reliability_degraded
            or self.percentage_used >= _SMART_CRITICAL_PERCENT_USED
            or self.available_spare_percent <= self.available_spare_threshold_percent
            or self.media_and_data_integrity_errors > 0
        )

    def as_dict(self) -> dict[str, object]:
        """Serialize SMART evidence used by operations clients."""
        return {
            "path": self.path.value,
            "model": self.model,
            "serial": self.serial,
            "firmware": self.firmware,
            "smartctlExitCode": self.smartctl_exit_code,
            "overallHealth": self.overall_health.value,
            "criticalWarning": self.critical_warning.as_string(),
            "reliabilityDegraded": self.reliability_degraded,
            "temperatureCelsius": self.temperature_celsius,
            "availableSparePercent": self.available_spare_percent,
            "availableSpareThresholdPercent": (self.available_spare_threshold_percent),
            "percentageUsed": self.percentage_used,
            "dataUnitsRead": self.data_units_read,
            "dataUnitsWritten": self.data_units_written,
            "powerCycles": self.power_cycles,
            "powerOnHours": self.power_on_hours,
            "unsafeShutdowns": self.unsafe_shutdowns,
            "mediaAndDataIntegrityErrors": self.media_and_data_integrity_errors,
            "errorInformationLogEntries": self.error_information_log_entries,
        }


@dataclass(frozen=True, slots=True)
class OperationsTask:
    """One open task derived from current platform evidence."""

    task_id: OperationsTaskId
    server_id: ResourceId
    kind: TaskKind
    severity: TaskSeverity
    state: TaskState
    title: str
    detail: str
    source: str

    def as_dict(self) -> dict[str, object]:
        """Serialize a stable operations task."""
        return {
            "id": self.task_id.value,
            "server": self.server_id.value,
            "kind": self.kind.value,
            "severity": self.severity.value,
            "state": self.state.value,
            "title": self.title,
            "detail": self.detail,
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class TaskEvidence:
    """Human-readable task content and its machine evidence source."""

    title: str
    detail: str
    source: str


@dataclass(frozen=True, slots=True)
class ServerOperations:
    """Operations projection for one desired server."""

    server_id: ResourceId
    hostname: str
    health: OperationsHealth
    observed_at: UtcTimestamp | None
    filesystems: tuple[MonitoredFilesystem, ...]
    smart_devices: tuple[SmartDeviceEvidence, ...]
    tasks: tuple[OperationsTask, ...]

    def as_dict(self) -> dict[str, object]:
        """Serialize a server operations projection."""
        counts = Counter(task.severity.value for task in self.tasks)
        return {
            "id": self.server_id.value,
            "hostname": self.hostname,
            "health": self.health.value,
            "observedAt": (
                self.observed_at.as_string() if self.observed_at is not None else None
            ),
            "summary": {
                "tasks": len(self.tasks),
                "critical": counts[TaskSeverity.CRITICAL.value],
                "warning": counts[TaskSeverity.WARNING.value],
                "unknown": counts[TaskSeverity.UNKNOWN.value],
            },
            "filesystems": [filesystem.as_dict() for filesystem in self.filesystems],
            "smartDevices": [device.as_dict() for device in self.smart_devices],
            "tasks": [task.as_dict() for task in self.tasks],
        }


@dataclass(frozen=True, slots=True)
class DomainCheck:
    """Display-ready status and evidence detail for one domain check."""

    status: RouteCheckStatus
    detail: str

    def as_dict(self) -> dict[str, str]:
        """Serialize one domain check."""
        return {"status": self.status.value, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class DomainOperations:
    """Evidence-derived lifecycle and route status for one desired domain."""

    domain_id: ResourceId
    primary_name: str
    proxy_server_id: ResourceId
    origin_server_id: ResourceId
    health: OperationsHealth
    observed_at: UtcTimestamp | None
    planned: LifecycleEvidence
    ready_to_deploy: LifecycleEvidence
    deployed: LifecycleEvidence
    deployed_at: UtcTimestamp | None
    configuration: ConfigurationStatus
    configuration_detail: str
    dns: DomainCheck
    tls: DomainCheck
    origin: DomainCheck
    public_route: DomainCheck

    def as_dict(self) -> dict[str, object]:
        """Serialize service lifecycle and route evidence."""
        return {
            "id": self.domain_id.value,
            "primaryName": self.primary_name,
            "proxyServer": self.proxy_server_id.value,
            "originServer": self.origin_server_id.value,
            "health": self.health.value,
            "observedAt": (
                self.observed_at.as_string() if self.observed_at is not None else None
            ),
            "lifecycle": {
                "planned": self.planned.value,
                "readyToDeploy": self.ready_to_deploy.value,
                "deployed": self.deployed.value,
                "deployedAt": (
                    self.deployed_at.as_string()
                    if self.deployed_at is not None
                    else None
                ),
                "configured": self.configuration.value,
                "configurationDetail": self.configuration_detail,
            },
            "checks": {
                "dns": self.dns.as_dict(),
                "tls": self.tls.as_dict(),
                "origin": self.origin.as_dict(),
                "public": self.public_route.as_dict(),
            },
        }


@dataclass(frozen=True, slots=True)
class DomainOperationEvidence:
    """Evidence inputs used to derive one domain operations view."""

    server_observation: ObservedServerSnapshot | None
    receipt: DeploymentReceipt | None
    observation: ObservedDomainSnapshot | None
    generated_at: UtcTimestamp


@dataclass(frozen=True, slots=True)
class FleetOperations:
    """Fleet-wide read-only projection for monitoring and dashboards."""

    generated_at: UtcTimestamp
    health: OperationsHealth
    servers: tuple[ServerOperations, ...]
    domains: tuple[DomainOperations, ...]

    @property
    def tasks(self) -> tuple[OperationsTask, ...]:
        """Return all tasks in deterministic priority order."""
        return _sort_tasks(task for server in self.servers for task in server.tasks)

    def as_dict(self) -> dict[str, object]:
        """Serialize the complete operations view."""
        tasks = self.tasks
        severity_counts = Counter(task.severity.value for task in tasks)
        health_counts = Counter(server.health.value for server in self.servers)
        domain_health_counts = Counter(domain.health.value for domain in self.domains)
        return {
            "generatedAt": self.generated_at.as_string(),
            "health": self.health.value,
            "summary": {
                "servers": len(self.servers),
                "serverHealth": {
                    status.value: health_counts[status.value]
                    for status in OperationsHealth
                },
                "tasks": {
                    "open": len(tasks),
                    "critical": severity_counts[TaskSeverity.CRITICAL.value],
                    "warning": severity_counts[TaskSeverity.WARNING.value],
                    "unknown": severity_counts[TaskSeverity.UNKNOWN.value],
                },
                "domains": {
                    "total": len(self.domains),
                    "health": {
                        status.value: domain_health_counts[status.value]
                        for status in OperationsHealth
                    },
                },
            },
            "tasks": [task.as_dict() for task in tasks],
            "servers": [server.as_dict() for server in self.servers],
            "domains": [domain.as_dict() for domain in self.domains],
        }


def build_operations_view(
    inventory: PlatformInventory,
    observations: ObservationSet,
    deployment_receipts: DeploymentReceiptSet,
    domain_observations: DomainObservationSet,
    *,
    generated_at: UtcTimestamp,
) -> FleetOperations:
    """Combine audit and monitoring evidence into one operations projection."""
    audit = audit_inventory(inventory, observations)
    servers = tuple(
        _server_operations(
            server,
            observations.for_server(server.resource_id),
            next(
                item
                for item in audit.servers
                if item.server_id == server.resource_id.value
            ),
            generated_at,
        )
        for server in inventory.servers
    )
    domains = tuple(
        _domain_operations(
            domain,
            inventory,
            DomainOperationEvidence(
                server_observation=observations.for_server(domain.proxy.server_id),
                receipt=deployment_receipts.for_domain(domain.resource_id),
                observation=domain_observations.for_domain(domain.resource_id),
                generated_at=generated_at,
            ),
        )
        for domain in inventory.domains
    )
    return FleetOperations(
        generated_at=generated_at,
        health=_fleet_health(servers, domains),
        servers=servers,
        domains=domains,
    )


def _domain_operations(
    domain: DomainInventory,
    inventory: PlatformInventory,
    evidence: DomainOperationEvidence,
) -> DomainOperations:
    proxy_server = _inventory_server(inventory, domain.proxy.server_id)
    origin_server = _inventory_server(inventory, domain.origin.server_id)
    ready = (
        LifecycleEvidence.YES
        if proxy_server.lifecycle.value == "active"
        and origin_server.lifecycle.value == "active"
        else LifecycleEvidence.NO
    )
    deployed, deployed_at = _deployment_evidence(domain, evidence.receipt)
    configuration, configuration_detail = _domain_configuration(
        domain,
        evidence.server_observation,
        evidence.receipt,
    )
    dns = _dns_check(domain, evidence.observation)
    tls = _tls_check(domain, evidence.observation, evidence.generated_at)
    origin = _endpoint_check(domain, evidence.observation, endpoint="origin")
    public_route = _endpoint_check(domain, evidence.observation, endpoint="public")
    health = _domain_health(
        ready,
        deployed,
        configuration,
        (dns, tls, origin, public_route),
    )
    return DomainOperations(
        domain_id=domain.resource_id,
        primary_name=domain.primary_name.value,
        proxy_server_id=domain.proxy.server_id,
        origin_server_id=domain.origin.server_id,
        health=health,
        observed_at=(
            UtcTimestamp(evidence.observation.observed_at.value)
            if evidence.observation is not None
            else None
        ),
        planned=LifecycleEvidence.YES,
        ready_to_deploy=ready,
        deployed=deployed,
        deployed_at=deployed_at,
        configuration=configuration,
        configuration_detail=configuration_detail,
        dns=dns,
        tls=tls,
        origin=origin,
        public_route=public_route,
    )


def _inventory_server(
    inventory: PlatformInventory, server_id: ResourceId
) -> ServerInventory:
    server = next(
        (
            candidate
            for candidate in inventory.servers
            if candidate.resource_id == server_id
        ),
        None,
    )
    if server is None:
        message = f"validated domain server does not exist: {server_id.value}"
        raise KeyError(message)
    return server


def _deployment_evidence(
    domain: DomainInventory, receipt: DeploymentReceipt | None
) -> tuple[LifecycleEvidence, UtcTimestamp | None]:
    if receipt is None:
        return LifecycleEvidence.NO, None
    matches = (
        receipt.server_id == domain.proxy.server_id
        and receipt.configuration_path == domain.proxy.configuration_path
    )
    return (
        LifecycleEvidence.YES if matches else LifecycleEvidence.NO,
        UtcTimestamp(receipt.deployed_at.value),
    )


def _domain_configuration(
    domain: DomainInventory,
    observation: ObservedServerSnapshot | None,
    receipt: DeploymentReceipt | None,
) -> tuple[ConfigurationStatus, str]:
    if observation is None:
        return ConfigurationStatus.UNKNOWN, "proxy server has no observation"
    services = _mapping(observation.spec, "services")
    service = _optional_mapping(services.get(domain.proxy.service_name.value))
    if service is None:
        return ConfigurationStatus.DRIFTED, "proxy service is not installed"
    if service.get("state") != "running" or service.get("status") != "enabled":
        return ConfigurationStatus.DRIFTED, "proxy service is not running and enabled"
    configuration = _mapping_sequence(observation.spec, "configuration")
    config = next(
        (
            item
            for item in configuration
            if item.get("path") == domain.proxy.configuration_path.value
        ),
        None,
    )
    if config is None or config.get("exists") is not True:
        return ConfigurationStatus.DRIFTED, "proxy configuration file is missing"
    if (
        receipt is not None
        and receipt.configuration_path == domain.proxy.configuration_path
        and config.get("sha256") != receipt.configuration_sha256.value
    ):
        return (
            ConfigurationStatus.DRIFTED,
            "observed configuration differs from deployment receipt",
        )
    return (
        ConfigurationStatus.COMPLIANT,
        "service and configuration match available evidence",
    )


def _dns_check(
    domain: DomainInventory, observation: ObservedDomainSnapshot | None
) -> DomainCheck:
    if observation is None:
        return DomainCheck(RouteCheckStatus.UNKNOWN, "domain has no route observation")
    addresses = ", ".join(address.value for address in observation.dns_addresses)
    if observation.dns_error is not None:
        return DomainCheck(RouteCheckStatus.UNHEALTHY, observation.dns_error)
    if domain.edge.mode.value == "proxied":
        return DomainCheck(
            (
                RouteCheckStatus.HEALTHY
                if observation.edge_detected
                else RouteCheckStatus.UNHEALTHY
            ),
            (
                f"{domain.edge.provider.value} edge detected; {addresses}"
                if observation.edge_detected
                else f"{domain.edge.provider.value} edge not detected; {addresses}"
            ),
        )
    return DomainCheck(
        (
            RouteCheckStatus.HEALTHY
            if observation.proxy_address_observed
            else RouteCheckStatus.UNHEALTHY
        ),
        (
            f"DNS resolves directly to proxy; {addresses}"
            if observation.proxy_address_observed
            else f"DNS does not resolve to proxy; {addresses}"
        ),
    )


def _tls_check(
    domain: DomainInventory,
    observation: ObservedDomainSnapshot | None,
    generated_at: UtcTimestamp,
) -> DomainCheck:
    if domain.tls_mode.value == "disabled":
        return DomainCheck(RouteCheckStatus.HEALTHY, "TLS is disabled by desired state")
    if observation is None:
        return DomainCheck(RouteCheckStatus.UNKNOWN, "domain has no TLS observation")
    if not observation.tls.valid:
        return DomainCheck(
            RouteCheckStatus.UNHEALTHY,
            observation.tls.error or "TLS certificate is unavailable or invalid",
        )
    expires = observation.tls.expires_at
    if expires is None:
        return DomainCheck(RouteCheckStatus.UNHEALTHY, "TLS expiry is unavailable")
    if expires.value - generated_at.value <= timedelta(days=30):
        return DomainCheck(
            RouteCheckStatus.UNHEALTHY,
            f"TLS certificate expires {expires.as_string()}",
        )
    return DomainCheck(
        RouteCheckStatus.HEALTHY,
        f"valid certificate expires {expires.as_string()}",
    )


def _endpoint_check(
    domain: DomainInventory,
    observation: ObservedDomainSnapshot | None,
    *,
    endpoint: str,
) -> DomainCheck:
    if observation is None:
        return DomainCheck(
            RouteCheckStatus.UNKNOWN,
            f"domain has no {endpoint} observation",
        )
    evidence = observation.origin if endpoint == "origin" else observation.public
    if evidence.skipped:
        return DomainCheck(
            RouteCheckStatus.HEALTHY,
            evidence.skip_reason or f"{endpoint} probe skipped by evidence",
        )
    if not evidence.reachable or evidence.status is None:
        return DomainCheck(
            RouteCheckStatus.UNHEALTHY,
            evidence.error or f"{endpoint} endpoint is unreachable",
        )
    expected = frozenset(domain.health_check.expected_statuses)
    healthy = evidence.status in expected
    return DomainCheck(
        RouteCheckStatus.HEALTHY if healthy else RouteCheckStatus.UNHEALTHY,
        f"HTTP {evidence.status.value}; expected "
        + ", ".join(
            str(status.value) for status in domain.health_check.expected_statuses
        ),
    )


def _domain_health(
    ready: LifecycleEvidence,
    deployed: LifecycleEvidence,
    configuration: ConfigurationStatus,
    checks: tuple[DomainCheck, ...],
) -> OperationsHealth:
    if (
        ready is LifecycleEvidence.NO
        or deployed is LifecycleEvidence.NO
        or configuration is ConfigurationStatus.DRIFTED
        or any(check.status is RouteCheckStatus.UNHEALTHY for check in checks)
    ):
        return OperationsHealth.WARNING
    if (
        ready is LifecycleEvidence.UNKNOWN
        or deployed is LifecycleEvidence.UNKNOWN
        or configuration is ConfigurationStatus.UNKNOWN
        or any(check.status is RouteCheckStatus.UNKNOWN for check in checks)
    ):
        return OperationsHealth.UNKNOWN
    return OperationsHealth.HEALTHY


def _fleet_health(
    servers: tuple[ServerOperations, ...], domains: tuple[DomainOperations, ...]
) -> OperationsHealth:
    values = frozenset(
        (*[server.health for server in servers], *[domain.health for domain in domains])
    )
    if OperationsHealth.CRITICAL in values:
        return OperationsHealth.CRITICAL
    if OperationsHealth.UNKNOWN in values:
        return OperationsHealth.UNKNOWN
    if OperationsHealth.WARNING in values:
        return OperationsHealth.WARNING
    return OperationsHealth.HEALTHY


def _server_operations(
    server: ServerInventory,
    snapshot: ObservedServerSnapshot | None,
    audit: ServerAudit,
    generated_at: UtcTimestamp,
) -> ServerOperations:
    audit_tasks = tuple(
        _audit_task(server.resource_id, check)
        for check in audit.checks
        if check.status is not AuditStatus.COMPLIANT
    )
    if snapshot is None:
        tasks = _sort_tasks(audit_tasks)
        return ServerOperations(
            server_id=server.resource_id,
            hostname=server.hostname.value,
            health=_health(task.severity for task in tasks),
            observed_at=None,
            filesystems=(),
            smart_devices=(),
            tasks=tasks,
        )

    observed_at = UtcTimestamp.from_boundary(snapshot.spec.get("observedAt"))
    filesystems = _filesystems(snapshot)
    smart_available, smart_expected, smart_devices = _smart_evidence(snapshot)
    monitoring_tasks = (
        _observation_tasks(server.resource_id, observed_at, generated_at)
        + tuple(
            task
            for filesystem in filesystems
            if (task := _filesystem_task(server.resource_id, filesystem)) is not None
        )
        + _failed_service_tasks(server.resource_id, snapshot)
        + _smart_tasks(
            server.resource_id,
            available=smart_available,
            expected=smart_expected,
            devices=smart_devices,
        )
    )
    tasks = _sort_tasks((*audit_tasks, *monitoring_tasks))
    return ServerOperations(
        server_id=server.resource_id,
        hostname=server.hostname.value,
        health=_health(task.severity for task in tasks),
        observed_at=observed_at,
        filesystems=filesystems,
        smart_devices=smart_devices,
        tasks=tasks,
    )


def _audit_task(server_id: ResourceId, check: AuditCheck) -> OperationsTask:
    source = f"audit.{check.check}"
    missing_observation = check.check == "observation.available"
    severity = (
        TaskSeverity.UNKNOWN
        if check.status is AuditStatus.UNKNOWN
        else TaskSeverity.CRITICAL
        if check.check == "storage.softwareRaid.activeDevices"
        else TaskSeverity.WARNING
    )
    return _task(
        server_id,
        TaskKind.OBSERVATION if missing_observation else TaskKind.DRIFT,
        severity,
        TaskEvidence(
            title=(
                "Collect a server observation"
                if missing_observation
                else f"Resolve desired-state drift: {check.check}"
            ),
            detail=check.message,
            source=source,
        ),
    )


def _observation_tasks(
    server_id: ResourceId,
    observed_at: UtcTimestamp,
    generated_at: UtcTimestamp,
) -> tuple[OperationsTask, ...]:
    age = generated_at.value - observed_at.value
    if age <= _STALE_WARNING_AFTER:
        return ()
    severity = (
        TaskSeverity.CRITICAL if age > _STALE_CRITICAL_AFTER else TaskSeverity.WARNING
    )
    age_hours = int(age.total_seconds() // 3600)
    return (
        _task(
            server_id,
            TaskKind.OBSERVATION,
            severity,
            TaskEvidence(
                title="Refresh stale server observation",
                detail=f"latest evidence is {age_hours} hours old",
                source="observed.spec.observedAt",
            ),
        ),
    )


def _filesystems(
    snapshot: ObservedServerSnapshot,
) -> tuple[MonitoredFilesystem, ...]:
    storage = _mapping(snapshot.spec, "storage")
    result: list[MonitoredFilesystem] = []
    for raw in _mapping_sequence(storage, "filesystems"):
        filesystem_name = FilesystemName.from_boundary(raw.get("fstype"))
        if filesystem_name.value not in _PERSISTENT_FILESYSTEMS:
            continue
        used = ObservedByteCount.from_boundary(raw.get("used"))
        available = ObservedByteCount.from_boundary(raw.get("avail"))
        result.append(
            MonitoredFilesystem(
                target=AbsolutePath.from_boundary(raw.get("target")),
                filesystem=filesystem_name,
                size=ObservedByteCount.from_boundary(raw.get("size")),
                used=used,
                available=available,
                utilization=Utilization.from_counts(used, available),
            )
        )
    return tuple(sorted(result, key=lambda item: item.target.value))


def _filesystem_task(
    server_id: ResourceId, filesystem: MonitoredFilesystem
) -> OperationsTask | None:
    basis_points = filesystem.utilization.basis_points
    if basis_points < _FILESYSTEM_WARNING_BASIS_POINTS:
        return None
    severity = (
        TaskSeverity.CRITICAL
        if basis_points >= _FILESYSTEM_CRITICAL_BASIS_POINTS
        else TaskSeverity.WARNING
    )
    percent = filesystem.utilization.percent
    return _task(
        server_id,
        TaskKind.FILESYSTEM,
        severity,
        TaskEvidence(
            title=f"Reduce disk usage on {filesystem.target.value}",
            detail=(
                f"{percent:.1f}% used; "
                f"{filesystem.available.value} bytes remain available"
            ),
            source=f"observed.storage.filesystems[{filesystem.target.value}]",
        ),
    )


def _smart_evidence(
    snapshot: ObservedServerSnapshot,
) -> tuple[bool, bool, tuple[SmartDeviceEvidence, ...]]:
    storage = _mapping(snapshot.spec, "storage")
    smart = _mapping(storage, "smart")
    available = _boolean(smart, "available")
    expected = any(
        _string(device, "type") == "disk"
        and _string(device, "path").startswith("/dev/nvme")
        for device in _mapping_sequence(storage, "blockDevices")
    )
    devices = tuple(
        SmartDeviceEvidence.from_boundary(item)
        for item in _mapping_sequence(smart, "devices")
    )
    return available, expected, devices


def _smart_tasks(
    server_id: ResourceId,
    *,
    available: bool,
    expected: bool,
    devices: tuple[SmartDeviceEvidence, ...],
) -> tuple[OperationsTask, ...]:
    if not available and expected:
        return (
            _task(
                server_id,
                TaskKind.SMART,
                TaskSeverity.WARNING,
                TaskEvidence(
                    title="Restore SMART evidence collection",
                    detail="smartctl was unavailable during inspection",
                    source="observed.storage.smart.available",
                ),
            ),
        )
    if not available:
        return ()

    tasks: list[OperationsTask] = []
    for device in devices:
        if not (
            device.is_degraded
            or device.percentage_used >= _SMART_WARNING_PERCENT_USED
            or device.smartctl_exit_code != 0
        ):
            continue
        severity = TaskSeverity.CRITICAL if device.is_degraded else TaskSeverity.WARNING
        tasks.append(
            _task(
                server_id,
                TaskKind.SMART,
                severity,
                TaskEvidence(
                    title=(
                        f"Plan replacement for degraded NVMe {device.path.value}"
                        if device.is_degraded
                        else f"Review NVMe endurance for {device.path.value}"
                    ),
                    detail=(
                        f"SMART {device.overall_health.value}; "
                        f"warning {device.critical_warning.as_string()}; "
                        f"{device.percentage_used}% used; "
                        f"{device.available_spare_percent}% spare; "
                        f"{device.media_and_data_integrity_errors} media errors"
                    ),
                    source=f"observed.storage.smart.devices[{device.path.value}]",
                ),
            )
        )
    return tuple(tasks)


def _failed_service_tasks(
    server_id: ResourceId, snapshot: ObservedServerSnapshot
) -> tuple[OperationsTask, ...]:
    services = _mapping(snapshot.spec, "services")
    tasks: list[OperationsTask] = []
    for name, value in services.items():
        service = _optional_mapping(value)
        if service is None or service.get("state") != "failed":
            continue
        service_name = ServiceName.from_boundary(name)
        tasks.append(
            _task(
                server_id,
                TaskKind.SERVICE,
                TaskSeverity.WARNING,
                TaskEvidence(
                    title=f"Investigate failed service {service_name.value}",
                    detail=f"systemd status is {service.get('status')!s}",
                    source=f"observed.services[{service_name.value}]",
                ),
            )
        )
    return tuple(tasks)


def _task(
    server_id: ResourceId,
    kind: TaskKind,
    severity: TaskSeverity,
    evidence: TaskEvidence,
) -> OperationsTask:
    digest = hashlib.sha256(
        f"{server_id.value}\0{kind.value}\0{evidence.source}".encode()
    ).hexdigest()[:16]
    return OperationsTask(
        task_id=OperationsTaskId(f"{server_id.value}:{kind.value}:{digest}"),
        server_id=server_id,
        kind=kind,
        severity=severity,
        state=TaskState.OPEN,
        title=evidence.title,
        detail=evidence.detail,
        source=evidence.source,
    )


def _sort_tasks(tasks: Iterable[OperationsTask]) -> tuple[OperationsTask, ...]:
    rank = {
        TaskSeverity.CRITICAL: 0,
        TaskSeverity.WARNING: 1,
        TaskSeverity.UNKNOWN: 2,
    }
    return tuple(
        sorted(tasks, key=lambda task: (rank[task.severity], task.task_id.value))
    )


def _health(severities: Iterable[TaskSeverity]) -> OperationsHealth:
    values = frozenset(severities)
    if TaskSeverity.CRITICAL in values:
        return OperationsHealth.CRITICAL
    if TaskSeverity.UNKNOWN in values:
        return OperationsHealth.UNKNOWN
    if TaskSeverity.WARNING in values:
        return OperationsHealth.WARNING
    return OperationsHealth.HEALTHY


def _mapping(content: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = _optional_mapping(content.get(key))
    if value is None:
        message = f"validated field {key!r} is not an object"
        raise TypeError(message)
    return value


def _optional_mapping(value: object) -> Mapping[str, object] | None:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        return None
    return cast("Mapping[str, object]", value)


def _mapping_sequence(
    content: Mapping[str, object], key: str
) -> tuple[Mapping[str, object], ...]:
    value = content.get(key)
    if not isinstance(value, list):
        message = f"validated field {key!r} is not an array"
        raise TypeError(message)
    result: list[Mapping[str, object]] = []
    for item in value:
        mapping = _optional_mapping(item)
        if mapping is None:
            message = f"validated field {key!r} contains a non-object"
            raise TypeError(message)
        result.append(mapping)
    return tuple(result)


def _string(content: Mapping[str, object], key: str) -> str:
    value = content.get(key)
    if not isinstance(value, str):
        message = f"validated field {key!r} is not a string"
        raise TypeError(message)
    return value


def _integer(content: Mapping[str, object], key: str) -> int:
    value = content.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        message = f"validated field {key!r} is not an integer"
        raise TypeError(message)
    return value


def _boolean(content: Mapping[str, object], key: str) -> bool:
    value = content.get(key)
    if not isinstance(value, bool):
        message = f"validated field {key!r} is not a boolean"
        raise TypeError(message)
    return value
