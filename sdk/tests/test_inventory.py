"""Typed platform inventory tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from atlas.cli import main
from atlas.domain import ResourceId
from atlas.inventory import PlatformInventory
from atlas.validation import validate_state

ROOT = Path(__file__).parents[2]
SCHEMAS = ROOT / "state" / "schemas" / "v1"
EXAMPLES = ROOT / "state" / "examples"


def _inventory() -> PlatformInventory:
    return PlatformInventory.from_state(validate_state(EXAMPLES, SCHEMAS))


def test_inventory_exposes_typed_placement_queries() -> None:
    inventory = _inventory()

    components = inventory.components_on_server(ResourceId("h1"))
    servers = inventory.servers_for_component(ResourceId("crm-backend"))

    assert [component.resource_id.value for component in components] == ["crm-backend"]
    assert [server.resource_id.value for server in servers] == ["h1", "h2"]


def test_inventory_rejects_unknown_query_targets() -> None:
    inventory = _inventory()

    with pytest.raises(KeyError, match="server does not exist"):
        inventory.components_on_server(ResourceId("h9"))

    with pytest.raises(KeyError, match="component does not exist"):
        inventory.servers_for_component(ResourceId("crm-worker"))


def test_inventory_serialization_excludes_secret_references() -> None:
    payload = _inventory().as_dict()
    servers = payload["servers"]
    profiles = payload["hostProfiles"]
    projects = payload["projects"]
    components = payload["components"]
    domains = payload["domains"]
    ssh_public_keys = payload["sshPublicKeys"]
    logging_stacks = payload["loggingStacks"]

    assert isinstance(servers, list)
    assert isinstance(profiles, list)
    assert isinstance(projects, list)
    assert isinstance(components, list)
    assert isinstance(domains, list)
    assert isinstance(ssh_public_keys, list)
    assert len(servers) == 2
    assert len(profiles) == 1
    assert len(projects) == 1
    assert len(components) == 1
    assert domains == []
    assert len(ssh_public_keys) == 1
    assert ssh_public_keys[0]["id"] == "example-admin"
    assert ssh_public_keys[0]["algorithm"] == "ssh-rsa"
    assert ssh_public_keys[0]["lifecycle"] == "active"
    assert isinstance(logging_stacks, list)
    assert logging_stacks[0]["id"] == "operations"
    assert logging_stacks[0]["migration"]["preserveLegacyAgents"] is True
    assert logging_stacks[0]["backend"]["listenAddress"] == "127.0.0.1"
    assert profiles[0]["id"] == "debian-application"
    serialized = json.dumps(payload).lower()
    assert "private key" not in serialized
    assert "passwordvalue" not in serialized


def test_inventory_cli_emits_structured_output(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(
        [
            "inventory",
            "show",
            str(EXAMPLES),
            "--schemas",
            str(SCHEMAS),
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["inventory"]["components"][0]["id"] == "crm-backend"
    assert captured.err == ""
