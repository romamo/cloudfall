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


def test_observed_systemd_escaped_unit_names_are_accepted(tmp_path: Path) -> None:
    r"""Observed units may carry systemd path escapes such as ``\x2d``.

    The 2026-09-08 Hetzner proving run failed observation validation because
    a real host reported ``systemd-fsck@dev-disk-by\x2duuid-....service``,
    which the declared-state unit-name pattern rejects.
    """
    observations = tmp_path / "observed"
    shutil.copytree(COMPLIANT, observations)
    h1 = observations / "h1.json"
    snapshot = json.loads(h1.read_text(encoding="utf-8"))
    escaped = "systemd-fsck@dev-disk-by\\x2duuid-E079\\x2d7D41.service"
    snapshot["spec"]["services"][escaped] = {
        "name": escaped,
        "state": "stopped",
        "status": "static",
        "source": "systemd",
    }
    h1.write_text(json.dumps(snapshot), encoding="utf-8")

    report = _audit(observations)

    assert report.status is AuditStatus.COMPLIANT


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


def test_audit_reports_firewall_and_timer_drift(tmp_path: Path) -> None:
    observations = tmp_path / "observed"
    shutil.copytree(COMPLIANT, observations)
    h1 = observations / "h1.json"
    content = json.loads(h1.read_text(encoding="utf-8"))
    spec = content["spec"]
    table = json.loads(spec["firewall"]["managedTableJson"])
    table["nftables"] = [
        entry
        for entry in table["nftables"]
        if entry.get("rule", {}).get("handle") != 9
    ]
    for entry in table["nftables"]:
        chain = entry.get("chain")
        if chain is not None and chain["name"] == "input":
            chain["policy"] = "accept"
    spec["firewall"]["managedTableJson"] = json.dumps(table)
    spec["timers"]["apt-daily.timer"]["status"] = "disabled"
    h1.write_text(json.dumps(content), encoding="utf-8")

    report = _audit(observations)
    h1_report = next(server for server in report.servers if server.server_id == "h1")
    drift_checks = {
        check.check
        for check in h1_report.checks
        if check.status is AuditStatus.DRIFT
    }

    assert report.status is AuditStatus.DRIFT
    assert {
        "firewall.inputPolicy",
        "firewall.allowedInbound",
        "services.required[apt-daily.timer]",
    } <= drift_checks
    allowed = next(
        check
        for check in h1_report.checks
        if check.check == "firewall.allowedInbound"
    )
    assert allowed.desired == ["tcp/22", "tcp/80", "tcp/443"]
    assert allowed.observed == ["tcp/22", "tcp/80"]


def test_audit_reports_missing_firewall_table_as_drift(tmp_path: Path) -> None:
    observations = tmp_path / "observed"
    shutil.copytree(COMPLIANT, observations)
    h1 = observations / "h1.json"
    content = json.loads(h1.read_text(encoding="utf-8"))
    content["spec"]["firewall"] = {
        "nftAvailable": False,
        "managedTableJson": None,
    }
    h1.write_text(json.dumps(content), encoding="utf-8")

    report = _audit(observations)
    h1_report = next(server for server in report.servers if server.server_id == "h1")
    drift_checks = {
        check.check
        for check in h1_report.checks
        if check.status is AuditStatus.DRIFT
    }

    assert {
        "firewall.managedTable",
        "firewall.inputPolicy",
        "firewall.allowedInbound",
    } <= drift_checks


def test_audit_reports_missing_alert_evidence_as_drift(tmp_path: Path) -> None:
    observations = tmp_path / "observed"
    shutil.copytree(COMPLIANT, observations)
    h1 = observations / "h1.json"
    content = json.loads(h1.read_text(encoding="utf-8"))
    del content["spec"]["alerting"]
    h1.write_text(json.dumps(content), encoding="utf-8")

    report = _audit(observations)
    h1_report = next(server for server in report.servers if server.server_id == "h1")
    check = next(
        check
        for check in h1_report.checks
        if check.check == "alerting.rules[postgresql-down]"
    )

    assert check.status is AuditStatus.DRIFT
    assert check.observed is None


def test_audit_reports_unloaded_alert_rule_as_drift(tmp_path: Path) -> None:
    observations = tmp_path / "observed"
    shutil.copytree(COMPLIANT, observations)
    h1 = observations / "h1.json"
    content = json.loads(h1.read_text(encoding="utf-8"))
    content["spec"]["alerting"]["rulesJson"] = json.dumps(
        {"status": "success", "data": {"groups": []}}
    )
    h1.write_text(json.dumps(content), encoding="utf-8")

    report = _audit(observations)
    h1_report = next(server for server in report.servers if server.server_id == "h1")
    check = next(
        check
        for check in h1_report.checks
        if check.check == "alerting.rules[postgresql-down]"
    )

    assert check.status is AuditStatus.DRIFT


def test_audit_reports_unhealthy_alert_rule_as_drift(tmp_path: Path) -> None:
    observations = tmp_path / "observed"
    shutil.copytree(COMPLIANT, observations)
    h1 = observations / "h1.json"
    content = json.loads(h1.read_text(encoding="utf-8"))
    content["spec"]["alerting"]["rulesJson"] = json.dumps(
        {
            "status": "success",
            "data": {
                "groups": [
                    {
                        "name": "cloudfall",
                        "rules": [
                            {
                                "name": "postgresql_down",
                                "type": "alerting",
                                "state": "inactive",
                                "health": "err",
                            }
                        ],
                    }
                ]
            },
        }
    )
    h1.write_text(json.dumps(content), encoding="utf-8")

    report = _audit(observations)
    h1_report = next(server for server in report.servers if server.server_id == "h1")
    check = next(
        check
        for check in h1_report.checks
        if check.check == "alerting.rules[postgresql-down]"
    )

    assert check.status is AuditStatus.DRIFT
    assert isinstance(check.observed, dict)
    assert check.observed["health"] == "err"


def test_audit_accepts_firing_declared_alert_as_compliant(
    tmp_path: Path,
) -> None:
    observations = tmp_path / "observed"
    shutil.copytree(COMPLIANT, observations)
    h1 = observations / "h1.json"
    content = json.loads(h1.read_text(encoding="utf-8"))
    content["spec"]["alerting"]["rulesJson"] = json.dumps(
        {
            "status": "success",
            "data": {
                "groups": [
                    {
                        "name": "cloudfall",
                        "rules": [
                            {
                                "name": "postgresql_down",
                                "type": "alerting",
                                "state": "firing",
                                "health": "ok",
                            }
                        ],
                    }
                ]
            },
        }
    )
    h1.write_text(json.dumps(content), encoding="utf-8")

    report = _audit(observations)
    h1_report = next(server for server in report.servers if server.server_id == "h1")
    check = next(
        check
        for check in h1_report.checks
        if check.check == "alerting.rules[postgresql-down]"
    )

    assert check.status is AuditStatus.COMPLIANT
    assert isinstance(check.observed, dict)
    assert check.observed["state"] == "firing"


def test_audit_reports_wildcard_service_exposure_as_drift(
    tmp_path: Path,
) -> None:
    observations = tmp_path / "observed"
    shutil.copytree(COMPLIANT, observations)
    h1 = observations / "h1.json"
    content = json.loads(h1.read_text(encoding="utf-8"))
    network = content["spec"]["network"]
    network["listeningSockets"] = network["listeningSockets"].replace(
        "127.0.0.1:5432", "0.0.0.0:5432"
    )
    h1.write_text(json.dumps(content), encoding="utf-8")

    report = _audit(observations)
    h1_report = next(server for server in report.servers if server.server_id == "h1")
    bind_check = next(
        check
        for check in h1_report.checks
        if check.check == "services.bind[postgresql-main]"
    )

    assert report.status is AuditStatus.DRIFT
    assert bind_check.status is AuditStatus.DRIFT
    assert bind_check.observed == {"listeners": ["0.0.0.0:5432"]}


def test_audit_reports_missing_service_listener_as_drift(
    tmp_path: Path,
) -> None:
    observations = tmp_path / "observed"
    shutil.copytree(COMPLIANT, observations)
    h1 = observations / "h1.json"
    content = json.loads(h1.read_text(encoding="utf-8"))
    network = content["spec"]["network"]
    network["listeningSockets"] = "\n".join(
        line
        for line in network["listeningSockets"].splitlines()
        if ":5432" not in line
    )
    h1.write_text(json.dumps(content), encoding="utf-8")

    report = _audit(observations)
    h1_report = next(server for server in report.servers if server.server_id == "h1")
    bind_check = next(
        check
        for check in h1_report.checks
        if check.check == "services.bind[postgresql-main]"
    )

    assert bind_check.status is AuditStatus.DRIFT
    assert bind_check.observed is None


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


def _environment_evidence(sha256: str | None, *, exists: bool = True) -> dict:
    return {
        "component": "crm-backend",
        "path": "/srv/apps/crm/backend/shared/env/crm-backend.env",
        "exists": exists,
        "sha256": sha256,
    }


def _audit_with_receipts(
    observation_directory: Path, receipts: dict[str, str]
) -> AuditReport:
    inventory = PlatformInventory.from_state(
        validate_state(EXAMPLES, SCHEMAS)
    )
    observations = load_observations(observation_directory, SCHEMAS)
    return audit_inventory(inventory, observations, receipts)


def test_environment_file_matching_hash_is_compliant(tmp_path: Path) -> None:
    expected = "a" * 64
    observations = tmp_path / "observed"
    shutil.copytree(COMPLIANT, observations)
    h1 = observations / "h1.json"
    content = json.loads(h1.read_text(encoding="utf-8"))
    content["spec"]["componentEnvironment"] = [_environment_evidence(expected)]
    h1.write_text(json.dumps(content), encoding="utf-8")

    report = _audit_with_receipts(observations, {"crm-backend": expected})
    h1_report = next(
        server for server in report.servers if server.server_id == "h1"
    )
    check = next(
        check
        for check in h1_report.checks
        if check.check == "environment.file[crm-backend]"
    )

    assert check.status is AuditStatus.COMPLIANT


def test_environment_file_hash_mismatch_is_drift(tmp_path: Path) -> None:
    observations = tmp_path / "observed"
    shutil.copytree(COMPLIANT, observations)
    h1 = observations / "h1.json"
    content = json.loads(h1.read_text(encoding="utf-8"))
    content["spec"]["componentEnvironment"] = [
        _environment_evidence("b" * 64)
    ]
    h1.write_text(json.dumps(content), encoding="utf-8")

    report = _audit_with_receipts(observations, {"crm-backend": "a" * 64})
    h1_report = next(
        server for server in report.servers if server.server_id == "h1"
    )
    check = next(
        check
        for check in h1_report.checks
        if check.check == "environment.file[crm-backend]"
    )

    assert check.status is AuditStatus.DRIFT


def test_environment_file_missing_evidence_is_drift(tmp_path: Path) -> None:
    observations = tmp_path / "observed"
    shutil.copytree(COMPLIANT, observations)

    report = _audit_with_receipts(observations, {"crm-backend": "a" * 64})
    h1_report = next(
        server for server in report.servers if server.server_id == "h1"
    )
    check = next(
        check
        for check in h1_report.checks
        if check.check == "environment.file[crm-backend]"
    )

    assert check.status is AuditStatus.DRIFT
    assert check.observed is None


def test_no_environment_receipts_means_no_environment_checks() -> None:
    report = _audit(COMPLIANT)

    for server in report.servers:
        assert not any(
            check.check.startswith("environment.file")
            for check in server.checks
        )
