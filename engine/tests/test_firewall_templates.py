"""Firewall configuration template tests."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

ROOT = Path(__file__).parents[2]
TEMPLATES = (
    ROOT / "engine" / "ansible" / "roles" / "cloudfall_firewall" / "templates"
)


def _render(ruleset: dict[str, object]) -> str:
    environment = Environment(
        loader=FileSystemLoader(TEMPLATES),
        undefined=StrictUndefined,
        autoescape=False,  # noqa: S701 - configuration files are not HTML.
        keep_trailing_newline=True,
    )
    return environment.get_template("nftables.conf.j2").render(
        cloudfall_firewall_ruleset=ruleset,
    )


def test_firewall_template_renders_default_deny_with_declared_rules() -> None:
    rendered = _render(
        {
            "policy": "default-deny",
            "allowedInbound": [
                {"port": 22, "protocol": "tcp", "description": "ssh"},
                {"port": 443, "protocol": "tcp", "description": "https"},
                {"port": 5000, "protocol": "udp"},
            ],
        }
    )

    assert "type filter hook input priority 0; policy drop;" in rendered
    assert "type filter hook forward priority 0; policy drop;" in rendered
    assert "type filter hook output priority 0; policy accept;" in rendered
    assert 'iif "lo" accept' in rendered
    assert "ct state established,related accept" in rendered
    assert 'tcp dport 22 accept comment "ssh"' in rendered
    assert 'tcp dport 443 accept comment "https"' in rendered
    assert "udp dport 5000 accept" in rendered
    assert "flush ruleset" in rendered
    assert "table inet cloudfall {" in rendered
