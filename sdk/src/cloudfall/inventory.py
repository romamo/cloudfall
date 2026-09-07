"""Typed, read-only inventory projections over validated platform state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from cloudfall.domain import (
    AbsolutePath,
    ByteSize,
    ConfigCapture,
    ConnectionAddress,
    DatacenterCode,
    DeploymentApproval,
    DnsMode,
    FileMode,
    FilesystemName,
    FirewallPolicy,
    Hostname,
    HttpScheme,
    HttpStatusCode,
    IpAddress,
    Ipv4Address,
    Ipv6NetworkCidr,
    LinuxUser,
    LoggingMigrationMode,
    LoggingStorageType,
    NetworkProtocol,
    OpenSshPublicKey,
    OperatingSystemDistribution,
    OperatingSystemMajorVersion,
    PackageName,
    PackageVersion,
    PositiveCount,
    ProviderServerId,
    RaidLevel,
    RequiredServiceState,
    RequiredServiceStatus,
    ResourceDocument,
    ResourceId,
    ResourceKind,
    ServerLifecycle,
    ServiceManager,
    ServiceName,
    Sha256Digest,
    SshPublicKeyLifecycle,
    SystemdUnitName,
    TcpPort,
    TlsMode,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from cloudfall.validation import ValidatedState


@dataclass(frozen=True, slots=True)
class ServerLabel:
    """Typed server label entry."""

    key: ResourceId
    value: str

    def __post_init__(self) -> None:
        """Reject empty label values."""
        if not self.value:
            message = "server label value must not be empty"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class ServerProvider:
    """Infrastructure-provider identity for a server."""

    name: ResourceId
    server_id: ProviderServerId | None
    datacenter: DatacenterCode | None

    def as_dict(self) -> dict[str, object]:
        """Serialize provider identity."""
        result: dict[str, object] = {"name": self.name.value}
        if self.server_id is not None:
            result["serverId"] = self.server_id.value
        if self.datacenter is not None:
            result["datacenter"] = self.datacenter.value
        return result


@dataclass(frozen=True, slots=True)
class ServerNetwork:
    """Public network allocation and reverse DNS for a server."""

    ipv4: Ipv4Address
    ipv6_cidr: Ipv6NetworkCidr
    reverse_dns: Hostname

    def as_dict(self) -> dict[str, object]:
        """Serialize public network details."""
        return {
            "ipv4": self.ipv4.value,
            "ipv6Cidr": self.ipv6_cidr.value,
            "reverseDns": self.reverse_dns.value,
        }


@dataclass(frozen=True, slots=True)
class ServerInventory:
    """Connection and placement data for one managed server."""

    resource_id: ResourceId
    hostname: Hostname
    address: ConnectionAddress
    environment: ResourceId
    profile_id: ResourceId
    lifecycle: ServerLifecycle
    ssh_user: LinuxUser
    ssh_port: TcpPort
    labels: tuple[ServerLabel, ...]
    provider: ServerProvider | None
    network: ServerNetwork | None

    def as_dict(self) -> dict[str, object]:
        """Serialize the server without exposing secrets."""
        result: dict[str, object] = {
            "id": self.resource_id.value,
            "hostname": self.hostname.value,
            "address": self.address.value,
            "environment": self.environment.value,
            "profile": self.profile_id.value,
            "lifecycle": self.lifecycle.value,
            "ssh": {
                "user": self.ssh_user.value,
                "port": self.ssh_port.value,
            },
            "labels": {label.key.value: label.value for label in self.labels},
        }
        if self.provider is not None:
            result["provider"] = self.provider.as_dict()
        if self.network is not None:
            result["network"] = self.network.as_dict()
        return result


@dataclass(frozen=True, slots=True)
class OperatingSystemRequirement:
    """Desired operating-system contract for a host profile."""

    distribution: OperatingSystemDistribution
    versions: tuple[OperatingSystemMajorVersion, ...]
    service_manager: ServiceManager


@dataclass(frozen=True, slots=True)
class RaidRequirement:
    """Desired Linux software RAID contract."""

    level: RaidLevel
    minimum_active_devices: PositiveCount
    minimum_usable_bytes: ByteSize


@dataclass(frozen=True, slots=True)
class MountRequirement:
    """Desired mounted filesystem contract."""

    path: AbsolutePath
    filesystem: FilesystemName
    minimum_bytes: ByteSize


@dataclass(frozen=True, slots=True)
class PackageRequirement:
    """Desired installed package and optional exact version."""

    name: PackageName
    version: PackageVersion | None


@dataclass(frozen=True, slots=True)
class ServiceRequirement:
    """Desired systemd service- or timer-unit state."""

    name: SystemdUnitName
    state: RequiredServiceState
    status: RequiredServiceStatus


@dataclass(frozen=True, slots=True)
class FirewallRule:
    """One allowed inbound transport-layer endpoint."""

    port: TcpPort
    protocol: NetworkProtocol
    description: str | None

    def as_dict(self) -> dict[str, object]:
        """Serialize the rule for engine and audit consumers."""
        result: dict[str, object] = {
            "port": self.port.value,
            "protocol": self.protocol.value,
        }
        if self.description is not None:
            result["description"] = self.description
        return result


@dataclass(frozen=True, slots=True)
class FirewallRequirement:
    """Desired default-deny inbound firewall contract."""

    policy: FirewallPolicy
    allowed_inbound: tuple[FirewallRule, ...]

    def __post_init__(self) -> None:
        """Reject duplicate port/protocol rules."""
        seen: set[tuple[int, NetworkProtocol]] = set()
        for rule in self.allowed_inbound:
            identity = (rule.port.value, rule.protocol)
            if identity in seen:
                message = (
                    "duplicate firewall rule: "
                    f"{rule.port.value}/{rule.protocol.value}"
                )
                raise ValueError(message)
            seen.add(identity)

    def as_dict(self) -> dict[str, object]:
        """Serialize the desired firewall contract."""
        return {
            "policy": self.policy.value,
            "allowedInbound": [rule.as_dict() for rule in self.allowed_inbound],
        }


@dataclass(frozen=True, slots=True)
class ConfigurationFileRequirement:
    """Allowlisted non-secret configuration evidence contract."""

    path: AbsolutePath
    capture: ConfigCapture
    owner: LinuxUser | None
    group: LinuxUser | None
    mode: FileMode | None
    sha256: Sha256Digest | None

    def as_dict(self) -> dict[str, object]:
        """Serialize the safe configuration collection contract."""
        result: dict[str, object] = {
            "path": self.path.value,
            "capture": self.capture.value,
        }
        if self.owner is not None:
            result["owner"] = self.owner.value
        if self.group is not None:
            result["group"] = self.group.value
        if self.mode is not None:
            result["mode"] = self.mode.value
        if self.sha256 is not None:
            result["sha256"] = self.sha256.value
        return result


@dataclass(frozen=True, slots=True)
class HostProfileInventory:
    """Reusable desired host contract."""

    resource_id: ResourceId
    os: OperatingSystemRequirement
    raid: RaidRequirement | None
    mounts: tuple[MountRequirement, ...]
    required_packages: tuple[PackageRequirement, ...]
    forbidden_packages: tuple[PackageName, ...]
    required_services: tuple[ServiceRequirement, ...]
    firewall: FirewallRequirement | None
    configuration_files: tuple[ConfigurationFileRequirement, ...]

    def as_dict(self) -> dict[str, object]:
        """Serialize the profile for inspection and audit consumers."""
        required_packages: list[dict[str, object]] = []
        for package in self.required_packages:
            item: dict[str, object] = {"name": package.name.value}
            if package.version is not None:
                item["version"] = package.version.value
            required_packages.append(item)
        storage: dict[str, object] = {
            "mounts": [
                {
                    "path": mount.path.value,
                    "filesystem": mount.filesystem.value,
                    "minimumBytes": mount.minimum_bytes.value,
                }
                for mount in self.mounts
            ]
        }
        if self.raid is not None:
            storage["softwareRaid"] = {
                "level": self.raid.level.value,
                "minimumActiveDevices": self.raid.minimum_active_devices.value,
                "minimumUsableBytes": self.raid.minimum_usable_bytes.value,
            }
        result: dict[str, object] = {
            "id": self.resource_id.value,
            "os": {
                "distribution": self.os.distribution.value,
                "versions": [version.value for version in self.os.versions],
                "serviceManager": self.os.service_manager.value,
            },
            "storage": storage,
            "packages": {
                "required": required_packages,
                "forbidden": [package.value for package in self.forbidden_packages],
            },
            "services": {
                "required": [
                    {
                        "name": service.name.value,
                        "state": service.state.value,
                        "status": service.status.value,
                    }
                    for service in self.required_services
                ]
            },
            "configuration": {
                "files": [item.as_dict() for item in self.configuration_files]
            },
        }
        if self.firewall is not None:
            result["firewall"] = self.firewall.as_dict()
        return result


@dataclass(frozen=True, slots=True)
class ProjectInventory:
    """Composition and ownership data for one SaaS project."""

    resource_id: ResourceId
    linux_user: LinuxUser
    approval: DeploymentApproval
    component_ids: tuple[ResourceId, ...]

    def as_dict(self) -> dict[str, object]:
        """Serialize project composition for inventory consumers."""
        return {
            "id": self.resource_id.value,
            "linuxUser": self.linux_user.value,
            "approval": self.approval.value,
            "components": [component.value for component in self.component_ids],
        }


@dataclass(frozen=True, slots=True)
class ComponentInventory:
    """Placement data for one deployable component."""

    resource_id: ResourceId
    project_id: ResourceId
    server_ids: tuple[ResourceId, ...]
    install_root: AbsolutePath

    def as_dict(self) -> dict[str, object]:
        """Serialize component placement for inventory consumers."""
        return {
            "id": self.resource_id.value,
            "project": self.project_id.value,
            "servers": [server.value for server in self.server_ids],
            "installRoot": self.install_root.value,
        }


@dataclass(frozen=True, slots=True)
class DomainProxy:
    """Desired public proxy placement and configuration evidence path."""

    server_id: ResourceId
    configuration_path: AbsolutePath
    service_name: ServiceName
    upstream_address: IpAddress | None = None
    upstream_port: TcpPort | None = None


@dataclass(frozen=True, slots=True)
class DomainOrigin:
    """Pinned origin endpoint behind a public proxy."""

    server_id: ResourceId
    configuration_path: AbsolutePath
    service_name: ServiceName
    scheme: HttpScheme
    port: TcpPort
    server_name: Hostname


@dataclass(frozen=True, slots=True)
class DomainEdge:
    """Desired external DNS/edge provider behavior."""

    provider: ResourceId
    mode: DnsMode


@dataclass(frozen=True, slots=True)
class DomainHealthCheck:
    """End-to-end public and origin health contract."""

    scheme: HttpScheme
    path: str
    expected_statuses: tuple[HttpStatusCode, ...]
    timeout_seconds: PositiveCount


@dataclass(frozen=True, slots=True)
class DomainInventory:
    """Typed desired state for one public domain route."""

    resource_id: ResourceId
    primary_name: Hostname
    aliases: tuple[Hostname, ...]
    proxy: DomainProxy
    origin: DomainOrigin
    edge: DomainEdge
    tls_mode: TlsMode
    health_check: DomainHealthCheck

    def as_dict(self) -> dict[str, object]:
        """Serialize public routing intent without secret material."""
        proxy: dict[str, object] = {
            "server": self.proxy.server_id.value,
            "configurationPath": self.proxy.configuration_path.value,
            "service": self.proxy.service_name.value,
        }
        if (
            self.proxy.upstream_address is not None
            and self.proxy.upstream_port is not None
        ):
            proxy["upstream"] = {
                "address": self.proxy.upstream_address.value,
                "port": self.proxy.upstream_port.value,
            }
        return {
            "id": self.resource_id.value,
            "primaryName": self.primary_name.value,
            "aliases": [alias.value for alias in self.aliases],
            "proxy": proxy,
            "origin": {
                "server": self.origin.server_id.value,
                "configurationPath": self.origin.configuration_path.value,
                "service": self.origin.service_name.value,
                "scheme": self.origin.scheme.value,
                "port": self.origin.port.value,
                "serverName": self.origin.server_name.value,
            },
            "edge": {
                "provider": self.edge.provider.value,
                "mode": self.edge.mode.value,
            },
            "tls": {"mode": self.tls_mode.value},
            "healthCheck": {
                "scheme": self.health_check.scheme.value,
                "path": self.health_check.path,
                "expectedStatuses": [
                    status.value for status in self.health_check.expected_statuses
                ],
                "timeoutSeconds": self.health_check.timeout_seconds.value,
            },
        }


@dataclass(frozen=True, slots=True)
class SshPublicKeyInventory:
    """Validated public key scoped to one Cloudfall environment."""

    resource_id: ResourceId
    owner: ResourceId
    environment: ResourceId
    public_key: OpenSshPublicKey
    lifecycle: SshPublicKeyLifecycle

    @property
    def is_active(self) -> bool:
        """Return whether the key may be offered to execution consumers."""
        return self.lifecycle is SshPublicKeyLifecycle.ACTIVE

    def as_dict(self) -> dict[str, object]:
        """Serialize non-secret SSH public-key state."""
        return {
            "id": self.resource_id.value,
            "owner": self.owner.value,
            "environment": self.environment.value,
            "algorithm": self.public_key.algorithm,
            "publicKey": self.public_key.value,
            "lifecycle": self.lifecycle.value,
        }


@dataclass(frozen=True, slots=True)
class LoggingSoftware:
    """Pinned package versions for the observability stack."""

    loki: PackageVersion
    grafana: PackageVersion
    alloy: PackageVersion
    prometheus: PackageVersion | None


@dataclass(frozen=True, slots=True)
class MetricsBackend:
    """Loopback-only Prometheus backend colocated with the logging backend."""

    listen_address: IpAddress
    port: TcpPort
    storage_path: AbsolutePath
    retention_hours: PositiveCount


@dataclass(frozen=True, slots=True)
class MetricsCollection:
    """Host metrics collection contract for every declared collector."""

    interval_seconds: PositiveCount


@dataclass(frozen=True, slots=True)
class LoggingMetrics:
    """Push-only host metrics topology through the mTLS gateway."""

    backend: MetricsBackend
    collection: MetricsCollection


@dataclass(frozen=True, slots=True)
class LoggingBackendStorage:
    """Loki storage contract for the first single-node slice."""

    storage_type: LoggingStorageType
    path: AbsolutePath


@dataclass(frozen=True, slots=True)
class LoggingBackend:
    """Loopback-only Loki backend placement and retention."""

    server_id: ResourceId
    listen_address: IpAddress
    port: TcpPort
    storage: LoggingBackendStorage
    retention_hours: PositiveCount


@dataclass(frozen=True, slots=True)
class LoggingGatewayTls:
    """Server-side mTLS material paths for the ingestion gateway."""

    certificate_path: AbsolutePath
    key_path: AbsolutePath
    client_ca_path: AbsolutePath


@dataclass(frozen=True, slots=True)
class LoggingGateway:
    """mTLS-only Loki ingestion endpoint."""

    server_name: Hostname
    listen_address: IpAddress
    port: TcpPort
    tls: LoggingGatewayTls


@dataclass(frozen=True, slots=True)
class LoggingGrafana:
    """Loopback-only Grafana endpoint colocated with Loki."""

    listen_address: IpAddress
    port: TcpPort
    admin_password_path: AbsolutePath


@dataclass(frozen=True, slots=True)
class LoggingClientTls:
    """Collector-side mTLS material paths."""

    ca_path: AbsolutePath
    certificate_path: AbsolutePath
    key_path: AbsolutePath


@dataclass(frozen=True, slots=True)
class LoggingJournalSource:
    """Bounded systemd-journal collection contract."""

    enabled: bool
    max_age_hours: PositiveCount


@dataclass(frozen=True, slots=True)
class LoggingFileSource:
    """Explicit application log-file source and ownership labels."""

    resource_id: ResourceId
    path: AbsolutePath
    server_ids: tuple[ResourceId, ...]
    project_id: ResourceId | None
    component_id: ResourceId | None

    def as_dict(self) -> dict[str, object]:
        """Serialize one source without including log content."""
        result: dict[str, object] = {
            "id": self.resource_id.value,
            "path": self.path.value,
            "servers": [server.value for server in self.server_ids],
        }
        if self.project_id is not None:
            result["project"] = self.project_id.value
        if self.component_id is not None:
            result["component"] = self.component_id.value
        return result


@dataclass(frozen=True, slots=True)
class LoggingCollectors:
    """Fleet placement and sources for Grafana Alloy."""

    server_ids: tuple[ResourceId, ...]
    client_tls: LoggingClientTls
    journal: LoggingJournalSource
    files: tuple[LoggingFileSource, ...]


@dataclass(frozen=True, slots=True)
class LoggingStackInventory:
    """Safe parallel-migration logging topology."""

    resource_id: ResourceId
    environment: ResourceId
    migration_mode: LoggingMigrationMode
    preserve_legacy_agents: bool
    software: LoggingSoftware
    metrics: LoggingMetrics
    backend: LoggingBackend
    gateway: LoggingGateway
    grafana: LoggingGrafana
    collectors: LoggingCollectors

    def as_dict(self) -> dict[str, object]:
        """Serialize the logging topology without certificate contents."""
        software: dict[str, object] = {
            "lokiPackageVersion": self.software.loki.value,
            "grafanaPackageVersion": self.software.grafana.value,
            "alloyPackageVersion": self.software.alloy.value,
        }
        if self.software.prometheus is not None:
            software["prometheusPackageVersion"] = self.software.prometheus.value
        return {
            "id": self.resource_id.value,
            "environment": self.environment.value,
            "migration": {
                "mode": self.migration_mode.value,
                "preserveLegacyAgents": self.preserve_legacy_agents,
            },
            "software": software,
            "metrics": {
                "backend": {
                    "listenAddress": self.metrics.backend.listen_address.value,
                    "port": self.metrics.backend.port.value,
                    "storagePath": self.metrics.backend.storage_path.value,
                    "retentionHours": (
                        self.metrics.backend.retention_hours.value
                    ),
                },
                "collection": {
                    "intervalSeconds": (
                        self.metrics.collection.interval_seconds.value
                    ),
                },
            },
            "backend": {
                "server": self.backend.server_id.value,
                "listenAddress": self.backend.listen_address.value,
                "port": self.backend.port.value,
                "storage": {
                    "type": self.backend.storage.storage_type.value,
                    "path": self.backend.storage.path.value,
                },
                "retentionHours": self.backend.retention_hours.value,
            },
            "gateway": {
                "serverName": self.gateway.server_name.value,
                "listenAddress": self.gateway.listen_address.value,
                "port": self.gateway.port.value,
                "tls": {
                    "certificatePath": self.gateway.tls.certificate_path.value,
                    "keyPath": self.gateway.tls.key_path.value,
                    "clientCaPath": self.gateway.tls.client_ca_path.value,
                },
            },
            "grafana": {
                "listenAddress": self.grafana.listen_address.value,
                "port": self.grafana.port.value,
                "adminPasswordPath": self.grafana.admin_password_path.value,
            },
            "collectors": {
                "servers": [server.value for server in self.collectors.server_ids],
                "clientTls": {
                    "caPath": self.collectors.client_tls.ca_path.value,
                    "certificatePath": (
                        self.collectors.client_tls.certificate_path.value
                    ),
                    "keyPath": self.collectors.client_tls.key_path.value,
                },
                "journal": {
                    "enabled": self.collectors.journal.enabled,
                    "maxAgeHours": self.collectors.journal.max_age_hours.value,
                },
                "files": [source.as_dict() for source in self.collectors.files],
            },
        }


@dataclass(frozen=True, slots=True)
class PlatformInventory:
    """Typed inventory of validated servers, projects, and components."""

    servers: tuple[ServerInventory, ...]
    profiles: tuple[HostProfileInventory, ...]
    projects: tuple[ProjectInventory, ...]
    components: tuple[ComponentInventory, ...]
    domains: tuple[DomainInventory, ...]
    ssh_public_keys: tuple[SshPublicKeyInventory, ...]
    logging_stacks: tuple[LoggingStackInventory, ...]

    @classmethod
    def from_state(cls, state: ValidatedState) -> PlatformInventory:
        """Project validated state into stable inventory records."""
        servers = tuple(
            _server_inventory(document)
            for document in state.resources(ResourceKind.SERVER)
        )
        profiles = tuple(
            _host_profile_inventory(document)
            for document in state.resources(ResourceKind.HOST_PROFILE)
        )
        projects = tuple(
            _project_inventory(document)
            for document in state.resources(ResourceKind.PROJECT)
        )
        components = tuple(
            _component_inventory(document)
            for document in state.resources(ResourceKind.COMPONENT)
        )
        domains = tuple(
            _domain_inventory(document)
            for document in state.resources(ResourceKind.DOMAIN)
        )
        ssh_public_keys = tuple(
            _ssh_public_key_inventory(document)
            for document in state.resources(ResourceKind.SSH_PUBLIC_KEY)
        )
        logging_stacks = tuple(
            _logging_stack_inventory(document)
            for document in state.resources(ResourceKind.LOGGING_STACK)
        )
        return cls(
            servers=servers,
            profiles=profiles,
            projects=projects,
            components=components,
            domains=domains,
            ssh_public_keys=ssh_public_keys,
            logging_stacks=logging_stacks,
        )

    def profile(self, profile_id: ResourceId) -> HostProfileInventory:
        """Return an existing desired host profile."""
        profile = next(
            (
                candidate
                for candidate in self.profiles
                if candidate.resource_id == profile_id
            ),
            None,
        )
        if profile is None:
            message = f"host profile does not exist: {profile_id}"
            raise KeyError(message)
        return profile

    def components_on_server(
        self, server_id: ResourceId
    ) -> tuple[ComponentInventory, ...]:
        """Return components assigned to an existing server."""
        if not any(server.resource_id == server_id for server in self.servers):
            message = f"server does not exist: {server_id}"
            raise KeyError(message)
        return tuple(
            component
            for component in self.components
            if server_id in component.server_ids
        )

    def servers_for_component(
        self, component_id: ResourceId
    ) -> tuple[ServerInventory, ...]:
        """Return servers assigned to an existing component."""
        component = next(
            (
                candidate
                for candidate in self.components
                if candidate.resource_id == component_id
            ),
            None,
        )
        if component is None:
            message = f"component does not exist: {component_id}"
            raise KeyError(message)
        assigned = frozenset(component.server_ids)
        return tuple(
            server for server in self.servers if server.resource_id in assigned
        )

    def as_dict(self) -> dict[str, object]:
        """Serialize the complete non-secret platform inventory."""
        return {
            "servers": [server.as_dict() for server in self.servers],
            "hostProfiles": [profile.as_dict() for profile in self.profiles],
            "projects": [project.as_dict() for project in self.projects],
            "components": [component.as_dict() for component in self.components],
            "domains": [domain.as_dict() for domain in self.domains],
            "sshPublicKeys": [key.as_dict() for key in self.ssh_public_keys],
            "loggingStacks": [stack.as_dict() for stack in self.logging_stacks],
        }


def _server_inventory(document: ResourceDocument) -> ServerInventory:
    spec = _mapping(document.content, "spec")
    ssh = _mapping(spec, "ssh")
    labels = _labels(spec.get("labels", {}))
    return ServerInventory(
        resource_id=document.key.resource_id,
        hostname=Hostname.from_boundary(spec.get("hostname")),
        address=ConnectionAddress.from_boundary(spec.get("address")),
        environment=ResourceId.from_boundary(spec.get("environment")),
        profile_id=ResourceId.from_boundary(spec.get("profile")),
        lifecycle=ServerLifecycle.from_boundary(spec.get("lifecycle")),
        ssh_user=LinuxUser.from_boundary(ssh.get("user")),
        ssh_port=TcpPort.from_boundary(ssh.get("port")),
        labels=labels,
        provider=_server_provider(spec.get("provider")),
        network=_server_network(spec.get("network")),
    )


def _server_provider(value: object) -> ServerProvider | None:
    if value is None:
        return None
    provider = _boundary_mapping(value, "server provider")
    server_id = provider.get("serverId")
    datacenter = provider.get("datacenter")
    return ServerProvider(
        name=ResourceId.from_boundary(provider.get("name")),
        server_id=(
            ProviderServerId.from_boundary(server_id) if server_id is not None else None
        ),
        datacenter=(
            DatacenterCode.from_boundary(datacenter) if datacenter is not None else None
        ),
    )


def _server_network(value: object) -> ServerNetwork | None:
    if value is None:
        return None
    network = _boundary_mapping(value, "server network")
    return ServerNetwork(
        ipv4=Ipv4Address.from_boundary(network.get("ipv4")),
        ipv6_cidr=Ipv6NetworkCidr.from_boundary(network.get("ipv6Cidr")),
        reverse_dns=Hostname.from_boundary(network.get("reverseDns")),
    )


def _host_profile_inventory(document: ResourceDocument) -> HostProfileInventory:
    spec = _mapping(document.content, "spec")
    os_requirement = _mapping(spec, "os")
    storage = _mapping(spec, "storage")
    raw_raid = storage.get("softwareRaid")
    raid = None if raw_raid is None else _mapping(storage, "softwareRaid")
    packages = _mapping(spec, "packages")
    services = _mapping(spec, "services")
    configuration = _mapping(spec, "configuration")
    return HostProfileInventory(
        resource_id=document.key.resource_id,
        os=OperatingSystemRequirement(
            distribution=OperatingSystemDistribution.from_boundary(
                os_requirement.get("distribution")
            ),
            versions=tuple(
                OperatingSystemMajorVersion.from_boundary(item)
                for item in _list(os_requirement.get("versions"), "OS versions")
            ),
            service_manager=ServiceManager.from_boundary(
                os_requirement.get("serviceManager")
            ),
        ),
        raid=(
            None
            if raid is None
            else RaidRequirement(
                level=RaidLevel.from_boundary(raid.get("level")),
                minimum_active_devices=PositiveCount.from_boundary(
                    raid.get("minimumActiveDevices")
                ),
                minimum_usable_bytes=ByteSize.from_boundary(
                    raid.get("minimumUsableBytes")
                ),
            )
        ),
        mounts=tuple(
            _mount_requirement(item)
            for item in _mapping_list(storage.get("mounts"), "mount requirements")
        ),
        required_packages=tuple(
            _package_requirement(item)
            for item in _mapping_list(packages.get("required"), "required packages")
        ),
        forbidden_packages=tuple(
            PackageName.from_boundary(item)
            for item in _list(packages.get("forbidden"), "forbidden packages")
        ),
        required_services=tuple(
            _service_requirement(item)
            for item in _mapping_list(services.get("required"), "required services")
        ),
        firewall=_firewall_requirement(spec.get("firewall")),
        configuration_files=tuple(
            _configuration_requirement(item)
            for item in _mapping_list(configuration.get("files"), "configuration files")
        ),
    )


def _project_inventory(document: ResourceDocument) -> ProjectInventory:
    spec = _mapping(document.content, "spec")
    return ProjectInventory(
        resource_id=document.key.resource_id,
        linux_user=LinuxUser.from_boundary(spec.get("linuxUser")),
        approval=DeploymentApproval.from_boundary(spec.get("approval")),
        component_ids=_resource_ids(spec.get("components"), "project components"),
    )


def _component_inventory(document: ResourceDocument) -> ComponentInventory:
    spec = _mapping(document.content, "spec")
    deployment = _mapping(spec, "deployment")
    return ComponentInventory(
        resource_id=document.key.resource_id,
        project_id=ResourceId.from_boundary(spec.get("project")),
        server_ids=_resource_ids(deployment.get("servers"), "component servers"),
        install_root=AbsolutePath.from_boundary(deployment.get("installRoot")),
    )


def _domain_inventory(document: ResourceDocument) -> DomainInventory:
    spec = _mapping(document.content, "spec")
    proxy = _mapping(spec, "proxy")
    raw_proxy_upstream = proxy.get("upstream")
    proxy_upstream = (
        None if raw_proxy_upstream is None else _mapping(proxy, "upstream")
    )
    origin = _mapping(spec, "origin")
    edge = _mapping(spec, "edge")
    tls = _mapping(spec, "tls")
    health = _mapping(spec, "healthCheck")
    return DomainInventory(
        resource_id=document.key.resource_id,
        primary_name=Hostname.from_boundary(spec.get("primaryName")),
        aliases=tuple(
            Hostname.from_boundary(item)
            for item in _list(spec.get("aliases"), "domain aliases")
        ),
        proxy=DomainProxy(
            server_id=ResourceId.from_boundary(proxy.get("server")),
            configuration_path=AbsolutePath.from_boundary(
                proxy.get("configurationPath")
            ),
            service_name=ServiceName.from_boundary(proxy.get("service")),
            upstream_address=(
                None
                if proxy_upstream is None
                else IpAddress.from_boundary(proxy_upstream.get("address"))
            ),
            upstream_port=(
                None
                if proxy_upstream is None
                else TcpPort.from_boundary(proxy_upstream.get("port"))
            ),
        ),
        origin=DomainOrigin(
            server_id=ResourceId.from_boundary(origin.get("server")),
            configuration_path=AbsolutePath.from_boundary(
                origin.get("configurationPath")
            ),
            service_name=ServiceName.from_boundary(origin.get("service")),
            scheme=HttpScheme.from_boundary(origin.get("scheme")),
            port=TcpPort.from_boundary(origin.get("port")),
            server_name=Hostname.from_boundary(origin.get("serverName")),
        ),
        edge=DomainEdge(
            provider=ResourceId.from_boundary(edge.get("provider")),
            mode=DnsMode.from_boundary(edge.get("mode")),
        ),
        tls_mode=TlsMode.from_boundary(tls.get("mode")),
        health_check=DomainHealthCheck(
            scheme=HttpScheme.from_boundary(health.get("scheme")),
            path=_string(health, "path"),
            expected_statuses=tuple(
                HttpStatusCode.from_boundary(item)
                for item in _list(
                    health.get("expectedStatuses"), "expected HTTP statuses"
                )
            ),
            timeout_seconds=PositiveCount.from_boundary(health.get("timeoutSeconds")),
        ),
    )


def _ssh_public_key_inventory(
    document: ResourceDocument,
) -> SshPublicKeyInventory:
    spec = _mapping(document.content, "spec")
    return SshPublicKeyInventory(
        resource_id=document.key.resource_id,
        owner=ResourceId.from_boundary(spec.get("owner")),
        environment=ResourceId.from_boundary(spec.get("environment")),
        public_key=OpenSshPublicKey.from_boundary(spec.get("publicKey")),
        lifecycle=SshPublicKeyLifecycle.from_boundary(spec.get("lifecycle")),
    )


def _logging_stack_inventory(
    document: ResourceDocument,
) -> LoggingStackInventory:
    spec = _mapping(document.content, "spec")
    migration = _mapping(spec, "migration")
    software = _mapping(spec, "software")
    raw_prometheus = software.get("prometheusPackageVersion")
    metrics = _mapping(spec, "metrics")
    metrics_backend = _mapping(metrics, "backend")
    metrics_collection = _mapping(metrics, "collection")
    backend = _mapping(spec, "backend")
    storage = _mapping(backend, "storage")
    gateway = _mapping(spec, "gateway")
    gateway_tls = _mapping(gateway, "tls")
    grafana = _mapping(spec, "grafana")
    collectors = _mapping(spec, "collectors")
    client_tls = _mapping(collectors, "clientTls")
    journal = _mapping(collectors, "journal")
    return LoggingStackInventory(
        resource_id=document.key.resource_id,
        environment=ResourceId.from_boundary(spec.get("environment")),
        migration_mode=LoggingMigrationMode.from_boundary(migration.get("mode")),
        preserve_legacy_agents=_boolean(
            migration.get("preserveLegacyAgents"), "preserve legacy agents"
        ),
        software=LoggingSoftware(
            loki=PackageVersion.from_boundary(software.get("lokiPackageVersion")),
            grafana=PackageVersion.from_boundary(software.get("grafanaPackageVersion")),
            alloy=PackageVersion.from_boundary(software.get("alloyPackageVersion")),
            prometheus=(
                PackageVersion.from_boundary(raw_prometheus)
                if raw_prometheus is not None
                else None
            ),
        ),
        metrics=LoggingMetrics(
            backend=MetricsBackend(
                listen_address=IpAddress.from_boundary(
                    metrics_backend.get("listenAddress")
                ),
                port=TcpPort.from_boundary(metrics_backend.get("port")),
                storage_path=AbsolutePath.from_boundary(
                    metrics_backend.get("storagePath")
                ),
                retention_hours=PositiveCount.from_boundary(
                    metrics_backend.get("retentionHours")
                ),
            ),
            collection=MetricsCollection(
                interval_seconds=PositiveCount.from_boundary(
                    metrics_collection.get("intervalSeconds")
                ),
            ),
        ),
        backend=LoggingBackend(
            server_id=ResourceId.from_boundary(backend.get("server")),
            listen_address=IpAddress.from_boundary(backend.get("listenAddress")),
            port=TcpPort.from_boundary(backend.get("port")),
            storage=LoggingBackendStorage(
                storage_type=LoggingStorageType.from_boundary(storage.get("type")),
                path=AbsolutePath.from_boundary(storage.get("path")),
            ),
            retention_hours=PositiveCount.from_boundary(backend.get("retentionHours")),
        ),
        gateway=LoggingGateway(
            server_name=Hostname.from_boundary(gateway.get("serverName")),
            listen_address=IpAddress.from_boundary(gateway.get("listenAddress")),
            port=TcpPort.from_boundary(gateway.get("port")),
            tls=LoggingGatewayTls(
                certificate_path=AbsolutePath.from_boundary(
                    gateway_tls.get("certificatePath")
                ),
                key_path=AbsolutePath.from_boundary(gateway_tls.get("keyPath")),
                client_ca_path=AbsolutePath.from_boundary(
                    gateway_tls.get("clientCaPath")
                ),
            ),
        ),
        grafana=LoggingGrafana(
            listen_address=IpAddress.from_boundary(grafana.get("listenAddress")),
            port=TcpPort.from_boundary(grafana.get("port")),
            admin_password_path=AbsolutePath.from_boundary(
                grafana.get("adminPasswordPath")
            ),
        ),
        collectors=LoggingCollectors(
            server_ids=_resource_ids(
                collectors.get("servers"), "logging collector servers"
            ),
            client_tls=LoggingClientTls(
                ca_path=AbsolutePath.from_boundary(client_tls.get("caPath")),
                certificate_path=AbsolutePath.from_boundary(
                    client_tls.get("certificatePath")
                ),
                key_path=AbsolutePath.from_boundary(client_tls.get("keyPath")),
            ),
            journal=LoggingJournalSource(
                enabled=_boolean(journal.get("enabled"), "journal enabled"),
                max_age_hours=PositiveCount.from_boundary(journal.get("maxAgeHours")),
            ),
            files=tuple(
                _logging_file_source(item)
                for item in _mapping_list(
                    collectors.get("files"), "logging file sources"
                )
            ),
        ),
    )


def _logging_file_source(content: Mapping[str, object]) -> LoggingFileSource:
    raw_project = content.get("project")
    raw_component = content.get("component")
    return LoggingFileSource(
        resource_id=ResourceId.from_boundary(content.get("id")),
        path=AbsolutePath.from_boundary(content.get("path")),
        server_ids=_resource_ids(content.get("servers"), "log-file servers"),
        project_id=(
            ResourceId.from_boundary(raw_project) if raw_project is not None else None
        ),
        component_id=(
            ResourceId.from_boundary(raw_component)
            if raw_component is not None
            else None
        ),
    )


def _mount_requirement(content: Mapping[str, object]) -> MountRequirement:
    return MountRequirement(
        path=AbsolutePath.from_boundary(content.get("path")),
        filesystem=FilesystemName.from_boundary(content.get("filesystem")),
        minimum_bytes=ByteSize.from_boundary(content.get("minimumBytes")),
    )


def _package_requirement(content: Mapping[str, object]) -> PackageRequirement:
    raw_version = content.get("version")
    version = (
        PackageVersion.from_boundary(raw_version) if raw_version is not None else None
    )
    return PackageRequirement(
        name=PackageName.from_boundary(content.get("name")),
        version=version,
    )


def _service_requirement(content: Mapping[str, object]) -> ServiceRequirement:
    return ServiceRequirement(
        name=SystemdUnitName.from_boundary(content.get("name")),
        state=RequiredServiceState.from_boundary(content.get("state")),
        status=RequiredServiceStatus.from_boundary(content.get("status")),
    )


def _firewall_requirement(value: object) -> FirewallRequirement | None:
    if value is None:
        return None
    firewall = _boundary_mapping(value, "host profile firewall")
    return FirewallRequirement(
        policy=FirewallPolicy.from_boundary(firewall.get("policy")),
        allowed_inbound=tuple(
            _firewall_rule(item)
            for item in _mapping_list(
                firewall.get("allowedInbound"), "firewall rules"
            )
        ),
    )


def _firewall_rule(content: Mapping[str, object]) -> FirewallRule:
    raw_description = content.get("description")
    if raw_description is not None and not isinstance(raw_description, str):
        message = "validated firewall rule description is not a string"
        raise TypeError(message)
    return FirewallRule(
        port=TcpPort.from_boundary(content.get("port")),
        protocol=NetworkProtocol.from_boundary(content.get("protocol")),
        description=raw_description,
    )


def firewall_rules_for_server(
    firewall: FirewallRequirement, ssh_port: TcpPort
) -> tuple[FirewallRule, ...]:
    """Return effective inbound rules with the server's SSH port guaranteed."""
    declared = {
        (rule.port.value, rule.protocol) for rule in firewall.allowed_inbound
    }
    if (ssh_port.value, NetworkProtocol.TCP) in declared:
        return firewall.allowed_inbound
    ssh_rule = FirewallRule(
        port=ssh_port,
        protocol=NetworkProtocol.TCP,
        description="ssh",
    )
    return (ssh_rule, *firewall.allowed_inbound)


def _configuration_requirement(
    content: Mapping[str, object],
) -> ConfigurationFileRequirement:
    raw_owner = content.get("owner")
    raw_group = content.get("group")
    raw_mode = content.get("mode")
    raw_sha256 = content.get("sha256")
    return ConfigurationFileRequirement(
        path=AbsolutePath.from_boundary(content.get("path")),
        capture=ConfigCapture.from_boundary(content.get("capture")),
        owner=LinuxUser.from_boundary(raw_owner) if raw_owner is not None else None,
        group=LinuxUser.from_boundary(raw_group) if raw_group is not None else None,
        mode=FileMode.from_boundary(raw_mode) if raw_mode is not None else None,
        sha256=(
            Sha256Digest.from_boundary(raw_sha256) if raw_sha256 is not None else None
        ),
    )


def _mapping(content: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = content.get(key)
    if not isinstance(value, dict) or not all(
        isinstance(child_key, str) for child_key in value
    ):
        message = f"validated field {key!r} is not an object"
        raise TypeError(message)
    return cast("Mapping[str, object]", value)


def _string(content: Mapping[str, object], key: str) -> str:
    value = content.get(key)
    if not isinstance(value, str):
        message = f"validated field {key!r} is not a string"
        raise TypeError(message)
    return value


def _boundary_mapping(value: object, concept: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        message = f"validated {concept} is not an object"
        raise TypeError(message)
    return cast("Mapping[str, object]", value)


def _resource_ids(value: object, concept: str) -> tuple[ResourceId, ...]:
    if not isinstance(value, list):
        message = f"validated {concept} is not a list"
        raise TypeError(message)
    return tuple(ResourceId.from_boundary(item) for item in value)


def _list(value: object, concept: str) -> tuple[object, ...]:
    if not isinstance(value, list):
        message = f"validated {concept} is not a list"
        raise TypeError(message)
    return tuple(value)


def _mapping_list(value: object, concept: str) -> tuple[Mapping[str, object], ...]:
    items = _list(value, concept)
    mappings: list[Mapping[str, object]] = []
    for item in items:
        if not isinstance(item, dict) or not all(isinstance(key, str) for key in item):
            message = f"validated {concept} contains a non-object item"
            raise TypeError(message)
        mappings.append(cast("Mapping[str, object]", item))
    return tuple(mappings)


def _labels(value: object) -> tuple[ServerLabel, ...]:
    if not isinstance(value, dict):
        message = "validated server labels are not an object"
        raise TypeError(message)
    labels: list[ServerLabel] = []
    for key, label_value in value.items():
        if not isinstance(label_value, str):
            message = f"validated server label {key!r} is not a string"
            raise TypeError(message)
        labels.append(
            ServerLabel(
                key=ResourceId.from_boundary(key),
                value=label_value,
            )
        )
    return tuple(sorted(labels, key=lambda label: label.key.value))


def _boolean(value: object, concept: str) -> bool:
    if not isinstance(value, bool):
        message = f"validated {concept} is not a boolean"
        raise TypeError(message)
    return value
