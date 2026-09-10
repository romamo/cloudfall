"""Compare desired host profiles with validated server observations."""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, cast

from cloudfall.inventory import firewall_rules_for_server

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from cloudfall.domain import ResourceId
    from cloudfall.inventory import (
        AlertRuleInventory,
        ConfigurationFileRequirement,
        FirewallRequirement,
        HostProfileInventory,
        LoggingStackInventory,
        PlatformInventory,
        RaidRequirement,
        ServerInventory,
        ServiceInventory,
    )
    from cloudfall.observation import ObservationSet, ObservedServerSnapshot

_RAID_HEALTH_PATTERN = re.compile(r"\[(\d+)/(\d+)\]\s+\[([U_]+)\]")
_MANAGED_FIREWALL_TABLE = "cloudfall"
_MANAGED_FIREWALL_INPUT_CHAIN = "input"
_MANAGED_FIREWALL_INPUT_POLICY = "drop"
_LOOPBACK_ADDRESSES = frozenset({"127.0.0.1", "::1"})
_MINIMUM_SOCKET_FIELDS = 5


class AuditStatus(StrEnum):
    """Compliance status of an audit check or report."""

    COMPLIANT = "compliant"
    DRIFT = "drift"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class AuditCheck:
    """One desired-versus-observed comparison."""

    check: str
    status: AuditStatus
    desired: object
    observed: object
    message: str

    def as_dict(self) -> dict[str, object]:
        """Serialize a stable machine-readable check."""
        return {
            "check": self.check,
            "status": self.status.value,
            "desired": self.desired,
            "observed": self.observed,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class ServerAudit:
    """Audit result for one desired server."""

    server_id: str
    profile_id: str
    status: AuditStatus
    observation: str | None
    observed_at: str | None
    checks: tuple[AuditCheck, ...]

    def as_dict(self) -> dict[str, object]:
        """Serialize a server audit."""
        return {
            "server": self.server_id,
            "profile": self.profile_id,
            "status": self.status.value,
            "observation": self.observation,
            "observedAt": self.observed_at,
            "checks": [check.as_dict() for check in self.checks],
        }


@dataclass(frozen=True, slots=True)
class AuditReport:
    """Fleet-wide desired-versus-observed audit report."""

    status: AuditStatus
    servers: tuple[ServerAudit, ...]
    unmatched_observations: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        """Serialize the report with deterministic summary counts."""
        server_counts = Counter(server.status.value for server in self.servers)
        check_counts = Counter(
            check.status.value
            for server in self.servers
            for check in server.checks
        )
        return {
            "status": self.status.value,
            "summary": {
                "servers": {
                    status.value: server_counts[status.value]
                    for status in AuditStatus
                },
                "checks": {
                    status.value: check_counts[status.value]
                    for status in AuditStatus
                },
            },
            "unmatchedObservations": list(self.unmatched_observations),
            "servers": [server.as_dict() for server in self.servers],
        }


def audit_inventory(
    inventory: PlatformInventory, observations: ObservationSet
) -> AuditReport:
    """Audit every desired server against its validated observation."""
    stacks_by_backend = {
        stack.backend.server_id: stack for stack in inventory.logging_stacks
    }
    audits = tuple(
        _audit_server(
            server,
            inventory.profile(server.profile_id),
            tuple(
                service
                for service in inventory.services
                if service.server_id == server.resource_id
            ),
            _alert_rules_for_backend(server, stacks_by_backend, inventory),
            observations.for_server(server.resource_id),
        )
        for server in inventory.servers
    )
    desired_ids = frozenset(server.resource_id for server in inventory.servers)
    unmatched = tuple(
        sorted(
            snapshot.server_id.value
            for snapshot in observations.snapshots
            if snapshot.server_id not in desired_ids
        )
    )
    return AuditReport(
        status=_aggregate_status(audit.status for audit in audits),
        servers=audits,
        unmatched_observations=unmatched,
    )


def _audit_server(
    server: ServerInventory,
    profile: HostProfileInventory,
    services: tuple[ServiceInventory, ...],
    alert_rules: tuple[AlertRuleInventory, ...],
    snapshot: ObservedServerSnapshot | None,
) -> ServerAudit:
    if snapshot is None:
        check = AuditCheck(
            check="observation.available",
            status=AuditStatus.UNKNOWN,
            desired="validated observation",
            observed=None,
            message="no observation was supplied for this server",
        )
        return ServerAudit(
            server_id=server.resource_id.value,
            profile_id=profile.resource_id.value,
            status=AuditStatus.UNKNOWN,
            observation=None,
            observed_at=None,
            checks=(check,),
        )

    checks: list[AuditCheck] = []
    checks.append(
        _comparison(
            "profile.id",
            profile.resource_id.value,
            snapshot.profile_id.value,
            matches=profile.resource_id == snapshot.profile_id,
        )
    )
    checks.extend(_audit_os(profile, snapshot))
    if profile.raid is not None:
        checks.extend(_audit_raid(profile.raid, snapshot))
    checks.extend(_audit_mounts(profile, snapshot))
    checks.extend(_audit_packages(profile, snapshot))
    checks.extend(_audit_services(profile, snapshot))
    if profile.firewall is not None:
        checks.extend(_audit_firewall(server, profile.firewall, snapshot))
    checks.extend(_audit_service_binds(services, snapshot))
    checks.extend(_audit_alert_rules(alert_rules, snapshot))
    checks.extend(_audit_configuration(profile, snapshot))
    return ServerAudit(
        server_id=server.resource_id.value,
        profile_id=profile.resource_id.value,
        status=_aggregate_status(check.status for check in checks),
        observation=str(snapshot.source),
        observed_at=_string(snapshot.spec, "observedAt"),
        checks=tuple(checks),
    )


def _audit_os(
    profile: HostProfileInventory, snapshot: ObservedServerSnapshot
) -> tuple[AuditCheck, ...]:
    observed = _mapping(snapshot.spec, "os")
    distribution = _string(observed, "distribution")
    major_version = _string(observed, "majorVersion")
    service_manager = _string(observed, "serviceManager")
    versions = [version.value for version in profile.os.versions]
    return (
        _comparison(
            "os.distribution",
            profile.os.distribution.value,
            distribution,
            matches=distribution == profile.os.distribution.value,
        ),
        _comparison(
            "os.majorVersion",
            versions,
            major_version,
            matches=major_version in versions,
        ),
        _comparison(
            "os.serviceManager",
            profile.os.service_manager.value,
            service_manager,
            matches=service_manager == profile.os.service_manager.value,
        ),
    )


def _audit_raid(
    requirement: RaidRequirement, snapshot: ObservedServerSnapshot
) -> tuple[AuditCheck, ...]:
    storage = _mapping(snapshot.spec, "storage")
    software_raid = _mapping(storage, "softwareRaid")
    mdstat = _string(software_raid, "mdstat")
    raid_level = requirement.level.value
    health_matches = tuple(_RAID_HEALTH_PATTERN.finditer(mdstat))
    active_devices = max(
        (match.group(3).count("U") for match in health_matches),
        default=0,
    )
    raid_sizes = [
        _integer(device, "size")
        for device in _flatten_block_devices(
            _mapping_sequence(storage, "blockDevices")
        )
        if _string(device, "type") == raid_level
    ]
    usable_bytes = max(raid_sizes, default=0)
    return (
        _comparison(
            "storage.softwareRaid.level",
            raid_level,
            _observed_raid_levels(mdstat),
            matches=f"active {raid_level}" in mdstat,
        ),
        _comparison(
            "storage.softwareRaid.activeDevices",
            {"minimum": requirement.minimum_active_devices.value},
            active_devices,
            matches=active_devices >= requirement.minimum_active_devices.value,
        ),
        _comparison(
            "storage.softwareRaid.usableBytes",
            {"minimum": requirement.minimum_usable_bytes.value},
            usable_bytes,
            matches=usable_bytes >= requirement.minimum_usable_bytes.value,
        ),
    )


def _audit_mounts(
    profile: HostProfileInventory, snapshot: ObservedServerSnapshot
) -> tuple[AuditCheck, ...]:
    storage = _mapping(snapshot.spec, "storage")
    filesystems = {
        _string(item, "target"): item
        for item in _mapping_sequence(storage, "filesystems")
    }
    checks: list[AuditCheck] = []
    for requirement in profile.mounts:
        actual = filesystems.get(requirement.path.value)
        desired = {
            "filesystem": requirement.filesystem.value,
            "minimumBytes": requirement.minimum_bytes.value,
        }
        if actual is None:
            checks.append(
                _comparison(
                    f"storage.mounts[{requirement.path.value}]",
                    desired,
                    None,
                    matches=False,
                )
            )
            continue
        observed_size = actual.get("size")
        size_matches = (
            isinstance(observed_size, int)
            and not isinstance(observed_size, bool)
            and observed_size >= requirement.minimum_bytes.value
        )
        observed = {
            "filesystem": _string(actual, "fstype"),
            "size": observed_size,
            "source": _string(actual, "source"),
        }
        checks.append(
            _comparison(
                f"storage.mounts[{requirement.path.value}]",
                desired,
                observed,
                matches=(
                    observed["filesystem"] == requirement.filesystem.value
                    and size_matches
                ),
            )
        )
    return tuple(checks)


def _audit_packages(
    profile: HostProfileInventory, snapshot: ObservedServerSnapshot
) -> tuple[AuditCheck, ...]:
    packages: dict[str, list[Mapping[str, object]]] = {}
    for entry in _mapping_sequence(snapshot.spec, "packages"):
        packages.setdefault(_string(entry, "name"), []).append(entry)
    checks: list[AuditCheck] = []
    for requirement in profile.required_packages:
        entries = tuple(packages.get(requirement.name.value, []))
        versions = [_string(entry, "version") for entry in entries]
        desired: object = (
            {"installed": True}
            if requirement.version is None
            else {"version": requirement.version.value}
        )
        matches = bool(entries) and (
            requirement.version is None
            or requirement.version.value in versions
        )
        checks.append(
            _comparison(
                f"packages.required[{requirement.name.value}]",
                desired,
                {"versions": versions} if entries else None,
                matches=matches,
            )
        )
    for package in profile.forbidden_packages:
        present = package.value in packages
        checks.append(
            _comparison(
                f"packages.forbidden[{package.value}]",
                {"installed": False},
                {"installed": present},
                matches=not present,
            )
        )
    return tuple(checks)


def _audit_services(
    profile: HostProfileInventory, snapshot: ObservedServerSnapshot
) -> tuple[AuditCheck, ...]:
    services = _mapping(snapshot.spec, "services")
    timers = _mapping(snapshot.spec, "timers")
    checks: list[AuditCheck] = []
    for requirement in profile.required_services:
        evidence = timers if requirement.name.is_timer else services
        raw_actual = evidence.get(requirement.name.value)
        actual = _optional_mapping(raw_actual)
        desired = {
            "state": requirement.state.value,
            "status": requirement.status.value,
        }
        observed = (
            {
                "state": _string(actual, "state"),
                "status": _string(actual, "status"),
            }
            if actual is not None
            else None
        )
        checks.append(
            _comparison(
                f"services.required[{requirement.name.value}]",
                desired,
                observed,
                matches=observed == desired,
            )
        )
    return tuple(checks)


@dataclass(frozen=True, slots=True)
class _ObservedFirewallTable:
    """Managed nftables table facts parsed from observation evidence."""

    input_policy: str | None
    allowed_inbound: tuple[tuple[str, int], ...]


def _audit_firewall(
    server: ServerInventory,
    firewall: FirewallRequirement,
    snapshot: ObservedServerSnapshot,
) -> tuple[AuditCheck, ...]:
    evidence = _mapping(snapshot.spec, "firewall")
    nft_available = evidence.get("nftAvailable") is True
    raw_table = evidence.get("managedTableJson")
    table = (
        _parse_managed_firewall_table(raw_table)
        if nft_available and isinstance(raw_table, str)
        else None
    )
    expected_rules = sorted(
        (rule.protocol.value, rule.port.value)
        for rule in firewall_rules_for_server(firewall, server.ssh_port)
    )
    observed_rules = None if table is None else sorted(table.allowed_inbound)
    return (
        _comparison(
            "firewall.managedTable",
            {"table": f"inet {_MANAGED_FIREWALL_TABLE}", "present": True},
            {"nftAvailable": nft_available, "present": table is not None},
            matches=table is not None,
        ),
        _comparison(
            "firewall.inputPolicy",
            _MANAGED_FIREWALL_INPUT_POLICY,
            None if table is None else table.input_policy,
            matches=(
                table is not None
                and table.input_policy == _MANAGED_FIREWALL_INPUT_POLICY
            ),
        ),
        _comparison(
            "firewall.allowedInbound",
            [f"{protocol}/{port}" for protocol, port in expected_rules],
            (
                None
                if observed_rules is None
                else [f"{protocol}/{port}" for protocol, port in observed_rules]
            ),
            matches=observed_rules == expected_rules,
        ),
    )


def _parse_managed_firewall_table(raw: str) -> _ObservedFirewallTable | None:
    try:
        parsed: object = json.loads(raw)
    except json.JSONDecodeError:
        return None
    document = _optional_mapping(parsed)
    if document is None:
        return None
    entries = _mapping_values_sequence(document.get("nftables"))
    input_policy: str | None = None
    allowed: list[tuple[str, int]] = []
    table_present = False
    for entry in entries:
        table = _optional_mapping(entry.get("table"))
        if table is not None and table.get("name") == _MANAGED_FIREWALL_TABLE:
            table_present = True
        chain = _optional_mapping(entry.get("chain"))
        if (
            chain is not None
            and chain.get("table") == _MANAGED_FIREWALL_TABLE
            and chain.get("name") == _MANAGED_FIREWALL_INPUT_CHAIN
            and isinstance(chain.get("policy"), str)
        ):
            input_policy = cast("str", chain.get("policy"))
        rule = _optional_mapping(entry.get("rule"))
        if (
            rule is not None
            and rule.get("table") == _MANAGED_FIREWALL_TABLE
            and rule.get("chain") == _MANAGED_FIREWALL_INPUT_CHAIN
        ):
            endpoint = _accepted_inbound_endpoint(rule)
            if endpoint is not None:
                allowed.append(endpoint)
    if not table_present:
        return None
    return _ObservedFirewallTable(
        input_policy=input_policy,
        allowed_inbound=tuple(allowed),
    )


def _accepted_inbound_endpoint(
    rule: Mapping[str, object],
) -> tuple[str, int] | None:
    expressions = _mapping_values_sequence(rule.get("expr"))
    endpoint: tuple[str, int] | None = None
    accepts = False
    for expression in expressions:
        if "accept" in expression:
            accepts = True
            continue
        match = _optional_mapping(expression.get("match"))
        if match is None:
            continue
        left = _optional_mapping(match.get("left"))
        payload = None if left is None else _optional_mapping(left.get("payload"))
        port = match.get("right")
        if (
            payload is not None
            and payload.get("field") == "dport"
            and payload.get("protocol") in ("tcp", "udp")
            and isinstance(port, int)
            and not isinstance(port, bool)
        ):
            endpoint = (cast("str", payload.get("protocol")), port)
    if not accepts:
        return None
    return endpoint


@dataclass(frozen=True, slots=True)
class _SocketListener:
    """One listening socket parsed from collected evidence."""

    protocol: str
    address: str
    port: int


def _audit_service_binds(
    services: tuple[ServiceInventory, ...],
    snapshot: ObservedServerSnapshot,
) -> tuple[AuditCheck, ...]:
    if not services:
        return ()
    network = _mapping(snapshot.spec, "network")
    listeners = _parse_listening_sockets(_string(network, "listeningSockets"))
    checks: list[AuditCheck] = []
    for service in services:
        port = service.bind.port.value
        address = service.bind.address.value
        on_port = tuple(
            listener
            for listener in listeners
            if listener.protocol == "tcp" and listener.port == port
        )
        declared_present = any(
            listener.address == address for listener in on_port
        )
        exposed = tuple(
            listener
            for listener in on_port
            if listener.address not in _LOOPBACK_ADDRESSES
        )
        observed = (
            {
                "listeners": [
                    f"{listener.address}:{listener.port}"
                    for listener in on_port
                ]
            }
            if on_port
            else None
        )
        checks.append(
            _comparison(
                f"services.bind[{service.resource_id.value}]",
                {
                    "protocol": "tcp",
                    "address": address,
                    "port": port,
                    "loopbackOnly": True,
                },
                observed,
                matches=declared_present and not exposed,
            )
        )
    return tuple(checks)


def _parse_listening_sockets(raw: str) -> tuple[_SocketListener, ...]:
    listeners: list[_SocketListener] = []
    for line in raw.splitlines():
        fields = line.split()
        if len(fields) < _MINIMUM_SOCKET_FIELDS:
            continue
        protocol = fields[0]
        local = fields[4]
        address, separator, raw_port = local.rpartition(":")
        if not separator or not raw_port.isdigit():
            continue
        address = address.strip("[]").split("%", maxsplit=1)[0]
        listeners.append(
            _SocketListener(
                protocol=protocol,
                address=address,
                port=int(raw_port),
            )
        )
    return tuple(listeners)


def _alert_rules_for_backend(
    server: ServerInventory,
    stacks_by_backend: Mapping[ResourceId, LoggingStackInventory],
    inventory: PlatformInventory,
) -> tuple[AlertRuleInventory, ...]:
    stack = stacks_by_backend.get(server.resource_id)
    if stack is None:
        return ()
    return tuple(
        rule
        for rule in inventory.alert_rules
        if rule.environment == stack.environment
    )


def _audit_alert_rules(
    alert_rules: tuple[AlertRuleInventory, ...],
    snapshot: ObservedServerSnapshot,
) -> tuple[AuditCheck, ...]:
    if not alert_rules:
        return ()
    raw_alerting = _optional_mapping(snapshot.spec.get("alerting"))
    observed_rules = (
        _observed_alert_rules(raw_alerting)
        if raw_alerting is not None
        and raw_alerting.get("prometheusAvailable") is True
        else None
    )
    checks: list[AuditCheck] = []
    for rule in alert_rules:
        rule_name = rule.resource_id.value.replace("-", "_")
        desired = {"name": rule_name, "health": "ok", "loaded": True}
        observed = (
            observed_rules.get(rule_name) if observed_rules is not None else None
        )
        checks.append(
            _comparison(
                f"alerting.rules[{rule.resource_id.value}]",
                desired,
                observed,
                matches=(
                    observed is not None and observed.get("health") == "ok"
                ),
            )
        )
    return tuple(checks)


def _observed_alert_rules(
    alerting: Mapping[str, object],
) -> dict[str, dict[str, object]]:
    raw_rules_json = alerting.get("rulesJson")
    if not isinstance(raw_rules_json, str):
        return {}
    try:
        payload = cast("object", json.loads(raw_rules_json))
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    if not isinstance(data, dict):
        return {}
    observed: dict[str, dict[str, object]] = {}
    for group in _mapping_values_sequence(data.get("groups")):
        for raw_rule in _mapping_values_sequence(group.get("rules")):
            if raw_rule.get("type") != "alerting":
                continue
            name = raw_rule.get("name")
            if not isinstance(name, str):
                continue
            observed[name] = {
                "name": name,
                "health": raw_rule.get("health"),
                "state": raw_rule.get("state"),
                "loaded": True,
            }
    return observed


def _audit_configuration(
    profile: HostProfileInventory, snapshot: ObservedServerSnapshot
) -> tuple[AuditCheck, ...]:
    evidence = {
        _string(item, "path"): item
        for item in _mapping_sequence(snapshot.spec, "configuration")
    }
    return tuple(
        _audit_configuration_file(requirement, evidence.get(requirement.path.value))
        for requirement in profile.configuration_files
    )


def _audit_configuration_file(
    requirement: ConfigurationFileRequirement,
    actual: Mapping[str, object] | None,
) -> AuditCheck:
    desired: dict[str, object] = {
        "exists": True,
        "capture": requirement.capture.value,
    }
    if requirement.owner is not None:
        desired["owner"] = requirement.owner.value
    if requirement.group is not None:
        desired["group"] = requirement.group.value
    if requirement.mode is not None:
        desired["mode"] = requirement.mode.value
    if requirement.sha256 is not None:
        desired["sha256"] = requirement.sha256.value

    if actual is None:
        return _comparison(
            f"configuration.files[{requirement.path.value}]",
            desired,
            None,
            matches=False,
        )
    observed = {
        key: actual.get(key)
        for key in ("exists", "capture", "owner", "group", "mode", "sha256")
    }
    matches = actual.get("exists") is True
    if requirement.capture.value == "hash":
        matches = matches and actual.get("sha256") is not None
    for key, expected in desired.items():
        matches = matches and observed.get(key) == expected
    return _comparison(
        f"configuration.files[{requirement.path.value}]",
        desired,
        observed,
        matches=matches,
    )


def _comparison(
    check: str, desired: object, observed: object, *, matches: bool
) -> AuditCheck:
    status = AuditStatus.COMPLIANT if matches else AuditStatus.DRIFT
    message = "desired state matches" if matches else "desired state does not match"
    return AuditCheck(
        check=check,
        status=status,
        desired=desired,
        observed=observed,
        message=message,
    )


def _aggregate_status(statuses: Iterable[AuditStatus]) -> AuditStatus:
    values = tuple(statuses)
    if AuditStatus.DRIFT in values:
        return AuditStatus.DRIFT
    if AuditStatus.UNKNOWN in values:
        return AuditStatus.UNKNOWN
    return AuditStatus.COMPLIANT


def _observed_raid_levels(mdstat: str) -> list[str]:
    return sorted(
        {
            level
            for level in ("raid0", "raid1", "raid5", "raid6", "raid10")
            if f"active {level}" in mdstat
        }
    )


def _flatten_block_devices(
    devices: tuple[Mapping[str, object], ...],
) -> tuple[Mapping[str, object], ...]:
    flattened: list[Mapping[str, object]] = []
    for device in devices:
        flattened.append(device)
        raw_children = device.get("children", [])
        flattened.extend(_flatten_block_devices(_mapping_values_sequence(raw_children)))
    return tuple(flattened)


def _mapping(content: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = content.get(key)
    result = _optional_mapping(value)
    if result is None:
        message = f"validated field {key!r} is not an object"
        raise TypeError(message)
    return result


def _optional_mapping(value: object) -> Mapping[str, object] | None:
    if not isinstance(value, dict) or not all(
        isinstance(key, str) for key in value
    ):
        return None
    return cast("Mapping[str, object]", value)


def _mapping_sequence(
    content: Mapping[str, object], key: str
) -> tuple[Mapping[str, object], ...]:
    return _mapping_values_sequence(content.get(key))


def _mapping_values_sequence(value: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, list):
        return ()
    result: list[Mapping[str, object]] = []
    for item in value:
        mapping = _optional_mapping(item)
        if mapping is None:
            return ()
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
