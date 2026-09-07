"""Logging configuration template tests."""

from __future__ import annotations

from pathlib import Path

import yaml
from atlas.inventory import PlatformInventory
from atlas.validation import validate_state
from jinja2 import Environment, FileSystemLoader, StrictUndefined

ROOT = Path(__file__).parents[2]
SCHEMAS = ROOT / "state" / "schemas" / "v1"
EXAMPLES = ROOT / "state" / "examples"
BACKEND_TEMPLATES = (
    ROOT / "engine" / "ansible" / "roles" / "atlas_logging_backend" / "templates"
)
COLLECTOR_TEMPLATES = (
    ROOT / "engine" / "ansible" / "roles" / "atlas_logging_collector" / "templates"
)


def _logging_stack() -> dict[str, object]:
    inventory = PlatformInventory.from_state(validate_state(EXAMPLES, SCHEMAS))
    assert len(inventory.logging_stacks) == 1
    return inventory.logging_stacks[0].as_dict()


def _render(directory: Path, name: str, **context: object) -> str:
    environment = Environment(
        loader=FileSystemLoader(directory),
        undefined=StrictUndefined,
        autoescape=False,  # noqa: S701 - configuration files are not HTML.
        keep_trailing_newline=True,
    )
    return environment.get_template(name).render(**context)


def test_backend_templates_render_safe_endpoints() -> None:
    stack = _logging_stack()
    context = {"atlas_logging_backend_stack": stack}

    loki = yaml.safe_load(_render(BACKEND_TEMPLATES, "loki.yaml.j2", **context))
    datasource = yaml.safe_load(
        _render(BACKEND_TEMPLATES, "loki-datasource.yml.j2", **context)
    )
    grafana = _render(BACKEND_TEMPLATES, "grafana.ini.j2", **context)
    gateway = _render(BACKEND_TEMPLATES, "nginx-loki-gateway.conf.j2", **context)
    loki_override = _render(
        BACKEND_TEMPLATES, "loki-systemd-override.conf.j2", **context
    )

    assert loki["server"]["http_listen_address"] == "127.0.0.1"
    assert loki["schema_config"]["configs"][0]["store"] == "tsdb"
    assert loki["limits_config"]["retention_period"] == "720h"
    assert datasource["datasources"][0]["url"] == "http://127.0.0.1:3100"
    assert "$__file{/etc/atlas/logging/grafana-admin-password}" in grafana
    assert "ssl_verify_client on;" in gateway
    assert gateway.count("proxy_pass") == 1
    assert "location = /loki/api/v1/push" in gateway
    assert "location /" in gateway
    assert "-config.file=/etc/loki/atlas-operations.yaml" in loki_override


def test_collector_template_renders_mtls_and_bounded_labels() -> None:
    stack = _logging_stack()
    rendered = _render(
        COLLECTOR_TEMPLATES,
        "config.alloy.j2",
        atlas_logging_collector_stack=stack,
        atlas_server_id="h1",
    )
    override = _render(
        COLLECTOR_TEMPLATES,
        "alloy-systemd-override.conf.j2",
        atlas_logging_collector_stack=stack,
        atlas_server_id="h1",
    )

    assert 'url = "https://logs.example.internal:3101/loki/api/v1/push"' in rendered
    assert 'server_name = "logs.example.internal"' in rendered
    assert 'min_version = "TLS12"' in rendered
    assert 'environment = "production"' in rendered
    assert 'server      = "h1"' in rendered
    assert '"project"  = "crm"' in rendered
    assert '"component" = "crm-backend"' in rendered
    assert "request_id" not in rendered
    assert "/etc/alloy/atlas.alloy" in override
    assert "--storage.path=/var/lib/alloy/data" in override
