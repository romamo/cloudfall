"""Desired-versus-observed server audit tests."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from cloudfall.audit import AuditReport, AuditStatus, audit_inventory
from cloudfall.cli import main
from cloudfall.inventory import PlatformInventory
from cloudfall.observation import load_observations
from cloudfall.validation import StateValidationError, validate_state

ROOT = Path(__file__).parents[2]
SCHEMAS = ROOT / "state" / "schemas" / "v1"
EXAMPLES = ROOT / "state" / "examples"
COMPLIANT = ROOT / "state" / "tests" / "observed" / "compliant"


def _audit(observation_directory: Path) -> AuditReport:
    inventory = PlatformInventory.from_state(validate_state(EXAMPLES, SCHEMAS))
    observations = load_observations(observation_directory, SCHEMAS)
    return audit_inventory(inventory, observations)


def test_compliant_observations_match_desired_state() -> None:
    report = _audit(COMPLIANT)

    assert report.status is AuditStatus.COMPLIANT
    assert all(server.status is AuditStatus.COMPLIANT for server in report.servers)
    assert report.unmatched_observations == ()


def test_audit_reports_raid_package_service_and_config_drift(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observations = tmp_path / "observed"
    shutil.copytree(COMPLIANT, observations)
    h1 = observations / "h1.json"
    drifted = (
        h1.read_text(encoding="utf-8")
        .replace("[UU]", "[U_]")
        .replace('"name": "curl"', '"name": "curl-missing"')
        .replace('"state": "running"', '"state": "stopped"')
        .replace('"mode": "0644"', '"mode": "0600"')
    )
    h1.write_text(drifted, encoding="utf-8")

    report = _audit(observations)
    h1_report = next(server for server in report.servers if server.server_id == "h1")
    drift_checks = {
        check.check
        for check in h1_report.checks
        if check.status is AuditStatus.DRIFT
    }

    assert report.status is AuditStatus.DRIFT
    assert {
        "storage.softwareRaid.activeDevices",
        "packages.required[curl]",
        "services.required[ssh.service]",
        "configuration.files[/etc/ssh/sshd_config]",
    } <= drift_checks

    exit_code = main(
        [
            "audit",
            str(EXAMPLES),
            "--schemas",
            str(SCHEMAS),
            "--observed",
            str(observations),
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 1
    assert json.loads(captured.out)["status"] == "drift"
    assert captured.err == ""


def test_missing_observation_is_unknown(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observations = tmp_path / "observed"
    observations.mkdir()
    shutil.copy2(COMPLIANT / "h1.json", observations / "h1.json")

    report = _audit(observations)

    assert report.status is AuditStatus.UNKNOWN
    h2_report = next(server for server in report.servers if server.server_id == "h2")
    assert h2_report.status is AuditStatus.UNKNOWN

    exit_code = main(
        [
            "audit",
            str(EXAMPLES),
            "--schemas",
            str(SCHEMAS),
            "--observed",
            str(observations),
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 3
    assert json.loads(captured.out)["status"] == "unknown"
    assert captured.err == ""


def test_invalid_observation_fails_before_audit(tmp_path: Path) -> None:
    observations = tmp_path / "observed"
    observations.mkdir()
    invalid = (COMPLIANT / "h1.json").read_text(encoding="utf-8").replace(
        '"processorCount": 1', '"processorCount": -1'
    )
    (observations / "h1.json").write_text(invalid, encoding="utf-8")

    with pytest.raises(StateValidationError) as error:
        load_observations(observations, SCHEMAS)

    assert error.value.issue.code == "observation_schema_validation_failed"


def test_audit_cli_emits_json_and_compliance_exit_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(
        [
            "audit",
            str(EXAMPLES),
            "--schemas",
            str(SCHEMAS),
            "--observed",
            str(COMPLIANT),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert json.loads(captured.out)["status"] == "compliant"
    assert captured.err == ""
