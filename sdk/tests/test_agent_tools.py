"""Agent toolset and MCP server tests."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from cloudfall.agent_tools import AgentConfig, AgentToolset

ROOT = Path(__file__).parents[2]
SCHEMAS = ROOT / "state" / "schemas" / "v1"
EXAMPLES = ROOT / "state" / "examples"
ENGINE = ROOT / "engine"
COMPLIANT = ROOT / "state" / "tests" / "observed" / "compliant"

BLUEPRINT = """\
services:
  - type: worker
    name: acme-worker
    runtime: python
    repo: https://github.com/example/acme.git
    startCommand: celery -A acme worker
"""


def _config(tmp_path: Path, observed: Path | None = None) -> AgentConfig:
    return AgentConfig(
        state_directory=EXAMPLES,
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
    assert validated["resources"] == 10
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


def test_import_render_tool_maps_a_blueprint(tmp_path: Path) -> None:
    blueprint = tmp_path / "render.yaml"
    blueprint.write_text(BLUEPRINT, encoding="utf-8")
    toolset = AgentToolset(_config(tmp_path))

    result = toolset.import_render(
        str(blueprint),
        "acme",
        "h1",
        str(tmp_path / "state"),
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
        "validate_state",
        "audit_servers",
        "inspect_servers",
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
