"""Project scaffolding acceptance tests."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from cloudfall.cli import main
from cloudfall.domain import ResourceKind
from cloudfall.project import (
    GitRevision,
    GitSourceUrl,
    InitOptions,
    ProjectError,
    ProjectName,
    init_project,
    project_context,
    resolve_installed_revision,
    resolve_project_directory,
)
from cloudfall.validation import ConfigValidationError, validate_config

ROOT = Path(__file__).parents[2]
SCHEMAS = ROOT / "config" / "schemas" / "v1"
EXAMPLES = ROOT / "config" / "examples"
REVISION = "5dea76ae7fdf4f99f5e8eb84b939dfa68b12ef3b"


def _options(directory: Path) -> InitOptions:
    return InitOptions(
        directory=directory,
        name=ProjectName.from_directory(directory),
        revision=GitRevision(REVISION),
    )


def test_init_lays_out_every_resource_directory_and_pins_cloudfall(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "Acme Fleet"

    scaffold = init_project(_options(directory))

    assert str(scaffold.name) == "acme-fleet"
    for kind in ResourceKind:
        assert (directory / kind.directory / ".gitkeep").is_file()
    pyproject = (directory / "pyproject.toml").read_text(encoding="utf-8")
    assert 'name = "acme-fleet"' in pyproject
    assert f'rev = "{REVISION}"' in pyproject
    assert 'git = "https://github.com/romamo/cloudfall.git"' in pyproject
    assert "tmp/" in (directory / ".gitignore").read_text(encoding="utf-8")
    assert "# acme-fleet" in (directory / "README.md").read_text(encoding="utf-8")
    assert "pyproject.toml" in scaffold.files
    assert "servers/.gitkeep" in scaffold.files


def test_init_refuses_a_non_empty_directory(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("keep me\n", encoding="utf-8")

    with pytest.raises(ProjectError) as error:
        init_project(_options(tmp_path))

    assert error.value.code == "project_directory_not_empty"
    assert not (tmp_path / "pyproject.toml").exists()


def test_init_refuses_a_file_path(tmp_path: Path) -> None:
    target = tmp_path / "project"
    target.write_text("", encoding="utf-8")

    with pytest.raises(ProjectError) as error:
        init_project(_options(target))

    assert error.value.code == "project_directory_not_a_directory"


def test_project_name_is_strict() -> None:
    assert str(ProjectName.from_boundary(" My-Fleet ")) == "my-fleet"
    with pytest.raises(ValueError, match="project name"):
        ProjectName("-leading-dash")
    with pytest.raises(ValueError, match="project name"):
        ProjectName("has space")


def test_git_revision_requires_a_full_hash() -> None:
    assert str(GitRevision.from_boundary(REVISION.upper())) == REVISION
    with pytest.raises(ValueError, match="40-hex"):
        GitRevision("5dea76a")


def test_git_source_accepts_https_and_ssh_only() -> None:
    GitSourceUrl("https://github.com/romamo/cloudfall.git")
    GitSourceUrl("git@github.com:romamo/cloudfall.git")
    GitSourceUrl("ssh://git@github.com/romamo/cloudfall.git")
    with pytest.raises(ValueError, match="git source"):
        GitSourceUrl("/local/checkout")


def test_installed_revision_comes_from_a_git_install() -> None:
    record = json.dumps(
        {
            "url": "https://github.com/romamo/cloudfall.git",
            "vcs_info": {"vcs": "git", "commit_id": REVISION},
        }
    )

    assert str(resolve_installed_revision(lambda: record)) == REVISION


def test_installed_revision_reads_head_of_a_source_checkout(tmp_path: Path) -> None:
    record = json.dumps({"url": tmp_path.as_uri(), "dir_info": {"editable": True}})
    seen: list[Path] = []

    def run_git(checkout: Path) -> str:
        seen.append(checkout)
        return REVISION

    assert str(resolve_installed_revision(lambda: record, run_git)) == REVISION
    assert seen == [tmp_path]


def test_installed_revision_fails_without_an_origin() -> None:
    with pytest.raises(ProjectError) as error:
        resolve_installed_revision(lambda: None)

    assert error.value.code == "project_revision_unresolved"

    with pytest.raises(ProjectError) as error:
        resolve_installed_revision(lambda: json.dumps({"url": "file:///x"}))

    assert error.value.code == "project_revision_unresolved"


def test_cli_init_from_this_checkout_pins_its_head(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = tmp_path / "fleet"
    head = subprocess.run(  # noqa: S603 - fixed binary, repository path.
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    exit_code = main(["init", str(directory)])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["project"]["name"] == "fleet"
    assert payload["project"]["revision"] == head
    assert payload["next"][-1] == "uv run cloudfall config validate ."


def test_cli_init_reports_errors_as_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["init", str(tmp_path), "--rev", "abc"])

    payload = json.loads(capsys.readouterr().err)
    assert exit_code == 2
    assert payload["error"]["code"] == "invalid_argument"

    (tmp_path / "x").write_text("", encoding="utf-8")
    exit_code = main(["init", str(tmp_path), "--rev", REVISION])

    payload = json.loads(capsys.readouterr().err)
    assert exit_code == 2
    assert payload["error"]["code"] == "project_directory_not_empty"


def test_fresh_project_validates_as_empty(tmp_path: Path) -> None:
    directory = tmp_path / "fleet"
    init_project(_options(directory))

    with pytest.raises(ConfigValidationError) as error:
        validate_config(directory, SCHEMAS)

    assert error.value.issue.code == "project_empty"


def test_validation_rejects_a_resource_in_the_wrong_directory(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    shutil.copytree(EXAMPLES, project)
    misplaced = project / "servers" / "crm.yaml"
    (project / "applications" / "crm.yaml").rename(misplaced)

    with pytest.raises(ConfigValidationError) as error:
        validate_config(project, SCHEMAS)

    assert error.value.issue.code == "resource_misplaced"
    assert "applications/" in error.value.issue.message


def test_validation_reads_only_kind_directories(tmp_path: Path) -> None:
    project = tmp_path / "project"
    shutil.copytree(EXAMPLES, project)
    (project / "tmp" / "import" / "config").mkdir(parents=True)
    (project / "tmp" / "import" / "config" / "junk.yaml").write_text(
        "kind: Nonsense\n", encoding="utf-8"
    )
    (project / ".venv" / "lib").mkdir(parents=True)
    (project / ".venv" / "lib" / "vendored.yml").write_text("- 1\n", encoding="utf-8")
    (project / ".sops.yaml").write_text("creation_rules: []\n", encoding="utf-8")
    (project / "playbooks").mkdir()
    (project / "playbooks" / "proxy.yml").write_text("- hosts: all\n", encoding="utf-8")
    (project / "Taskfile.yml").write_text("version: '3'\n", encoding="utf-8")

    state = validate_config(project, SCHEMAS)

    assert state.resource_count == validate_config(EXAMPLES, SCHEMAS).resource_count


def test_missing_project_directory_is_reported(tmp_path: Path) -> None:
    with pytest.raises(ConfigValidationError) as error:
        validate_config(tmp_path / "absent", SCHEMAS)

    assert error.value.issue.code == "project_directory_missing"


def test_project_directory_resolution_order(tmp_path: Path) -> None:
    explicit = tmp_path / "explicit"
    from_environment = tmp_path / "from-env"
    current = tmp_path / "current"
    for directory in (explicit, from_environment):
        directory.mkdir()
    shutil.copytree(EXAMPLES, current)
    environment = {"CLOUDFALL_PROJECT": str(from_environment)}

    assert resolve_project_directory(explicit, environment, current) == explicit
    assert resolve_project_directory(None, environment, current) == from_environment
    assert resolve_project_directory(None, {}, current) == current


def test_project_directory_resolution_fails_outside_a_project(
    tmp_path: Path,
) -> None:
    with pytest.raises(ProjectError) as error:
        resolve_project_directory(None, {}, tmp_path)

    assert error.value.code == "project_directory_unresolved"
    assert "--project" in error.value.detail

    with pytest.raises(ProjectError) as error:
        resolve_project_directory(tmp_path / "absent", {}, tmp_path)

    assert error.value.code == "project_directory_missing"


def test_project_context_runs_inside_the_project_and_restores(tmp_path: Path) -> None:
    project = tmp_path / "project"
    shutil.copytree(EXAMPLES, project)
    before = Path.cwd()

    with project_context(project, {}) as directory:
        assert directory == project.resolve()
        assert Path.cwd() == project.resolve()

    assert Path.cwd() == before


def test_cli_finds_the_project_in_the_current_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "project"
    shutil.copytree(EXAMPLES, project)
    before = Path.cwd()
    os.chdir(project)
    try:
        exit_code = main(["config", "validate", "--schemas", str(SCHEMAS)])
    finally:
        os.chdir(before)

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["resources"] == 12


def test_cli_reports_a_missing_project_as_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["audit", "--project", str(tmp_path / "nope"), "--observed", "x"])

    payload = json.loads(capsys.readouterr().err)
    assert exit_code == 2
    assert payload["error"]["code"] == "project_directory_missing"
