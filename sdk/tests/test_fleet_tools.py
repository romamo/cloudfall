"""The agent surface on a brownfield repository."""

from __future__ import annotations

import asyncio
import json
import textwrap
from typing import TYPE_CHECKING, Any, cast

import pytest
from cloudfall.fleet_tools import (
    FleetConfig,
    FleetToolError,
    FleetToolset,
    operation_tool_description,
    operation_tool_name,
)
from cloudfall.mcp_server import create_fleet_server
from cloudfall.resources import default_engine_directory, default_schema_directory

if TYPE_CHECKING:
    from pathlib import Path

    from mcp.server.mcpserver import MCPServer
    from mcp.types import CallToolResult, TextContent

SCHEMAS = default_schema_directory()
ENGINE = default_engine_directory()

HOSTS = """\
---
all:
  children:
    app_servers:
      hosts:
        web-1:
          ansible_host: 127.0.0.1
"""

GROUP_VARS = """\
---
ansible_user: ansible
cloudfall_server_types:
  web:
    os:
      distribution: Debian
      versions:
        - '13'
      service_manager: systemd
    storage:
      mounts:
        - path: /
          filesystem: ext4
          minimum_bytes: 20000000000
    packages:
      required:
        - name: curl
      forbidden:
        - telnetd
    services:
      required:
        - name: ssh.service
          state: running
          status: enabled
    configuration:
      files:
        - path: /etc/ssh/sshd_config
          capture: hash
"""

WEB_1 = """\
---
cloudfall:
  environment: production
  server_type: web
  lifecycle: active
"""

MARKER = """\
id: write-marker
description: Write the release marker
playbook: playbooks/marker.yml
risk: mutating
targets: host
inputs:
  version: string
verify:
  playbook: playbooks/marker.yml
"""

PURGE = """\
id: purge
description: Remove what nothing points at
playbook: playbooks/marker.yml
risk: destructive
targets: fleet
verify:
  playbook: playbooks/marker.yml
"""

PLAYBOOK = """\
---
- name: Write the marker
  hosts: all
  connection: local
  gather_facts: false
  tasks:
    - name: Place the marker
      ansible.builtin.copy:
        content: "release {{ version | default('none') }}\\n"
        dest: "{{ playbook_dir }}/../marker.txt"
        mode: "0600"
"""


def _repository(tmp_path: Path, **operations: str) -> Path:
    repository = tmp_path / "fleet-ansible"
    inventory = repository / "inventories" / "production"
    (inventory / "group_vars" / "all").mkdir(parents=True)
    (inventory / "host_vars").mkdir(parents=True)
    (inventory / "hosts.yml").write_text(HOSTS, encoding="utf-8")
    (inventory / "group_vars" / "all" / "main.yml").write_text(
        GROUP_VARS, encoding="utf-8"
    )
    (inventory / "host_vars" / "web-1.yml").write_text(WEB_1, encoding="utf-8")
    (repository / "ansible.cfg").write_text(
        textwrap.dedent("""\
            [defaults]
            inventory = inventories/production
            retry_files_enabled = False
            interpreter_python = auto_silent
            """),
        encoding="utf-8",
    )
    (repository / "playbooks").mkdir()
    (repository / "playbooks" / "marker.yml").write_text(PLAYBOOK, encoding="utf-8")
    declared = operations or {"write-marker": MARKER}
    (repository / "operations").mkdir()
    for name, body in declared.items():
        (repository / "operations" / f"{name}.yml").write_text(
            body, encoding="utf-8"
        )
    return repository


def _toolset(repository: Path) -> FleetToolset:
    return FleetToolset(
        FleetConfig(
            repository=repository,
            schema_directory=SCHEMAS,
            engine_directory=ENGINE,
        )
    )


def _server(repository: Path) -> MCPServer:
    return create_fleet_server(
        FleetConfig(
            repository=repository,
            schema_directory=SCHEMAS,
            engine_directory=ENGINE,
        )
    )


def _call(
    server: MCPServer, name: str, arguments: dict[str, object]
) -> Any:  # noqa: ANN401 - a tool's envelope is read like parsed JSON
    """Call one tool and read the JSON envelope it returned."""
    result = asyncio.run(server.call_tool(name, arguments))
    content = cast("CallToolResult", result).content[0]
    return json.loads(cast("TextContent", content).text)


def test_the_tool_list_is_the_catalog(tmp_path: Path) -> None:
    """A playbook the team has not declared is not reachable."""
    repository = _repository(tmp_path, **{"write-marker": MARKER, "purge": PURGE})

    tools = asyncio.run(_server(repository).list_tools())

    names = [tool.name for tool in tools]
    assert "operation_write_marker" in names
    assert "operation_purge" in names
    assert not any("approve" in name for name in names)
    assert {
        "list_operations",
        "show_operation",
        "show_fleet",
        "observe_fleet",
        "audit_fleet",
        "list_decisions",
    } <= set(names)


def test_the_declared_risk_reaches_the_client_as_annotations(
    tmp_path: Path,
) -> None:
    """A client gates on risk without Cloudfall's help."""
    repository = _repository(tmp_path, **{"write-marker": MARKER, "purge": PURGE})

    tools = {tool.name: tool for tool in asyncio.run(_server(repository).list_tools())}

    mutating = tools["operation_write_marker"].annotations
    destructive = tools["operation_purge"].annotations
    assert mutating is not None
    assert destructive is not None
    assert mutating.read_only_hint is True
    assert mutating.destructive_hint is False
    assert destructive.read_only_hint is True
    assert destructive.destructive_hint is True


def test_an_operation_tool_describes_its_risk_and_its_approval(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    operation = _toolset(repository).catalog().operations[0]

    description = operation_tool_description(operation)

    assert operation_tool_name(operation) == "operation_write_marker"
    assert "Risk: mutating. Targets: host." in description
    assert "check mode only" in description
    assert "cloudfall operations approve" in description
    assert "Inputs: version: string." in description


def test_calling_an_operation_tool_changes_nothing(tmp_path: Path) -> None:
    """The agent's reach ends at a recorded proposal."""
    repository = _repository(tmp_path)

    payload = _call(
        _server(repository),
        "operation_write_marker",
        {"target": "web-1", "inputs": {"version": "2.0.0"}},
    )

    assert payload["decision"]["spec"]["status"] == "proposed"
    assert payload["decision"]["spec"]["inputs"] == {"version": "2.0.0"}
    assert payload["approval"]["required"] is True
    assert payload["approval"]["command"].startswith("cloudfall operations approve")
    assert not (repository / "marker.txt").exists()


def test_a_proposal_through_the_surface_lands_in_the_record(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    server = _server(repository)

    _call(
        server,
        "operation_write_marker",
        {"target": "web-1", "inputs": {"version": "2.0.0"}},
    )
    listed = _call(server, "list_decisions", {})

    assert [entry["spec"]["operation"]["id"] for entry in listed["decisions"]] == [
        "write-marker"
    ]


def test_the_surface_reads_the_fleet_from_the_team_inventory(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)

    payload = _call(_server(repository), "show_fleet", {})

    assert payload["ansible"]["managed"] == ["web-1"]
    assert [server["id"] for server in payload["inventory"]["servers"]] == ["web-1"]


def test_an_undeclared_operation_is_not_callable(tmp_path: Path) -> None:
    repository = _repository(tmp_path)

    tools = asyncio.run(_server(repository).list_tools())

    assert "operation_purge" not in {tool.name for tool in tools}


def test_a_repository_that_names_no_inventory_says_so(tmp_path: Path) -> None:
    repository = tmp_path / "bare"
    repository.mkdir()
    config = FleetConfig(
        repository=repository, schema_directory=SCHEMAS, engine_directory=ENGINE
    )

    with pytest.raises(FleetToolError) as error:
        config.source()

    assert error.value.code == "fleet_inventory_undeclared"


def test_the_inventory_comes_from_their_own_configuration(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    config = FleetConfig(
        repository=repository, schema_directory=SCHEMAS, engine_directory=ENGINE
    )

    assert config.source().value == repository / "inventories" / "production"
