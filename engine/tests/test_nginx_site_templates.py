"""Managed virtual-host template tests."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

ROOT = Path(__file__).parents[2]
TEMPLATES = (
    ROOT / "engine" / "ansible" / "roles" / "cloudfall_nginx_site" / "templates"
)


def _domain() -> dict[str, object]:
    return {
        "id": "crm-site",
        "primaryName": "crm.example.test",
        "aliases": ["www.crm.example.test"],
        "proxy": {
            "server": "h2",
            "configurationPath": (
                "/etc/nginx/sites-available/crm.example.test.conf"
            ),
            "service": "nginx.service",
            "upstream": {"address": "127.0.0.1", "port": 8100},
        },
        "tls": {"mode": "required"},
    }


def _render(*, tls_active: bool) -> str:
    environment = Environment(
        loader=FileSystemLoader(TEMPLATES),
        undefined=StrictUndefined,
        autoescape=False,  # noqa: S701 - configuration files are not HTML.
        keep_trailing_newline=True,
    )
    return environment.get_template("domain-site.conf.j2").render(
        cloudfall_nginx_site_domain=_domain(),
        cloudfall_nginx_site_acme_webroot="/var/lib/letsencrypt",
        cloudfall_nginx_site_tls_active=tls_active,
    )


def test_site_discards_client_forwarding_headers() -> None:
    rendered = _render(tls_active=True)

    assert "proxy_set_header X-Forwarded-For $remote_addr;" in rendered
    assert "proxy_add_x_forwarded_for" not in rendered
    assert "proxy_set_header X-Real-IP $remote_addr;" in rendered
    assert "proxy_set_header X-Forwarded-Proto $scheme;" in rendered


def test_tls_site_redirects_http_and_terminates_https() -> None:
    rendered = _render(tls_active=True)

    assert "server_name crm.example.test www.crm.example.test;" in rendered
    assert "return 301 https://$host$request_uri;" in rendered
    assert "listen 443 ssl;" in rendered
    assert (
        "ssl_certificate /etc/letsencrypt/live/crm.example.test/fullchain.pem;"
        in rendered
    )
    assert "ssl_protocols TLSv1.2 TLSv1.3;" in rendered
    assert "proxy_pass http://127.0.0.1:8100;" in rendered
    assert "location /.well-known/acme-challenge/" in rendered


def test_bootstrap_site_serves_http_until_a_certificate_exists() -> None:
    rendered = _render(tls_active=False)

    assert "listen 443" not in rendered
    assert "return 301" not in rendered
    assert "proxy_pass http://127.0.0.1:8100;" in rendered
    assert "location /.well-known/acme-challenge/" in rendered
