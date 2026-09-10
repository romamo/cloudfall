"""Logging configuration template tests."""

from __future__ import annotations

from pathlib import Path

import yaml
from cloudfall.inventory import PlatformInventory
from cloudfall.validation import validate_state
from jinja2 import Environment, FileSystemLoader, StrictUndefined

ROOT = Path(__file__).parents[2]
SCHEMAS = ROOT / "state" / "schemas" / "v1"
EXAMPLES = ROOT / "state" / "examples"
BACKEND_TEMPLATES = (
    ROOT / "engine" / "ansible" / "roles" / "cloudfall_logging_backend" / "templates"
)
COLLECTOR_TEMPLATES = (
    ROOT / "engine" / "ansible" / "roles" / "cloudfall_logging_collector" / "templates"
)


def _logging_stack() -> dict[str, object]:
    inventory = PlatformInventory.from_state(validate_state(EXAMPLES, SCHEMAS))
    assert len(inventory.logging_stacks) == 1
    return inventory.logging_stacks[0].as_dict()


def _services() -> list[dict[str, object]]:
    inventory = PlatformInventory.from_state(validate_state(EXAMPLES, SCHEMAS))
    assert len(inventory.services) == 1
    return [service.as_dict() for service in inventory.services]


def _alert_rules() -> list[dict[str, object]]:
    inventory = PlatformInventory.from_state(validate_state(EXAMPLES, SCHEMAS))
    assert len(inventory.alert_rules) == 1
    return [rule.as_dict() for rule in inventory.alert_rules]


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
    context = {"cloudfall_logging_backend_stack": stack}

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
    assert "$__file{/etc/cloudfall/logging/grafana-admin-password}" in grafana
    assert "ssl_verify_client on;" in gateway
    assert gateway.count("proxy_pass") == 2
    assert "location = /loki/api/v1/push" in gateway
    assert "location = /api/v1/write" in gateway
    assert "proxy_pass http://127.0.0.1:9090;" in gateway
    assert "location /" in gateway
    assert "-config.file=/etc/loki/cloudfall-operations.yaml" in loki_override


def test_backend_metrics_templates_render_loopback_prometheus() -> None:
    stack = _logging_stack()
    context = {"cloudfall_logging_backend_stack": stack}

    prometheus = yaml.safe_load(
        _render(BACKEND_TEMPLATES, "prometheus.yaml.j2", **context)
    )
    defaults = _render(BACKEND_TEMPLATES, "prometheus-defaults.j2", **context)
    datasource = yaml.safe_load(
        _render(BACKEND_TEMPLATES, "prometheus-datasource.yml.j2", **context)
    )

    assert prometheus["global"]["scrape_interval"] == "60s"
    assert prometheus["global"]["evaluation_interval"] == "60s"
    scrape_targets = prometheus["scrape_configs"][0]["static_configs"][0]
    assert scrape_targets["targets"] == ["127.0.0.1:9090"]
    assert "--web.listen-address=127.0.0.1:9090" in defaults
    assert "--web.enable-remote-write-receiver" in defaults
    assert "--storage.tsdb.retention.time=360h" in defaults
    assert "--storage.tsdb.path=/var/lib/prometheus-cloudfall" in defaults
    assert "--config.file=/etc/prometheus/cloudfall.yaml" in defaults
    assert datasource["datasources"][0]["url"] == "http://127.0.0.1:9090"
    assert datasource["datasources"][0]["isDefault"] is False


def test_collector_template_renders_mtls_and_bounded_labels() -> None:
    stack = _logging_stack()
    rendered = _render(
        COLLECTOR_TEMPLATES,
        "config.alloy.j2",
        cloudfall_logging_collector_stack=stack,
        cloudfall_server_id="h1",
    )
    override = _render(
        COLLECTOR_TEMPLATES,
        "alloy-systemd-override.conf.j2",
        cloudfall_logging_collector_stack=stack,
        cloudfall_server_id="h1",
    )

    assert 'url = "https://logs.example.internal:3101/loki/api/v1/push"' in rendered
    assert 'url = "https://logs.example.internal:3101/api/v1/write"' in rendered
    assert 'prometheus.exporter.unix "host"' in rendered
    assert 'scrape_interval = "60s"' in rendered
    assert 'server_name = "logs.example.internal"' in rendered
    assert rendered.count('min_version = "TLS12"') == 2
    assert rendered.count('environment = "production"') == 2
    assert rendered.count('server      = "h1"') == 2
    assert '"project"  = "crm"' in rendered
    assert '"component" = "crm-backend"' in rendered
    assert "request_id" not in rendered
    assert "/etc/alloy/cloudfall.alloy" in override
    assert "--storage.path=/var/lib/alloy/data" in override


def test_backend_rules_template_renders_declared_alerts() -> None:
    rules = yaml.safe_load(
        _render(
            BACKEND_TEMPLATES,
            "prometheus-rules.yaml.j2",
            cloudfall_alert_rules=_alert_rules(),
        )
    )

    assert len(rules["groups"]) == 1
    group = rules["groups"][0]
    assert group["name"] == "cloudfall"
    assert len(group["rules"]) == 1
    rule = group["rules"][0]
    assert rule["alert"] == "postgresql_down"
    assert rule["expr"] == 'pg_up{service="postgresql-main"} == 0'
    assert rule["for"] == "1m"
    assert rule["labels"] == {
        "severity": "critical",
        "environment": "production",
        "cloudfall_rule": "postgresql-down",
    }
    assert rule["annotations"]["summary"] == (
        "PostgreSQL postgresql-main is not answering its exporter"
    )


def test_backend_rules_template_renders_empty_groups_without_rules() -> None:
    rules = yaml.safe_load(
        _render(
            BACKEND_TEMPLATES,
            "prometheus-rules.yaml.j2",
            cloudfall_alert_rules=[],
        )
    )

    assert rules == {"groups": []}


def test_backend_prometheus_config_references_the_rules_file() -> None:
    stack = _logging_stack()
    prometheus = yaml.safe_load(
        _render(
            BACKEND_TEMPLATES,
            "prometheus.yaml.j2",
            cloudfall_logging_backend_stack=stack,
        )
    )

    assert prometheus["rule_files"] == ["/etc/prometheus/cloudfall-rules.yaml"]


def test_backend_alertmanager_template_renders_declared_receivers() -> None:
    stack = _logging_stack()
    context = {"cloudfall_logging_backend_stack": stack}

    alertmanager = yaml.safe_load(
        _render(BACKEND_TEMPLATES, "alertmanager.yaml.j2", **context)
    )
    defaults = _render(BACKEND_TEMPLATES, "alertmanager-defaults.j2", **context)

    assert alertmanager["route"]["receiver"] == "cloudfall"
    assert alertmanager["route"]["group_by"] == ["alertname", "environment"]
    assert len(alertmanager["receivers"]) == 1
    receiver = alertmanager["receivers"][0]
    assert receiver["name"] == "cloudfall"
    assert receiver["webhook_configs"] == [
        {"url": "https://hooks.example.internal/cloudfall"}
    ]
    assert receiver["email_configs"] == [
        {
            "to": "oncall@example.internal",
            "from": "alerts@example.internal",
            "smarthost": "smtp.example.internal:587",
            "auth_username": "alerts@example.internal",
            "auth_password_file": "/etc/cloudfall/logging/smtp-password",
        }
    ]
    assert "--web.listen-address=127.0.0.1:9093" in defaults
    assert "--cluster.listen-address=" in defaults
    assert "--config.file=/etc/prometheus/cloudfall-alertmanager.yaml" in defaults


def test_backend_prometheus_config_targets_declared_alertmanager() -> None:
    stack = _logging_stack()
    prometheus = yaml.safe_load(
        _render(
            BACKEND_TEMPLATES,
            "prometheus.yaml.j2",
            cloudfall_logging_backend_stack=stack,
        )
    )

    alertmanagers = prometheus["alerting"]["alertmanagers"]
    assert alertmanagers == [
        {"static_configs": [{"targets": ["127.0.0.1:9093"]}]}
    ]


def test_backend_prometheus_config_omits_alerting_when_undeclared() -> None:
    stack = {
        key: value for key, value in _logging_stack().items() if key != "alerting"
    }
    prometheus = yaml.safe_load(
        _render(
            BACKEND_TEMPLATES,
            "prometheus.yaml.j2",
            cloudfall_logging_backend_stack=stack,
        )
    )

    assert "alerting" not in prometheus


def test_collector_template_renders_postgres_exporter_for_declared_metrics() -> None:
    stack = _logging_stack()
    services = _services()
    rendered = _render(
        COLLECTOR_TEMPLATES,
        "config.alloy.j2",
        cloudfall_logging_collector_stack=stack,
        cloudfall_server_id="h1",
        cloudfall_services=services,
        cloudfall_logging_collector_metrics_role="alloy",
    )

    assert 'prometheus.exporter.postgres "postgresql_main"' in rendered
    assert (
        '"postgresql://alloy@:5432/postgres'
        '?host=/var/run/postgresql&sslmode=disable"' in rendered
    )
    assert 'prometheus.scrape "postgresql_main"' in rendered
    assert (
        "forward_to      = [prometheus.relabel.postgresql_main.receiver]"
        in rendered
    )
    assert 'replacement  = "postgresql-main"' in rendered
    assert "password" not in rendered


def test_collector_template_skips_postgres_exporter_without_metrics() -> None:
    stack = _logging_stack()
    services = _services()
    undeclared = [
        {key: value for key, value in service.items() if key != "metrics"}
        for service in services
    ]
    rendered = _render(
        COLLECTOR_TEMPLATES,
        "config.alloy.j2",
        cloudfall_logging_collector_stack=stack,
        cloudfall_server_id="h1",
        cloudfall_services=undeclared,
        cloudfall_logging_collector_metrics_role="alloy",
    )

    assert "prometheus.exporter.postgres" not in rendered
