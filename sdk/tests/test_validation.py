"""State validation acceptance tests."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from cloudfall.cli import main
from cloudfall.domain import ResourceId, ResourceKind
from cloudfall.validation import StateValidationError, validate_state

ROOT = Path(__file__).parents[2]
SCHEMAS = ROOT / "state" / "schemas" / "v1"


def test_example_state_validates_and_builds_typed_index() -> None:
    state = validate_state(ROOT / "state" / "examples", SCHEMAS)

    assert state.resource_count == 7
    assert state.counts_by_kind() == {
        "Component": 1,
        "HostProfile": 1,
        "LoggingStack": 1,
        "Project": 1,
        "Server": 2,
        "SshPublicKey": 1,
    }
    component = state.get(ResourceKind.COMPONENT, ResourceId("crm-backend"))
    assert component.key.resource_id == ResourceId("crm-backend")
    logging = state.get(ResourceKind.LOGGING_STACK, ResourceId("operations"))
    assert logging.key.resource_id == ResourceId("operations")


def test_unknown_component_field_fails_schema_validation() -> None:
    invalid_state = ROOT / "state" / "tests" / "invalid" / "schema"

    with pytest.raises(StateValidationError) as error:
        validate_state(invalid_state, SCHEMAS)

    assert error.value.issue.code == "schema_validation_failed"
    assert "unsupportedField" in error.value.issue.message


def test_missing_server_reference_fails_semantic_validation() -> None:
    invalid_state = ROOT / "state" / "tests" / "invalid" / "missing-server"

    with pytest.raises(StateValidationError) as error:
        validate_state(invalid_state, SCHEMAS)

    assert error.value.issue.code == "resource_reference_missing"
    assert "Server/h9" in error.value.issue.message


def test_component_install_root_must_stay_below_project_root(
    tmp_path: Path,
) -> None:
    state_directory = tmp_path / "state"
    shutil.copytree(ROOT / "state" / "examples", state_directory)
    component_path = state_directory / "components" / "crm-backend.yaml"
    component = component_path.read_text(encoding="utf-8").replace(
        "/srv/apps/crm/backend", "/opt/crm/backend"
    )
    component_path.write_text(component, encoding="utf-8")

    with pytest.raises(StateValidationError) as error:
        validate_state(state_directory, SCHEMAS)

    assert error.value.issue.code == "component_install_root_invalid"


def test_host_profile_rejects_duplicate_configuration_paths(
    tmp_path: Path,
) -> None:
    state_directory = tmp_path / "state"
    shutil.copytree(ROOT / "state" / "examples", state_directory)
    profile_path = state_directory / "host-profiles" / "debian-application.yaml"
    duplicated = profile_path.read_text(encoding="utf-8") + (
        "      - path: /etc/ssh/sshd_config\n        capture: metadata\n"
    )
    profile_path.write_text(duplicated, encoding="utf-8")

    with pytest.raises(StateValidationError) as error:
        validate_state(state_directory, SCHEMAS)

    assert error.value.issue.code == "host_profile_entry_duplicate"


def test_ssh_public_key_algorithm_must_match_wire_payload(
    tmp_path: Path,
) -> None:
    state_directory = tmp_path / "state"
    shutil.copytree(ROOT / "state" / "examples", state_directory)
    key_directory = state_directory / "ssh-public-keys"
    example_key = (
        key_directory / "example-admin.yaml"
    ).read_text(encoding="utf-8")
    mismatched = example_key.replace("ssh-rsa", "ssh-ed25519", 1).replace(
        "example-admin", "mismatched"
    )
    (key_directory / "mismatched.yaml").write_text(mismatched, encoding="utf-8")

    with pytest.raises(StateValidationError) as error:
        validate_state(state_directory, SCHEMAS)

    assert error.value.issue.code == "ssh_public_key_invalid"


def test_host_profile_rejects_duplicate_firewall_rules(
    tmp_path: Path,
) -> None:
    state_directory = tmp_path / "state"
    shutil.copytree(ROOT / "state" / "examples", state_directory)
    profile_path = state_directory / "host-profiles" / "debian-application.yaml"
    duplicated = profile_path.read_text(encoding="utf-8").replace(
        "      - port: 443\n        protocol: tcp\n        description: https",
        "      - port: 80\n        protocol: tcp\n        description: duplicate",
    )
    profile_path.write_text(duplicated, encoding="utf-8")

    with pytest.raises(StateValidationError) as error:
        validate_state(state_directory, SCHEMAS)

    assert error.value.issue.code == "firewall_rule_duplicate"


def test_logging_stack_rejects_conflicting_listener_ports(
    tmp_path: Path,
) -> None:
    state_directory = tmp_path / "state"
    shutil.copytree(ROOT / "state" / "examples", state_directory)
    logging_path = state_directory / "logging-stacks" / "operations.yaml"
    conflicting = logging_path.read_text(encoding="utf-8").replace(
        "port: 9090", "port: 3100", 1
    )
    logging_path.write_text(conflicting, encoding="utf-8")

    with pytest.raises(StateValidationError) as error:
        validate_state(state_directory, SCHEMAS)

    assert error.value.issue.code == "logging_port_conflict"


def test_logging_collector_must_reference_an_existing_server(
    tmp_path: Path,
) -> None:
    state_directory = tmp_path / "state"
    shutil.copytree(ROOT / "state" / "examples", state_directory)
    logging_path = state_directory / "logging-stacks" / "operations.yaml"
    invalid = logging_path.read_text(encoding="utf-8").replace(
        "      - h2", "      - h9", 1
    )
    logging_path.write_text(invalid, encoding="utf-8")

    with pytest.raises(StateValidationError) as error:
        validate_state(state_directory, SCHEMAS)

    assert error.value.issue.code == "resource_reference_missing"
    assert "Server/h9" in error.value.issue.message


def test_logging_migration_cannot_disable_legacy_agents(
    tmp_path: Path,
) -> None:
    state_directory = tmp_path / "state"
    shutil.copytree(ROOT / "state" / "examples", state_directory)
    logging_path = state_directory / "logging-stacks" / "operations.yaml"
    invalid = logging_path.read_text(encoding="utf-8").replace(
        "preserveLegacyAgents: true", "preserveLegacyAgents: false"
    )
    logging_path.write_text(invalid, encoding="utf-8")

    with pytest.raises(StateValidationError) as error:
        validate_state(state_directory, SCHEMAS)

    assert error.value.issue.code == "schema_validation_failed"


def test_logging_secret_paths_stay_in_managed_directory(
    tmp_path: Path,
) -> None:
    state_directory = tmp_path / "state"
    shutil.copytree(ROOT / "state" / "examples", state_directory)
    logging_path = state_directory / "logging-stacks" / "operations.yaml"
    invalid = logging_path.read_text(encoding="utf-8").replace(
        "/etc/cloudfall/logging/server.key", "/var/lib/cloudfall-unsafe/server.key"
    )
    logging_path.write_text(invalid, encoding="utf-8")

    with pytest.raises(StateValidationError) as error:
        validate_state(state_directory, SCHEMAS)

    assert error.value.issue.code == "logging_secret_path_invalid"


def test_cli_emits_structured_success(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(
        [
            "state",
            "validate",
            str(ROOT / "state" / "examples"),
            "--schemas",
            str(SCHEMAS),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert json.loads(captured.out) == {
        "status": "ok",
        "resources": 7,
        "byKind": {
            "Component": 1,
            "HostProfile": 1,
            "LoggingStack": 1,
            "Project": 1,
            "Server": 2,
            "SshPublicKey": 1,
        },
    }
    assert captured.err == ""


def test_cli_emits_structured_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    invalid_state = ROOT / "state" / "tests" / "invalid" / "missing-server"
    exit_code = main(
        [
            "state",
            "validate",
            str(invalid_state),
            "--schemas",
            str(SCHEMAS),
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.err)
    assert exit_code == 2
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "resource_reference_missing"
    assert captured.out == ""
