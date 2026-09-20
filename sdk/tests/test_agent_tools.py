"""Agent toolset and MCP server tests."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import yaml
from cloudfall.agent_tools import AgentConfig, AgentToolset
from cloudfall.project import (
    InitOptions,
    ProjectName,
    ReleaseVersion,
    init_project,
)

ROOT = Path(__file__).parents[2]
SCHEMAS = ROOT / "config" / "schemas" / "v1"
EXAMPLES = ROOT / "config" / "examples"
ENGINE = ROOT / "engine"
COMPLIANT = ROOT / "config" / "tests" / "observed" / "compliant"

BLUEPRINT = """\
services:
  - type: worker
    name: acme-worker
    runtime: python
    repo: https://github.com/example/acme.git
    startCommand: celery -A acme worker
"""


VERSION = "0.3.0"


def _config(
    tmp_path: Path,
    observed: Path | None = None,
    project_directory: Path = EXAMPLES,
) -> AgentConfig:
    return AgentConfig(
        project_directory=project_directory,
        schema_directory=SCHEMAS,
        engine_directory=ENGINE,
        inventory_file=tmp_path / "inventory.json",
        observed_directory=observed if observed is not None else COMPLIANT,
        service_observed_directory=tmp_path / "observed-services",
        deployments_directory=tmp_path / "deployments",
        releases_directory=tmp_path / "releases",
        artifacts_directory=tmp_path / "artifacts",
        proposals_directory=tmp_path / "proposals",
    )


def test_read_only_tools_return_structured_evidence(tmp_path: Path) -> None:
    toolset = AgentToolset(_config(tmp_path))

    validated = toolset.validate()
    inventory = toolset.inventory()
    audit = toolset.audit()

    assert validated["status"] == "ok"
    assert validated["resources"] == 12
    assert inventory["status"] == "ok"
    assert audit["status"] == "compliant"


def test_audit_reports_missing_evidence_as_a_structured_error(
    tmp_path: Path,
) -> None:
    toolset = AgentToolset(_config(tmp_path, observed=tmp_path / "missing"))

    result = toolset.audit()

    assert result["status"] == "error"
    error = result["error"]
    assert isinstance(error, dict)
    assert error["code"] == "observation_directory_missing"


def test_mutating_tools_require_an_explicit_confirmation(
    tmp_path: Path,
) -> None:
    toolset = AgentToolset(_config(tmp_path))

    deploy_preview = toolset.deploy_component(
        "crm-backend", "20260101T000000Z-abcdef0"
    )
    baseline_preview = toolset.converge_baseline()

    assert deploy_preview["status"] == "confirmation-required"
    assert deploy_preview["action"] == "deploy"
    assert "crm-backend" in str(deploy_preview["wouldRun"])
    assert baseline_preview["status"] == "confirmation-required"
    assert "confirm=true" in str(baseline_preview["instruction"])


def test_confirmed_deploy_still_verifies_the_artifact(tmp_path: Path) -> None:
    toolset = AgentToolset(_config(tmp_path))

    result = toolset.deploy_component(
        "crm-backend", "20260101T000000Z-abcdef0", confirm=True
    )

    assert result["status"] == "error"
    error = result["error"]
    assert isinstance(error, dict)
    assert error["code"] == "lifecycle_artifact_missing"


def test_invalid_identifiers_return_structured_errors(tmp_path: Path) -> None:
    toolset = AgentToolset(_config(tmp_path))

    result = toolset.rollback_component(
        "crm-backend", "not-a-release", confirm=True
    )

    assert result["status"] == "error"
    error = result["error"]
    assert isinstance(error, dict)
    assert error["code"] == "invalid_argument"


def test_path_arguments_must_stay_inside_the_project(tmp_path: Path) -> None:
    blueprint = tmp_path / "render.yaml"
    blueprint.write_text(BLUEPRINT, encoding="utf-8")
    toolset = AgentToolset(_config(tmp_path))

    imported = toolset.import_render(
        str(blueprint), "acme", "h1", "../../config", "tmp/env"
    )
    planned = toolset.migrate(plan_file="tmp/../../plan.json")

    for result in (imported, planned):
        assert result["status"] == "error"
        error = result["error"]
        assert isinstance(error, dict)
        assert error["code"] == "invalid_argument"
        assert "leaves the project directory" in str(error["message"])


def _fresh_project(tmp_path: Path) -> AgentToolset:
    directory = tmp_path / "project"
    init_project(
        InitOptions(
            directory=directory,
            name=ProjectName("project"),
            version=ReleaseVersion(VERSION),
        )
    )
    return AgentToolset(_config(tmp_path, project_directory=directory))


def _example_key() -> str:
    document = yaml.safe_load(
        (EXAMPLES / "ssh-public-keys" / "example-admin.yaml").read_text(
            encoding="utf-8"
        )
    )
    return str(document["spec"]["publicKey"])


def test_fleet_tools_write_resources_and_revalidate_the_project(
    tmp_path: Path,
) -> None:
    toolset = _fresh_project(tmp_path)
    key_file = tmp_path / "id_ed25519.pub"
    key_file.write_text(f"{_example_key()}\n", encoding="utf-8")

    key = toolset.add_ssh_key(str(key_file), "roman")
    server = toolset.add_server("h1", "203.0.113.10", description="first host")
    validated = toolset.validate()

    assert key["status"] == "ok"
    assert key["added"] == [
        {"kind": "SshPublicKey", "id": "roman", "path": "ssh-public-keys/roman.yaml"}
    ]
    assert server["status"] == "ok"
    added = server["added"]
    assert isinstance(added, list)
    assert [resource["kind"] for resource in added] == ["ServerType", "Server"]
    assert validated["status"] == "ok"
    assert validated["resources"] == 3


def test_fleet_tools_return_structured_errors_and_never_overwrite(
    tmp_path: Path,
) -> None:
    toolset = _fresh_project(tmp_path)

    missing_key = toolset.add_ssh_key(str(tmp_path / "absent.pub"), "roman")
    bad_port = toolset.add_server("h1", "203.0.113.10", ssh_port=70000)
    first = toolset.add_server_type("debian-application")
    again = toolset.add_server_type("debian-application")

    assert first["status"] == "ok"
    for result, code in (
        (missing_key, "ssh_key_file_missing"),
        (bad_port, "invalid_argument"),
        (again, "resource_exists"),
    ):
        assert result["status"] == "error"
        error = result["error"]
        assert isinstance(error, dict)
        assert error["code"] == code


def test_import_render_tool_maps_a_blueprint(tmp_path: Path) -> None:
    blueprint = tmp_path / "render.yaml"
    blueprint.write_text(BLUEPRINT, encoding="utf-8")
    toolset = AgentToolset(_config(tmp_path))

    result = toolset.import_render(
        str(blueprint),
        "acme",
        "h1",
        str(tmp_path / "config"),
        str(tmp_path / "env"),
    )

    assert result["status"] == "ok"
    assert result["components"] == ["acme-worker"]


def test_migrate_tool_previews_the_plan_without_confirmation(
    tmp_path: Path,
) -> None:
    toolset = AgentToolset(_config(tmp_path))

    result = toolset.migrate(
        builds={"crm-backend": "main"},
        plan_file=str(tmp_path / "plan.json"),
    )

    assert result["status"] == "plan"
    assert result["next"] == "baseline"
    assert "confirm=true" in str(result["instruction"])
    assert not (tmp_path / "plan.json").exists()


def test_mcp_server_registers_annotated_tools(tmp_path: Path) -> None:
    pytest.importorskip("mcp")
    from cloudfall.mcp_server import (  # noqa: PLC0415 - optional extra.
        create_server,
    )

    server = create_server(_config(tmp_path))
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}

    assert {
        "validate_config",
        "audit_servers",
        "inspect_servers",
        "add_ssh_key",
        "add_server_type",
        "add_server",
        "import_render",
        "build_artifact",
        "deploy_component",
        "converge_baseline",
        "converge_domains",
        "migrate",
    } <= set(tools)
    assert tools["audit_servers"].annotations is not None
    assert tools["audit_servers"].annotations.read_only_hint is True
    assert tools["deploy_component"].annotations is not None
    assert tools["deploy_component"].annotations.destructive_hint is True
    assert "confirm" in str(tools["deploy_component"].input_schema)
    assert tools["add_server"].annotations is not None
    assert tools["add_server"].annotations.read_only_hint is False
    assert tools["add_server"].annotations.destructive_hint is False
    assert "ssh_port" in str(tools["add_server"].input_schema)
    assert "confirm" not in str(tools["add_server"].input_schema)


def test_operator_tools_list_gate_and_report_missing_material(
    tmp_path: Path,
) -> None:
    toolset = AgentToolset(_config(tmp_path))

    proposals = toolset.operator_proposals()
    gate = toolset.operator_approve("op-nonexistent")
    watch = toolset.operator_watch()

    assert proposals == {"status": "ok", "proposals": []}
    assert gate["status"] == "error"
    assert watch["status"] == "error"
    error = watch["error"]
    assert isinstance(error, dict)
    assert error["code"] == "operator_gateway_material_missing"


def test_operator_approve_gates_before_executing(tmp_path: Path) -> None:
    from cloudfall.domain import ResourceId  # noqa: PLC0415 - test-local.
    from cloudfall.operator import (  # noqa: PLC0415 - test-local.
        OperationKind,
        ProposalStore,
        propose_for_drift,
    )
    from cloudfall.validation import SchemaCatalog  # noqa: PLC0415

    config = _config(tmp_path)
    store = ProposalStore(
        directory=config.proposals_directory, catalog=SchemaCatalog(SCHEMAS)
    )
    proposal = propose_for_drift(
        ResourceId.from_boundary("h1"),
        ("packages.required[curl]",),
        OperationKind.CONVERGE_BASELINE,
        "2026-09-10T15:00:00+00:00",
    )
    store.save(proposal)
    toolset = AgentToolset(config)

    gate = toolset.operator_approve(proposal.resource_id.value)

    assert gate["status"] == "confirmation-required"
    assert "baseline.yml" in str(gate["wouldRun"])


def test_backup_tools_gate_and_reject_unknown_services(
    tmp_path: Path,
) -> None:
    toolset = AgentToolset(_config(tmp_path))

    gate = toolset.backup_service("postgresql-main")
    verify_gate = toolset.verify_backup("postgresql-main")
    unknown = toolset.backup_service("mystery-service", confirm=True)

    assert gate["status"] == "confirmation-required"
    assert "run the declared backup" in str(gate["wouldRun"])
    assert verify_gate["status"] == "confirmation-required"
    assert "prove the newest backup restores" in str(verify_gate["wouldRun"])
    assert unknown["status"] == "error"
    error = unknown["error"]
    assert isinstance(error, dict)
    assert error["code"] == "lifecycle_service_missing"


def test_backup_receipt_schema_accepts_the_playbook_shape() -> None:
    from cloudfall.validation import SchemaCatalog  # noqa: PLC0415

    receipt = {
        "apiVersion": "cloudfall/v1",
        "kind": "BackupReceipt",
        "metadata": {
            "id": "postgresql-main-restore-check",
            "description": "Backup operation receipt written by backup.yml",
        },
        "spec": {
            "service": "postgresql-main",
            "server": "h1",
            "action": "restore-check",
            "executedAt": "2026-09-11T10:00:00Z",
            "summary": "restore check passed for crm: latest.dump",
        },
    }

    SchemaCatalog(SCHEMAS).validate_named(
        "backup-receipt.schema.json", receipt
    )
