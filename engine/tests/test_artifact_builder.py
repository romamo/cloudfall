"""Release artifact builder tests."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest
from cloudfall.inventory import PlatformInventory
from cloudfall.validation import validate_state
from cloudfall_engine.artifact import ArtifactBuildError, build_artifact

ROOT = Path(__file__).parents[2]
SCHEMAS = ROOT / "state" / "schemas" / "v1"
EXAMPLES = ROOT / "state" / "examples"
_GIT = shutil.which("git") or "git"


def _git(*arguments: str) -> str:
    completed = subprocess.run(  # noqa: S603 - fixture repository setup.
        [
            _GIT,
            "-c",
            "user.email=test@example.test",
            "-c",
            "user.name=Cloudfall Test",
            *arguments,
        ],
        capture_output=True,
        check=True,
        text=True,
    )
    return completed.stdout


def _fixture_inventory(tmp_path: Path) -> PlatformInventory:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git("-C", str(repository), "-c", "init.defaultBranch=main", "init", "--quiet")
    (repository / "app.py").write_text("print('crm')\n", encoding="utf-8")
    (repository / "pyproject.toml").write_text(
        '[project]\nname = "crm-backend"\nversion = "1.0.0"\n',
        encoding="utf-8",
    )
    _git("-C", str(repository), "add", ".")
    _git("-C", str(repository), "commit", "--quiet", "--message", "initial")

    state_directory = tmp_path / "state"
    shutil.copytree(EXAMPLES, state_directory)
    component_path = state_directory / "components" / "crm-backend.yaml"
    component = component_path.read_text(encoding="utf-8").replace(
        "https://github.com/example/crm-backend.git",
        f"file://{repository}",
    )
    component_path.write_text(component, encoding="utf-8")
    return PlatformInventory.from_state(validate_state(state_directory, SCHEMAS))


def test_builder_packages_a_hashed_release_with_metadata(
    tmp_path: Path,
) -> None:
    inventory = _fixture_inventory(tmp_path)
    output_directory = tmp_path / "artifacts"

    built = build_artifact(
        inventory, "crm-backend", "main", output_directory, SCHEMAS
    )

    assert built.archive_path.is_file()
    assert built.metadata_path.is_file()
    digest = hashlib.sha256(built.archive_path.read_bytes()).hexdigest()
    assert digest == built.archive_sha256

    metadata = json.loads(built.metadata_path.read_text(encoding="utf-8"))
    assert metadata["kind"] == "Artifact"
    assert metadata["spec"]["component"] == "crm-backend"
    assert metadata["spec"]["release"] == built.release
    assert metadata["spec"]["gitCommit"] == built.git_commit
    assert metadata["spec"]["archiveSha256"] == digest
    assert metadata["spec"]["sizeBytes"] == built.archive_path.stat().st_size

    with tarfile.open(built.archive_path) as archive:
        names = archive.getnames()
    assert "./app.py" in names
    assert "./pyproject.toml" in names
    assert all(".git" not in Path(name).parts for name in names)


def test_builder_rejects_an_unknown_component(tmp_path: Path) -> None:
    inventory = _fixture_inventory(tmp_path)

    with pytest.raises(ArtifactBuildError) as error:
        build_artifact(
            inventory, "billing-backend", "main", tmp_path / "artifacts", SCHEMAS
        )

    assert error.value.code == "artifact_component_missing"


def test_builder_rejects_an_unsafe_git_ref(tmp_path: Path) -> None:
    inventory = _fixture_inventory(tmp_path)

    with pytest.raises(ArtifactBuildError) as error:
        build_artifact(
            inventory,
            "crm-backend",
            "--upload-pack=/bin/false",
            tmp_path / "artifacts",
            SCHEMAS,
        )

    assert error.value.code == "artifact_ref_invalid"
