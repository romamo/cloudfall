"""Operator propose-and-approve loop tests."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from cloudfall.cli import main
from cloudfall.domain import ResourceId
from cloudfall.inventory import PlatformInventory
from cloudfall.operator import (
    ApproveOptions,
    OperatorAlert,
    OperatorError,
    ProposalStatus,
    ProposalStore,
    RunReport,
    SkippedAlert,
    alert_fingerprint,
    approve,
    parse_prometheus_alerts,
    run_once,
)
from cloudfall.validation import SchemaCatalog, validate_state

_FAST_APPROVE = ApproveOptions(
    verify_timeout_seconds=2.0,
    poll_interval_seconds=1.0,
    sleep=lambda _: None,
)

ROOT = Path(__file__).parents[2]
SCHEMAS = ROOT / "state" / "schemas" / "v1"
EXAMPLES = ROOT / "state" / "examples"

_LABELS = {
    "alertname": "postgresql_down",
    "cloudfall_rule": "postgresql-down",
    "severity": "critical",
    "environment": "production",
    "server": "h1",
    "service": "postgresql-main",
    "instance": "postgresql:///var/run/postgresql:5432/postgres",
    "job": "integrations/postgres",
}


def _alerts_payload(state: str = "firing") -> str:
    return json.dumps(
        {
            "status": "success",
            "data": {
                "alerts": [
                    {
                        "labels": _LABELS,
                        "annotations": {"summary": "postgres is down"},
                        "state": state,
                        "activeAt": "2026-09-10T14:45:45Z",
                        "value": "0e+00",
                    }
                ]
            },
        }
    )


@dataclass
class FakeFeed:
    """Scripted alert feed: one payload per fetch, last repeats."""

    payloads: list[str] = field(default_factory=list)
    fetches: int = 0

    def fetch(self) -> tuple[OperatorAlert, ...]:
        """Return the alerts parsed from the next scripted payload."""
        index = min(self.fetches, len(self.payloads) - 1)
        self.fetches += 1
        return parse_prometheus_alerts(self.payloads[index])


def _inventory() -> PlatformInventory:
    return PlatformInventory.from_state(validate_state(EXAMPLES, SCHEMAS))


def _store(tmp_path: Path) -> ProposalStore:
    return ProposalStore(
        directory=tmp_path / "proposals", catalog=SchemaCatalog(SCHEMAS)
    )


def test_parse_prometheus_alerts_returns_firing_actionable_alerts() -> None:
    alerts = parse_prometheus_alerts(_alerts_payload())

    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.name == "postgresql_down"
    assert alert.cloudfall_rule.value == "postgresql-down"
    assert alert.server.value == "h1"
    assert alert.service.value == "postgresql-main"
    assert alert.fingerprint == alert_fingerprint(_LABELS)


def test_parse_prometheus_alerts_ignores_pending_alerts() -> None:
    assert parse_prometheus_alerts(_alerts_payload(state="pending")) == ()


def test_parse_prometheus_alerts_rejects_invalid_payload() -> None:
    with pytest.raises(OperatorError) as caught:
        parse_prometheus_alerts("{}")
    assert caught.value.code == "operator_feed_invalid"


def test_run_once_writes_a_schema_valid_proposal(tmp_path: Path) -> None:
    store = _store(tmp_path)
    feed = FakeFeed(payloads=[_alerts_payload()])

    report = run_once(feed, _inventory(), store)

    assert isinstance(report, RunReport)
    assert len(report.proposed) == 1
    assert report.skipped == ()
    assert report.open_proposals == 1
    proposal = store.load(ResourceId.from_boundary(report.proposed[0]))
    assert proposal.status is ProposalStatus.PROPOSED
    assert proposal.alert.service.value == "postgresql-main"
    assert proposal.operation_kind.value == "converge-services"
    assert "converging declared services" in proposal.diagnosis_summary


def test_run_once_does_not_duplicate_open_proposals(tmp_path: Path) -> None:
    store = _store(tmp_path)
    feed = FakeFeed(payloads=[_alerts_payload()])

    first = run_once(feed, _inventory(), store)
    second = run_once(feed, _inventory(), store)

    assert len(first.proposed) == 1
    assert second.proposed == ()
    assert len(store.list()) == 1


def test_run_once_skips_undeclared_services(tmp_path: Path) -> None:
    labels = dict(_LABELS, service="mystery-db")
    payload = json.dumps(
        {
            "status": "success",
            "data": {
                "alerts": [
                    {
                        "labels": labels,
                        "state": "firing",
                        "activeAt": "2026-09-10T14:45:45Z",
                    }
                ]
            },
        }
    )
    store = _store(tmp_path)

    report = run_once(FakeFeed(payloads=[payload]), _inventory(), store)

    assert report.proposed == ()
    assert len(report.skipped) == 1
    assert isinstance(report.skipped[0], SkippedAlert)
    assert "not declared" in report.skipped[0].reason
    assert store.list() == ()


def test_approve_executes_and_verifies_resolution(tmp_path: Path) -> None:
    store = _store(tmp_path)
    resolved = json.dumps({"status": "success", "data": {"alerts": []}})
    feed = FakeFeed(payloads=[_alerts_payload(), resolved])
    report = run_once(feed, _inventory(), store)
    executed: list[str] = []

    result = approve(
        store,
        ResourceId.from_boundary(report.proposed[0]),
        lambda proposal: executed.append(proposal.resource_id.value),
        feed,
        _FAST_APPROVE,
    )

    assert executed == [report.proposed[0]]
    assert result.status is ProposalStatus.VERIFIED
    assert result.outcome is not None
    assert result.outcome.result == "verified"
    reloaded = store.load(result.resource_id)
    assert reloaded.status is ProposalStatus.VERIFIED


def test_approve_marks_unresolved_alerts_failed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    feed = FakeFeed(payloads=[_alerts_payload()])
    report = run_once(feed, _inventory(), store)

    result = approve(
        store,
        ResourceId.from_boundary(report.proposed[0]),
        lambda _proposal: None,
        feed,
        _FAST_APPROVE,
    )

    assert result.status is ProposalStatus.FAILED
    assert result.outcome is not None
    assert "still firing" in result.outcome.detail


def test_approve_records_execution_failure(tmp_path: Path) -> None:
    store = _store(tmp_path)
    feed = FakeFeed(payloads=[_alerts_payload()])
    report = run_once(feed, _inventory(), store)
    proposal_id = ResourceId.from_boundary(report.proposed[0])

    def _boom(_proposal: object) -> None:
        code = "operator_test_boom"
        message = "engine unavailable"
        raise OperatorError(code, message)

    with pytest.raises(OperatorError):
        approve(
            store,
            proposal_id,
            _boom,
            feed,
            _FAST_APPROVE,
        )

    failed = store.load(proposal_id)
    assert failed.status is ProposalStatus.FAILED
    assert failed.outcome is not None
    assert "execution failed" in failed.outcome.detail


def test_approve_refuses_non_open_proposals(tmp_path: Path) -> None:
    store = _store(tmp_path)
    resolved = json.dumps({"status": "success", "data": {"alerts": []}})
    feed = FakeFeed(payloads=[_alerts_payload(), resolved])
    report = run_once(feed, _inventory(), store)
    proposal_id = ResourceId.from_boundary(report.proposed[0])
    approve(
        store,
        proposal_id,
        lambda _proposal: None,
        feed,
        _FAST_APPROVE,
    )

    with pytest.raises(OperatorError) as caught:
        approve(
            store,
            proposal_id,
            lambda _proposal: None,
            feed,
            _FAST_APPROVE,
        )
    assert caught.value.code == "operator_proposal_not_open"


def test_cli_operator_list_emits_structured_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(
        [
            "operator",
            "list",
            str(EXAMPLES),
            "--schemas",
            str(SCHEMAS),
            "--proposals",
            str(tmp_path / "proposals"),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert json.loads(captured.out) == {"status": "ok", "proposals": []}
