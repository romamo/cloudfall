"""Secret reference resolution tests with an injected decryptor."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest
from cloudfall.domain import ResourceId
from cloudfall.lifecycle import EngineContext
from cloudfall.secrets import (
    SecretsError,
    SopsSecretProvider,
    render_environment,
)

ROOT = Path(__file__).parents[2]
SCHEMAS = ROOT / "config" / "schemas" / "v1"
EXAMPLES = ROOT / "config" / "examples"
ENGINE = ROOT / "engine"


def _context(tmp_path: Path) -> EngineContext:
    return EngineContext(
        config_directory=EXAMPLES,
        schema_directory=SCHEMAS,
        engine_directory=ENGINE,
        inventory_file=tmp_path / "inventory.json",
    )


def _provider(tmp_path: Path) -> SopsSecretProvider:
    secrets = tmp_path / "secrets"
    (secrets / "production").mkdir(parents=True)
    (secrets / "production" / "shared.env").write_text(
        "SENTRY_DSN=https://shared.example/1\nLOG_LEVEL=warning\n",
        encoding="utf-8",
    )
    (secrets / "production" / "crm.env").write_text(
        "# application-wide\nLOG_LEVEL=info\n", encoding="utf-8"
    )
    (secrets / "production" / "crm" / "backend.env").parent.mkdir(
        parents=True, exist_ok=True
    )
    (secrets / "production" / "crm" / "backend.env").write_text(
        "STRIPE_KEY=sk_test_123\n", encoding="utf-8"
    )
    return SopsSecretProvider(
        secrets_directory=secrets,
        decrypt=lambda path: path.read_text(encoding="utf-8"),
    )


def test_render_environment_merges_refs_in_override_order(
    tmp_path: Path,
) -> None:
    output = tmp_path / "env" / "crm-backend.env"

    result = render_environment(
        _context(tmp_path),
        ResourceId.from_boundary("crm-backend"),
        _provider(tmp_path),
        output,
    )

    content = output.read_text(encoding="utf-8")
    assert "SENTRY_DSN=https://shared.example/1" in content
    assert "LOG_LEVEL=info" in content
    assert "LOG_LEVEL=warning" not in content
    assert "STRIPE_KEY=sk_test_123" in content
    assert "FEATURE_SIGNUPS=true" in content
    assert "REDIS_URL=redis://127.0.0.1:6379/0" in content
    mode = stat.S_IMODE(output.stat().st_mode)
    assert mode == 0o600
    assert result["status"] == "ok"
    assert result["keys"] == [
        "FEATURE_SIGNUPS",
        "LOG_LEVEL",
        "REDIS_URL",
        "SENTRY_DSN",
        "STRIPE_KEY",
    ]
    assert result["declaredKeys"] == ["FEATURE_SIGNUPS", "REDIS_URL"]
    serialized = str(result)
    assert "sk_test_123" not in serialized
    assert "https://shared.example/1" not in serialized


def test_missing_secret_source_fails_with_the_expected_path(
    tmp_path: Path,
) -> None:
    provider = SopsSecretProvider(
        secrets_directory=tmp_path / "secrets",
        decrypt=lambda path: path.read_text(encoding="utf-8"),
    )

    with pytest.raises(SecretsError) as caught:
        render_environment(
            _context(tmp_path),
            ResourceId.from_boundary("crm-backend"),
            provider,
            tmp_path / "out.env",
        )

    assert caught.value.code == "secrets_source_missing"
    assert "production/shared.env" in caught.value.message


def test_malformed_source_lines_fail_fast(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    shared = tmp_path / "secrets" / "production" / "shared.env"
    shared.write_text("not a pair\n", encoding="utf-8")

    with pytest.raises(SecretsError) as caught:
        render_environment(
            _context(tmp_path),
            ResourceId.from_boundary("crm-backend"),
            provider,
            tmp_path / "out.env",
        )

    assert caught.value.code == "secrets_source_invalid"
    assert "line 1" in caught.value.message


def test_unknown_component_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SecretsError) as caught:
        render_environment(
            _context(tmp_path),
            ResourceId.from_boundary("mystery"),
            _provider(tmp_path),
            tmp_path / "out.env",
        )

    assert caught.value.code == "secrets_component_unknown"


def test_declared_and_secret_key_conflicts_fail_fast(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    backend = tmp_path / "secrets" / "production" / "crm" / "backend.env"
    backend.write_text(
        "STRIPE_KEY=sk_test_123\nFEATURE_SIGNUPS=false\n", encoding="utf-8"
    )

    with pytest.raises(SecretsError) as caught:
        render_environment(
            _context(tmp_path),
            ResourceId.from_boundary("crm-backend"),
            provider,
            tmp_path / "out.env",
        )

    assert caught.value.code == "secrets_key_conflict"
    assert "FEATURE_SIGNUPS" in caught.value.message
    assert "exactly one place" in caught.value.message
