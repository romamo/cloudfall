"""Render deterministic static Ansible inventory from typed Atlas inventory."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from atlas.domain import ResourceId
    from atlas.inventory import (
        ComponentInventory,
        LoggingStackInventory,
        PlatformInventory,
        ProjectInventory,
        ServerInventory,
    )


def render_ansible_inventory(inventory: PlatformInventory) -> dict[str, object]:
    """Render static JSON accepted by Ansible's YAML inventory plugin."""
    environment_groups: dict[str, object] = {}
    environments = sorted(
        {server.environment for server in inventory.servers},
        key=lambda environment: environment.value,
    )
    for environment in environments:
        environment_groups[_group_name("environment", environment)] = {
            "hosts": _empty_host_entries(
                server.resource_id.value
                for server in inventory.servers
                if server.environment == environment
            )
        }

    project_groups: dict[str, object] = {}
    for project in inventory.projects:
        project_components = frozenset(project.component_ids)
        project_hosts = sorted(
            {
                server_id.value
                for component in inventory.components
                if component.resource_id in project_components
                for server_id in component.server_ids
            }
        )
        project_groups[_group_name("project", project.resource_id)] = {
            "hosts": _empty_host_entries(project_hosts)
        }

    component_groups = {
        _group_name("component", component.resource_id): {
            "hosts": _empty_host_entries(
                server.value for server in component.server_ids
            )
        }
        for component in inventory.components
    }

    logging_backend_hosts = _empty_host_entries(
        stack.backend.server_id.value for stack in inventory.logging_stacks
    )
    logging_collector_hosts = _empty_host_entries(
        server_id.value
        for stack in inventory.logging_stacks
        for server_id in stack.collectors.server_ids
    )
    domain_origin_hosts = _empty_host_entries(
        domain.origin.server_id.value for domain in inventory.domains
    )

    return {
        "all": {
            "children": {
                "ungrouped": {"hosts": {}},
                "atlas_servers": {"hosts": _host_variables(inventory)},
                "atlas_environments": {"children": environment_groups},
                "atlas_projects": {"children": project_groups},
                "atlas_components": {"children": component_groups},
                "atlas_logging_backends": {"hosts": logging_backend_hosts},
                "atlas_logging_collectors": {"hosts": logging_collector_hosts},
                "atlas_domain_origins": {"hosts": domain_origin_hosts},
            }
        }
    }


def _host_variables(inventory: PlatformInventory) -> dict[str, object]:
    projects_by_id = {project.resource_id: project for project in inventory.projects}
    profiles_by_id = {profile.resource_id: profile for profile in inventory.profiles}
    return {
        server.resource_id.value: {
            "ansible_host": server.address.value,
            "ansible_port": server.ssh_port.value,
            "ansible_user": server.ssh_user.value,
            "atlas_server_id": server.resource_id.value,
            "atlas_hostname": server.hostname.value,
            "atlas_environment": server.environment.value,
            "atlas_lifecycle": server.lifecycle.value,
            "atlas_labels": {label.key.value: label.value for label in server.labels},
            "atlas_ssh_public_keys": [
                key.as_dict()
                for key in inventory.ssh_public_keys
                if key.environment == server.environment and key.is_active
            ],
            **_host_infrastructure(server),
            **_host_logging(server, inventory.logging_stacks),
            "atlas_host_profile": profiles_by_id[server.profile_id].as_dict(),
            "atlas_projects": [
                _host_project(project)
                for project in inventory.projects
                if _project_is_on_server(project, server, inventory)
            ],
            "atlas_components": [
                _host_component(component, projects_by_id[component.project_id])
                for component in inventory.components
                if server.resource_id in component.server_ids
            ],
            "atlas_domains": [
                domain.as_dict()
                for domain in inventory.domains
                if domain.proxy.server_id == server.resource_id
            ],
            "atlas_origin_domains": [
                domain.as_dict()
                for domain in inventory.domains
                if domain.origin.server_id == server.resource_id
            ],
        }
        for server in inventory.servers
    }


def _host_infrastructure(server: ServerInventory) -> dict[str, object]:
    variables: dict[str, object] = {}
    if server.provider is not None:
        variables["atlas_provider"] = server.provider.as_dict()
    if server.network is not None:
        variables["atlas_network"] = server.network.as_dict()
    return variables


def _host_logging(
    server: ServerInventory,
    logging_stacks: tuple[LoggingStackInventory, ...],
) -> dict[str, object]:
    variables: dict[str, object] = {}
    backend = next(
        (
            stack
            for stack in logging_stacks
            if stack.backend.server_id == server.resource_id
        ),
        None,
    )
    collector = next(
        (
            stack
            for stack in logging_stacks
            if server.resource_id in stack.collectors.server_ids
        ),
        None,
    )
    if backend is not None:
        variables["atlas_logging_backend"] = backend.as_dict()
    if collector is not None:
        variables["atlas_logging_collector"] = collector.as_dict()
    return variables


def _host_project(project: ProjectInventory) -> dict[str, object]:
    return {
        "id": project.resource_id.value,
        "user": project.linux_user.value,
        "approval": project.approval.value,
    }


def _host_component(
    component: ComponentInventory, project: ProjectInventory
) -> dict[str, object]:
    return {
        "id": component.resource_id.value,
        "project": component.project_id.value,
        "project_user": project.linux_user.value,
        "install_root": component.install_root.value,
    }


def _project_is_on_server(
    project: ProjectInventory,
    server: ServerInventory,
    inventory: PlatformInventory,
) -> bool:
    project_components = frozenset(project.component_ids)
    return any(
        component.resource_id in project_components
        and server.resource_id in component.server_ids
        for component in inventory.components
    )


def _empty_host_entries(hosts: Iterable[str]) -> dict[str, object]:
    return {host: {} for host in hosts}


def _group_name(prefix: str, resource_id: ResourceId) -> str:
    return f"{prefix}_{resource_id.value.replace('-', '_')}"
