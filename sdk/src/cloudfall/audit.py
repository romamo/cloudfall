"""Compare desired host profiles with validated server observations."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from cloudfall.inventory import (
        ConfigurationFileRequirement,
        HostProfileInventory,
        PlatformInventory,
        RaidRequirement,
        ServerInventory,
    )
    from cloudfall.observation import ObservationSet, ObservedServerSnapshot

_RAID_HEALTH_PATTERN = re.compile(r"\[(\d+)/(\d+)\]\s+\[([U_]+)\]")


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
    audits = tuple(
        _audit_server(
            server,
            inventory.profile(server.profile_id),
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
    checks: list[AuditCheck] = []
    for requirement in profile.required_services:
        raw_actual = services.get(requirement.name.value)
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
