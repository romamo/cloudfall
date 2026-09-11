"""Component lifecycle orchestration tests."""

from __future__ import annotations

import hashlib
import json
import tarfile
from pathlib import Path

import pytest
from cloudfall.cli import main
from cloudfall.domain import ReleaseId, ResourceId
from cloudfall.lifecycle import (
    DeployOptions,
    EngineContext,
    LifecycleError,
    deploy,
    plan_deploy,
    plan_health,
    verify_artifact,
)

ROOT = Path(__file__).parents[2]
SCHEMAS = ROOT / "config" / "schemas" / "v1"
EXAMPLES = ROOT / "config" / "examples"
ENGINE = ROOT / "engine"
RELEASE = "20260101T000000Z-abcdef0"


def _context(tmp_path: Path) -> EngineContext:
    return EngineContext(
        config_directory=EXAMPLES,
        schema_directory=SCHEMAS,
        engine_directory=ENGINE,
        inventory_file=tmp_path / "inventory.json",
    )


def _write_artifact(tmp_path: Path) -> Path:
    artifact_directory = tmp_path / "artifacts"
    component_directory = artifact_directory / "crm-backend"
    component_directory.mkdir(parents=True)
    source = tmp_path / "source"
    source.mkdir()
    (source / "app.py").write_text("print('crm')\n", encoding="utf-8")
    archive_path = component_directory / f"{RELEASE}.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        archive.add(source, arcname=".")
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    metadata = {
        "apiVersion": "cloudfall/v1",
        "kind": "Artifact",
        "metadata": {"id": "crm-backend"},
        "spec": {
            "component": "crm-backend",
            "release": RELEASE,
            "gitRef": "main",
            "gitCommit": "a" * 40,
            "builtAt": "2026-01-01T00:00:00Z",
            "archive": archive_path.name,
            "archiveSha256": digest,
            "sizeBytes": archive_path.stat().st_size,
        },
    }
    (component_directory / f"{RELEASE}.json").write_text(
        json.dumps(metadata), encoding="utf-8"
    )
    return artifact_directory


def test_verify_artifact_accepts_a_consistent_release(tmp_path: Path) -> None:
    artifact_directory = _write_artifact(tmp_path)

    verified = verify_artifact(
        artifact_directory,
        ResourceId("crm-backend"),
        ReleaseId(RELEASE),
        SCHEMAS,
    )

    assert verified.archive_path.name == f"{RELEASE}.tar.gz"
    assert len(verified.sha256) == 64


def test_verify_artifact_rejects_a_missing_release(tmp_path: Path) -> None:
    with pytest.raises(LifecycleError) as error:
        verify_artifact(
            tmp_path / "artifacts",
            ResourceId("crm-backend"),
            ReleaseId(RELEASE),
            SCHEMAS,
        )

    assert error.value.code == "lifecycle_artifact_missing"


def test_verify_artifact_rejects_a_tampered_archive(tmp_path: Path) -> None:
    artifact_directory = _write_artifact(tmp_path)
    archive = artifact_directory / "crm-backend" / f"{RELEASE}.tar.gz"
    archive.write_bytes(archive.read_bytes() + b"tampered")

    with pytest.raises(LifecycleError) as error:
        verify_artifact(
            artifact_directory,
            ResourceId("crm-backend"),
            ReleaseId(RELEASE),
            SCHEMAS,
        )

    assert error.value.code == "lifecycle_artifact_digest_mismatch"


def test_verify_artifact_rejects_mismatched_identity(tmp_path: Path) -> None:
    artifact_directory = _write_artifact(tmp_path)
    metadata_path = artifact_directory / "crm-backend" / f"{RELEASE}.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["spec"]["component"] = "billing-backend"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    with pytest.raises(LifecycleError) as error:
        verify_artifact(
            artifact_directory,
            ResourceId("crm-backend"),
            ReleaseId(RELEASE),
            SCHEMAS,
        )

    assert error.value.code == "lifecycle_artifact_identity_mismatch"


def test_deploy_rejects_an_undeclared_component(tmp_path: Path) -> None:
    with pytest.raises(LifecycleError) as error:
        deploy(
            _context(tmp_path),
            ResourceId("billing-backend"),
            ReleaseId(RELEASE),
            tmp_path / "artifacts",
        )

    assert error.value.code == "lifecycle_component_missing"


def test_deploy_plan_composes_engine_contract_steps(tmp_path: Path) -> None:
    artifact_directory = _write_artifact(tmp_path)
    context = _context(tmp_path)
    verified = verify_artifact(
        artifact_directory,
        ResourceId("crm-backend"),
        ReleaseId(RELEASE),
        SCHEMAS,
    )

    plan = plan_deploy(
        context,
        ResourceId("crm-backend"),
        ReleaseId(RELEASE),
        verified,
        DeployOptions(receipt_directory=tmp_path / "releases"),
    )

    render, playbook = plan.steps
    assert "cloudfall_engine" in render.argv
    assert str(context.inventory_file) in render.argv
    assert playbook.argv[-1].endswith("deploy.yml")
    assert playbook.environment["ANSIBLE_CONFIG"].endswith("ansible.cfg")
    extra_vars = json.loads(playbook.argv[playbook.argv.index("--extra-vars") + 1])
    assert extra_vars["cloudfall_deploy_component_id"] == "crm-backend"
    assert extra_vars["cloudfall_deploy_release"] == RELEASE
    assert extra_vars["cloudfall_deploy_artifact_sha256"] == verified.sha256
    assert extra_vars["cloudfall_deploy_receipt_directory"] == str(
        tmp_path / "releases"
    )


def test_health_plan_targets_the_health_playbook(tmp_path: Path) -> None:
    plan = plan_health(_context(tmp_path), ResourceId("crm-backend"))

    assert plan.action == "health"
    assert plan.steps[-1].argv[-1].endswith("health.yml")


def test_cli_deploy_reports_a_missing_artifact(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(
        [
            "deploy",
            str(EXAMPLES),
            "crm-backend",
            "--release",
            RELEASE,
            "--schemas",
            str(SCHEMAS),
            "--engine",
            str(ENGINE),
            "--artifacts",
            str(tmp_path / "artifacts"),
            "--inventory-file",
            str(tmp_path / "inventory.json"),
            "--receipts",
            str(tmp_path / "releases"),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    payload = json.loads(captured.err)
    assert payload["error"]["code"] == "lifecycle_artifact_missing"
