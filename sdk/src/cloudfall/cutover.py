"""Explicit cutover machinery: TTL evidence, parallel-run, rollback data.

The DNS switch is the one irreversible-feeling moment of a migration, so
everything around it is made explicit and evidence-backed: authoritative
TTLs are measured (not assumed) with a minimal stdlib DNS client, the new
origin is proven to serve every declared domain before any record changes,
and the pre-switch answers are captured so the rollback window is a
recorded fact with exact revert instructions rather than folklore.
"""

from __future__ import annotations

import os
import socket
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from cloudfall.domain import HttpScheme, PositiveCount, TcpPort
from cloudfall.service_evidence import ProbeTarget

if TYPE_CHECKING:
    from cloudfall.inventory import DomainInventory, PlatformInventory
    from cloudfall.service_evidence import DomainNetworkClient

_RESOLV_CONF = Path("/etc/resolv.conf")
_DNS_PORT = 53
_DNS_TIMEOUT_SECONDS = 5.0
_TYPE_A = 1
_TYPE_NS = 2
_TYPE_CNAME = 5
_TYPE_AAAA = 28
_TYPE_NAMES = {_TYPE_A: "A", _TYPE_NS: "NS", _TYPE_CNAME: "CNAME",
               _TYPE_AAAA: "AAAA"}
_CLASS_IN = 1
_POINTER_MASK = 0xC0
_HTTP_PORT = 80


class CutoverError(RuntimeError):
    """Structured cutover failure suitable for plan envelopes."""

    def __init__(self, code: str, message: str) -> None:
        """Capture a stable error code alongside the human message."""
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class DnsAnswer:
    """One authoritative DNS answer with its true TTL."""

    name: str
    record_type: str
    ttl: int
    value: str

    def as_dict(self) -> dict[str, object]:
        """Serialize for plan details and rollback instructions."""
        return {
            "name": self.name,
            "type": self.record_type,
            "ttl": self.ttl,
            "value": self.value,
        }


class DnsProbe(Protocol):
    """Source of authoritative DNS answers."""

    def authoritative_answers(self, hostname: str) -> tuple[DnsAnswer, ...]:
        """Return the zone's authoritative answers for one hostname."""
        ...


@dataclass(frozen=True, slots=True)
class SystemDnsProbe:
    """Authoritative resolver walking the zone from the system resolver."""

    resolver_address: str | None = None

    def authoritative_answers(self, hostname: str) -> tuple[DnsAnswer, ...]:
        """Query the zone's own nameserver so TTLs are not cache decay."""
        resolver = (
            self.resolver_address
            if self.resolver_address is not None
            else _system_resolver()
        )
        nameserver = _zone_nameserver(resolver, hostname)
        answers = _query(nameserver, hostname, _TYPE_A)
        answers += _query(nameserver, hostname, _TYPE_AAAA)
        deduplicated = tuple(dict.fromkeys(answers))
        if not deduplicated:
            message = (
                f"the authoritative nameserver returned no records for "
                f"{hostname}"
            )
            code = "cutover_dns_empty"
            raise CutoverError(code, message)
        return deduplicated


def check_ttl(
    inventory: PlatformInventory,
    probe: DnsProbe,
    max_ttl_seconds: int,
) -> tuple[dict[str, object], tuple[str, ...]]:
    """Measure authoritative TTLs; report every domain above the bound."""
    domains: list[dict[str, object]] = []
    offenders: list[str] = []
    for domain in _dns_only_domains(inventory):
        name = domain.primary_name.value
        try:
            answers = probe.authoritative_answers(name)
        except CutoverError as error:
            offenders.append(f"{name}: {error.message}")
            domains.append({"domain": name, "error": error.message})
            continue
        highest = max(answer.ttl for answer in answers)
        domains.append(
            {
                "domain": name,
                "answers": [answer.as_dict() for answer in answers],
                "maxTtl": highest,
            }
        )
        if highest > max_ttl_seconds:
            offenders.append(
                f"{name}: authoritative TTL {highest}s exceeds "
                f"{max_ttl_seconds}s"
            )
    detail: dict[str, object] = {
        "maxTtlSeconds": max_ttl_seconds,
        "domains": domains,
    }
    return detail, tuple(offenders)


def parallel_run(
    inventory: PlatformInventory,
    client: DomainNetworkClient,
) -> tuple[dict[str, object], tuple[str, ...]]:
    """Prove the new origin serves every domain before DNS changes."""
    servers = {server.resource_id: server for server in inventory.servers}
    probes: list[dict[str, object]] = []
    failures: list[str] = []
    for domain in inventory.domains:
        name = domain.primary_name.value
        proxy = servers[domain.proxy.server_id]
        expected = tuple(
            status.value for status in domain.health_check.expected_statuses
        )
        target = ProbeTarget(
            address=proxy.address,
            port=TcpPort(_HTTP_PORT),
            server_name=domain.primary_name,
            scheme=HttpScheme.HTTP,
            path=domain.health_check.path,
            timeout=PositiveCount(
                domain.health_check.timeout_seconds.value
            ),
        )
        endpoint = client.request(target).endpoint
        observed = (
            endpoint.status.value if endpoint.status is not None else None
        )
        entry: dict[str, object] = {
            "domain": name,
            "origin": proxy.address.value,
            "path": domain.health_check.path,
            "status": observed,
            "expected": list(expected),
        }
        if endpoint.error is not None:
            entry["error"] = endpoint.error
        probes.append(entry)
        if not endpoint.reachable or observed is None:
            failures.append(
                f"{name}: origin unreachable: "
                f"{endpoint.error or 'no response'}"
            )
        elif observed not in expected:
            failures.append(
                f"{name}: origin answered {observed} on "
                f"{domain.health_check.path}, expected one of "
                f"{sorted(expected)}"
            )
    detail: dict[str, object] = {"probes": probes}
    return detail, tuple(failures)


def rollback_instructions(
    previous: dict[str, object],
    window_hours: int,
    switched_at: str,
    window_ends_at: str,
) -> dict[str, object]:
    """Turn the pre-switch answers into an explicit rollback recipe."""
    return {
        "windowHours": window_hours,
        "switchedAt": switched_at,
        "windowEndsAt": window_ends_at,
        "previous": previous,
        "instruction": (
            "to roll the cutover back within the window, restore the "
            "previous DNS answers exactly as recorded, wait one TTL for "
            "propagation, and keep the imported services running until "
            "traffic confirms the revert"
        ),
    }


def _dns_only_domains(
    inventory: PlatformInventory,
) -> tuple[DomainInventory, ...]:
    return tuple(
        domain
        for domain in inventory.domains
        if domain.edge.mode.value == "dns-only"
    )


def _system_resolver() -> str:
    code = "cutover_resolver_missing"
    if not _RESOLV_CONF.is_file():
        message = f"no system resolver configuration at {_RESOLV_CONF}"
        raise CutoverError(code, message)
    for line in _RESOLV_CONF.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[0] == "nameserver":  # noqa: PLR2004
            return fields[1]
    message = f"no nameserver entries in {_RESOLV_CONF}"
    raise CutoverError(code, message)


def _zone_nameserver(resolver: str, hostname: str) -> str:
    labels = hostname.rstrip(".").split(".")
    for start in range(len(labels) - 1):
        zone = ".".join(labels[start:])
        answers = _query(resolver, zone, _TYPE_NS)
        nameservers = [
            answer.value for answer in answers if answer.record_type == "NS"
        ]
        if nameservers:
            try:
                records = socket.getaddrinfo(
                    nameservers[0], _DNS_PORT, type=socket.SOCK_DGRAM
                )
            except OSError as error:
                message = (
                    f"cannot resolve nameserver {nameservers[0]} for "
                    f"{zone}: {error}"
                )
                code = "cutover_nameserver_unreachable"
                raise CutoverError(code, message) from error
            return str(records[0][4][0])
    code = "cutover_nameserver_missing"
    message = f"no authoritative nameserver found for {hostname}"
    raise CutoverError(code, message)


def _query(server: str, name: str, record_type: int) -> tuple[DnsAnswer, ...]:
    query = _encode_query(name, record_type)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
        connection.settimeout(_DNS_TIMEOUT_SECONDS)
        try:
            connection.sendto(query, (server, _DNS_PORT))
            payload, _ = connection.recvfrom(4096)
        except OSError as error:
            code = "cutover_dns_unreachable"
            message = f"DNS query to {server} for {name} failed: {error}"
            raise CutoverError(code, message) from error
    return parse_answers(payload, query[:2])


def _encode_query(name: str, record_type: int) -> bytes:
    header = struct.pack(
        ">HHHHHH", int.from_bytes(os.urandom(2)), 0x0100, 1, 0, 0, 0
    )
    question = b"".join(
        bytes((len(label),)) + label.encode("ascii")
        for label in name.rstrip(".").split(".")
    ) + b"\x00"
    return header + question + struct.pack(">HH", record_type, _CLASS_IN)


def parse_answers(
    payload: bytes, expected_id: bytes
) -> tuple[DnsAnswer, ...]:
    """Parse the answer section of one DNS response payload."""
    code = "cutover_dns_invalid"
    if len(payload) < 12 or payload[:2] != expected_id:  # noqa: PLR2004
        message = "DNS response header is invalid"
        raise CutoverError(code, message)
    _, _, question_count, answer_count, _, _ = struct.unpack(
        ">HHHHHH", payload[:12]
    )
    offset = 12
    for _ in range(question_count):
        _, offset = _read_name(payload, offset)
        offset += 4
    answers: list[DnsAnswer] = []
    for _ in range(answer_count):
        name, offset = _read_name(payload, offset)
        record_type, _, ttl, data_length = struct.unpack(
            ">HHIH", payload[offset : offset + 10]
        )
        offset += 10
        rdata = payload[offset : offset + data_length]
        value = _decode_rdata(payload, offset, record_type, rdata)
        offset += data_length
        if value is not None:
            answers.append(
                DnsAnswer(
                    name=name,
                    record_type=_TYPE_NAMES.get(
                        record_type, str(record_type)
                    ),
                    ttl=ttl,
                    value=value,
                )
            )
    return tuple(answers)


def _decode_rdata(
    payload: bytes, offset: int, record_type: int, rdata: bytes
) -> str | None:
    if record_type == _TYPE_A and len(rdata) == 4:  # noqa: PLR2004
        return ".".join(str(byte) for byte in rdata)
    if record_type == _TYPE_AAAA and len(rdata) == 16:  # noqa: PLR2004
        return socket.inet_ntop(socket.AF_INET6, rdata)
    if record_type in (_TYPE_NS, _TYPE_CNAME):
        name, _ = _read_name(payload, offset)
        return name
    return None


def _read_name(payload: bytes, offset: int) -> tuple[str, int]:
    labels: list[str] = []
    jumps = 0
    cursor = offset
    end = offset
    jumped = False
    while True:
        if cursor >= len(payload) or jumps > 32:  # noqa: PLR2004
            code = "cutover_dns_invalid"
            message = "DNS response name is malformed"
            raise CutoverError(code, message)
        length = payload[cursor]
        if length & _POINTER_MASK == _POINTER_MASK:
            pointer = struct.unpack(">H", payload[cursor : cursor + 2])[0]
            if not jumped:
                end = cursor + 2
                jumped = True
            cursor = pointer & ~(_POINTER_MASK << 8)
            jumps += 1
            continue
        if length == 0:
            if not jumped:
                end = cursor + 1
            break
        cursor += 1
        labels.append(payload[cursor : cursor + length].decode("ascii"))
        cursor += length
    return ".".join(labels), end
