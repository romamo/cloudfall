"""Cutover machinery tests: TTL evidence, parallel-run, rollback data."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

import pytest
from cloudfall.cutover import (
    CutoverError,
    DnsAnswer,
    check_ttl,
    parallel_run,
    parse_answers,
    rollback_instructions,
)
from cloudfall.domain import HttpStatusCode
from cloudfall.inventory import PlatformInventory
from cloudfall.service_evidence import (
    EndpointEvidence,
    NetworkResponse,
    ProbeTarget,
    TlsEvidence,
)
from cloudfall.validation import validate_config

ROOT = Path(__file__).parents[2]
SCHEMAS = ROOT / "config" / "schemas" / "v1"
EXAMPLES = ROOT / "config" / "examples"


def _inventory() -> PlatformInventory:
    return PlatformInventory.from_state(validate_config(EXAMPLES, SCHEMAS))


@dataclass(frozen=True, slots=True)
class FakeDnsProbe:
    """Fixed authoritative answers for every hostname."""

    ttl: int

    def authoritative_answers(self, hostname: str) -> tuple[DnsAnswer, ...]:
        """Return one A answer with the configured TTL."""
        return (
            DnsAnswer(
                name=hostname,
                record_type="A",
                ttl=self.ttl,
                value="203.0.113.10",
            ),
        )


@dataclass(frozen=True, slots=True)
class FakeNetworkClient:
    """Scripted origin responses keyed by nothing: one answer for all."""

    status: int | None
    error: str | None = None

    def resolve(self, hostname: object, port: object) -> tuple[()]:
        """Never used by parallel-run."""
        raise NotImplementedError

    def request(self, target: ProbeTarget) -> NetworkResponse:
        """Answer every probe with the scripted endpoint evidence."""
        assert target.scheme.value == "http"
        assert target.port.value == 80
        endpoint = EndpointEvidence(
            reachable=self.status is not None,
            status=(
                HttpStatusCode(self.status)
                if self.status is not None
                else None
            ),
            error=self.error,
        )
        tls = TlsEvidence(
            available=False, valid=False, expires_at=None, error=None
        )
        return NetworkResponse(endpoint=endpoint, tls=tls, headers={})


def _dns_payload(query_id: bytes, ttl: int) -> bytes:
    header = query_id + struct.pack(">HHHHH", 0x8180, 1, 1, 0, 0)
    question = b"\x03crm\x07example\x03com\x00" + struct.pack(">HH", 1, 1)
    answer = (
        b"\xc0\x0c"
        + struct.pack(">HHIH", 1, 1, ttl, 4)
        + bytes((203, 0, 113, 10))
    )
    return header + question + answer


def test_parse_answers_reads_compressed_names_and_ttls() -> None:
    payload = _dns_payload(b"\xab\xcd", 3600)

    answers = parse_answers(payload, b"\xab\xcd")

    assert answers == (
        DnsAnswer(
            name="crm.example.com",
            record_type="A",
            ttl=3600,
            value="203.0.113.10",
        ),
    )


def test_parse_answers_rejects_a_mismatched_id() -> None:
    payload = _dns_payload(b"\xab\xcd", 60)

    with pytest.raises(CutoverError) as caught:
        parse_answers(payload, b"\x00\x00")
    assert caught.value.code == "cutover_dns_invalid"


def test_check_ttl_passes_low_ttls_with_evidence() -> None:
    detail, offenders = check_ttl(_inventory(), FakeDnsProbe(ttl=60), 300)

    assert offenders == ()
    domains = detail["domains"]
    assert isinstance(domains, list)
    assert domains[0]["domain"] == "crm.example.test"
    assert domains[0]["maxTtl"] == 60


def test_check_ttl_reports_high_ttls_as_offenders() -> None:
    detail, offenders = check_ttl(_inventory(), FakeDnsProbe(ttl=86400), 300)

    assert len(offenders) == 1
    assert "86400s exceeds 300s" in offenders[0]
    assert detail["maxTtlSeconds"] == 300


def test_parallel_run_passes_when_the_origin_answers_as_declared() -> None:
    detail, failures = parallel_run(_inventory(), FakeNetworkClient(200))

    assert failures == ()
    probes = detail["probes"]
    assert isinstance(probes, list)
    assert probes[0]["domain"] == "crm.example.test"
    assert probes[0]["status"] == 200


def test_parallel_run_reports_wrong_statuses_and_unreachable_origins() -> None:
    _, wrong = parallel_run(_inventory(), FakeNetworkClient(503))
    _, down = parallel_run(
        _inventory(), FakeNetworkClient(None, error="connection refused")
    )

    assert len(wrong) == 1
    assert "answered 503" in wrong[0]
    assert len(down) == 1
    assert "unreachable" in down[0]
    assert "connection refused" in down[0]


def test_rollback_instructions_record_the_window_and_previous_answers() -> None:
    previous = {"domains": [{"domain": "crm.example.test", "maxTtl": 60}]}

    recipe = rollback_instructions(
        previous, 24, "2026-09-11T10:00:00Z", "2026-09-12T10:00:00Z"
    )

    assert recipe["windowHours"] == 24
    assert recipe["previous"] == previous
    assert "restore the previous DNS answers" in str(recipe["instruction"])
