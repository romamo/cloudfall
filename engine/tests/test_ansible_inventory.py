"""Ansible inventory rendering acceptance tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from atlas.inventory import PlatformInventory
from atlas.validation import validate_state
from atlas_engine.ansible_inventory import render_ansible_inventory
from atlas_engine.cli import main

if TYPE_CHECKING:
    import pytest

ROOT = Path(__file__).parents[2]
SCHEMAS = ROOT / "state" / "schemas" / "v1"
EXAMPLES = ROOT / "state" / "examples"


def _rendered_inventory() -> dict[str, object]:
    state = validate_state(EXAMPLES, SCHEMAS)
    return render_ansible_inventory(PlatformInventory.from_state(state))


def _mapping_entry(mapping: dict[str, object], key: str) -> dict[str, object]:
    value = mapping[key]
    assert isinstance(value, dict)
    return value


def _inventory_children(rendered: dict[str, object]) -> dict[str, object]:
    all_group = _mapping_entry(rendered, "all")
    return _mapping_entry(all_group, "children")


def test_inventory_renders_ansible_host_variables() -> None:
    children = _inventory_children(_rendered_inventory())
    servers_group = _mapping_entry(children, "atlas_servers")
    hostvars = _mapping_entry(servers_group, "hosts")
    h1 = dict(_mapping_entry(hostvars, "h1"))
    logging_backend = _mapping_entry(h1, "atlas_logging_backend")
    logging_collector = _mapping_entry(h1, "atlas_logging_collector")
    ssh_public_keys = h1["atlas_ssh_public_keys"]
    del h1["atlas_logging_backend"]
    del h1["atlas_logging_collector"]
    del h1["atlas_ssh_public_keys"]

    assert isinstance(ssh_public_keys, list)
    assert len(ssh_public_keys) == 1
    assert isinstance(ssh_public_keys[0], dict)
    assert ssh_public_keys[0]["id"] == "example-admin"
    assert ssh_public_keys[0]["algorithm"] == "ssh-rsa"
    assert ssh_public_keys[0]["lifecycle"] == "active"

    assert h1 == {
        "ansible_host": "h1.example.internal",
        "ansible_port": 22,
        "ansible_user": "atlas",
        "atlas_components": [
            {
                "id": "crm-backend",
                "install_root": "/srv/apps/crm/backend",
                "project": "crm",
                "project_user": "crm",
            }
        ],
        "atlas_domains": [],
        "atlas_origin_domains": [],
        "atlas_environment": "production",
        "atlas_host_profile": {
            "configuration": {
                "files": [
                    {
                        "capture": "hash",
                        "group": "root",
                        "mode": "0644",
                        "owner": "root",
                        "path": "/etc/ssh/sshd_config",
                    }
                ]
            },
            "id": "debian-application",
            "os": {
                "distribution": "Debian",
                "serviceManager": "systemd",
                "versions": ["12", "13"],
            },
            "packages": {
                "forbidden": ["telnetd"],
                "required": [
                    {"name": "acl"},
                    {"name": "ca-certificates"},
                    {"name": "curl"},
                    {"name": "git"},
                    {"name": "rsync"},
                    {"name": "unzip"},
                ],
            },
            "services": {
                "required": [
                    {
                        "name": "ssh.service",
                        "state": "running",
                        "status": "enabled",
                    }
                ]
            },
            "storage": {
                "mounts": [
                    {
                        "filesystem": "ext4",
                        "minimumBytes": 850000000000,
                        "path": "/",
                    }
                ],
                "softwareRaid": {
                    "level": "raid1",
                    "minimumActiveDevices": 2,
                    "minimumUsableBytes": 900000000000,
                },
            },
        },
        "atlas_hostname": "h1.example.internal",
        "atlas_labels": {
            "region": "eu-central",
            "workload": "application",
        },
        "atlas_lifecycle": "active",
        "atlas_projects": [{"approval": "manual", "id": "crm", "user": "crm"}],
        "atlas_server_id": "h1",
    }
    assert logging_backend["id"] == "operations"
    assert _mapping_entry(logging_backend, "backend")["server"] == "h1"
    assert _mapping_entry(logging_collector, "collectors")["servers"] == [
        "h1",
        "h2",
    ]
    assert _mapping_entry(logging_collector, "migration") == {
        "mode": "parallel",
        "preserveLegacyAgents": True,
    }


def test_inventory_renders_environment_project_and_component_groups() -> None:
    children = _inventory_children(_rendered_inventory())
    environments = _mapping_entry(children, "atlas_environments")
    environment_groups = _mapping_entry(environments, "children")
    projects = _mapping_entry(children, "atlas_projects")
    project_groups = _mapping_entry(projects, "children")
    components = _mapping_entry(children, "atlas_components")
    component_groups = _mapping_entry(components, "children")
    logging_backends = _mapping_entry(children, "atlas_logging_backends")
    logging_collectors = _mapping_entry(children, "atlas_logging_collectors")
    domain_origins = _mapping_entry(children, "atlas_domain_origins")

    assert environment_groups["environment_production"] == {
        "hosts": {"h1": {}, "h2": {}},
    }
    assert project_groups["project_crm"] == {
        "hosts": {"h1": {}, "h2": {}},
    }
    assert component_groups["component_crm_backend"] == {
        "hosts": {"h1": {}, "h2": {}},
    }
    assert logging_backends == {"hosts": {"h1": {}}}
    assert logging_collectors == {"hosts": {"h1": {}, "h2": {}}}
    assert domain_origins == {"hosts": {}}
    assert children["ungrouped"] == {"hosts": {}}


def test_engine_cli_emits_ansible_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(
        [
            "inventory",
            "render",
            str(EXAMPLES),
            "--schemas",
            str(SCHEMAS),
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert (
        payload["all"]["children"]["atlas_servers"]["hosts"]["h1"]["ansible_user"]
        == "atlas"
    )
    assert (
        "component_crm_backend"
        in payload["all"]["children"]["atlas_components"]["children"]
    )
    assert captured.err == ""


def test_engine_cli_can_write_inventory_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output_path = tmp_path / "inventory.json"
    exit_code = main(
        [
            "inventory",
            "render",
            str(EXAMPLES),
            "--schemas",
            str(SCHEMAS),
            "--output",
            str(output_path),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert json.loads(captured.out) == {
        "output": str(output_path),
        "status": "ok",
    }
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert sorted(written["all"]["children"]["atlas_servers"]["hosts"]) == [
        "h1",
        "h2",
    ]
    assert captured.err == ""
