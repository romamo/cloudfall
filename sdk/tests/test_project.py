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
    CheckoutState,
    GitRevision,
    GitSetup,
    GitSourceUrl,
    InitOptions,
    ProjectDescription,
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


def _options(
    directory: Path, description: ProjectDescription | None = None
) -> InitOptions:
    return InitOptions(
        directory=directory,
        name=ProjectName.from_directory(directory),
        revision=GitRevision(REVISION),
        description=description,
    )


def _git(directory: Path, *arguments: str) -> str:
    return subprocess.run(  # noqa: S603 - fixed binary, test directory.
        ["git", "-C", str(directory), *arguments],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


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
    readme = (directory / "README.md").read_text(encoding="utf-8")
    assert "# acme-fleet" in readme
    assert "## About" in readme
    assert "--description" in readme
    assert "pyproject.toml" in scaffold.files
    assert "servers/.gitkeep" in scaffold.files


def test_init_links_the_guide_and_examples_at_the_pinned_revision(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "fleet"

    init_project(_options(directory))

    readme = (directory / "README.md").read_text(encoding="utf-8")
    base = "https://github.com/romamo/cloudfall"
    assert "[secrets guide][secrets-guide]" in readme
    assert "[reference set][examples]" in readme
    assert f"[cloudfall]: {base}\n" in readme
    assert f"[secrets-guide]: {base}/blob/{REVISION}/docs/secrets-guide.md\n" in readme
    assert f"[examples]: {base}/tree/{REVISION}/config/examples\n" in readme


def test_init_lays_out_the_secrets_setup(tmp_path: Path) -> None:
    directory = tmp_path / "fleet"

    scaffold = init_project(_options(directory))

    assert (directory / "secrets" / ".gitkeep").is_file()
    sops = (directory / ".sops.yaml").read_text(encoding="utf-8")
    assert "path_regex: secrets/.*\\.env$" in sops
    assert "age: age1" in sops
    assert ".sops.yaml" in scaffold.files
    assert "secrets/.gitkeep" in scaffold.files
    assert "secrets" not in (directory / ".gitignore").read_text(encoding="utf-8")


def test_init_writes_the_description_into_readme_and_pyproject(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "fleet"
    description = ProjectDescription("Two Hetzner hosts running the CRM for Acme")

    init_project(_options(directory, description))

    readme = (directory / "README.md").read_text(encoding="utf-8")
    pyproject = (directory / "pyproject.toml").read_text(encoding="utf-8")
    assert f"## About\n\n{description}\n" in readme
    assert "--description" not in readme
    assert f'description = "{description}"' in pyproject


def test_project_description_is_one_printable_line() -> None:
    assert str(ProjectDescription.from_boundary("  Acme fleet ")) == "Acme fleet"
    for bad in ("", "two\nlines", 'say "hi"', "back\\slash", "x" * 513):
        with pytest.raises(ValueError, match="project description"):
            ProjectDescription(bad)


def test_init_creates_a_git_repository(tmp_path: Path) -> None:
    directory = tmp_path / "fleet"

    scaffold = init_project(_options(directory))

    assert scaffold.git is GitSetup.INITIALIZED
    assert (directory / ".git").is_dir()
    assert _git(directory, "rev-parse", "--show-toplevel") == str(directory.resolve())
    assert "fresh git repository" in (directory / "README.md").read_text(
        encoding="utf-8"
    )


def test_init_leaves_an_enclosing_git_repository_alone(tmp_path: Path) -> None:
    _git(tmp_path, "init", "--quiet")
    directory = tmp_path / "fleet"

    scaffold = init_project(_options(directory))

    assert scaffold.git is GitSetup.ENCLOSED
    assert not (directory / ".git").exists()
    assert _git(directory, "rev-parse", "--show-toplevel") == str(tmp_path.resolve())
    assert "existing git repository" in (directory / "README.md").read_text(
        encoding="utf-8"
    )


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


def test_git_source_browses_over_https_whatever_the_transport() -> None:
    expected = "https://github.com/romamo/cloudfall"
    for source in (
        "https://github.com/romamo/cloudfall.git",
        "https://github.com/romamo/cloudfall",
        "git@github.com:romamo/cloudfall.git",
        "ssh://git@github.com/romamo/cloudfall.git",
    ):
        assert GitSourceUrl(source).browse_url == expected


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

    def inspect(checkout: Path) -> CheckoutState:
        seen.append(checkout)
        return CheckoutState(head=REVISION, clean=True, published=True)

    assert str(resolve_installed_revision(lambda: record, inspect)) == REVISION
    assert seen == [tmp_path]


def test_installed_revision_refuses_a_head_that_is_not_the_running_code(
    tmp_path: Path,
) -> None:
    record = json.dumps({"url": tmp_path.as_uri(), "dir_info": {"editable": True}})

    with pytest.raises(ProjectError) as error:
        resolve_installed_revision(
            lambda: record,
            lambda _: CheckoutState(head=REVISION, clean=False, published=True),
        )

    assert error.value.code == "project_revision_uncommitted"
    assert "--rev" in error.value.detail


def test_installed_revision_refuses_a_head_that_uv_could_not_fetch(
    tmp_path: Path,
) -> None:
    record = json.dumps({"url": tmp_path.as_uri(), "dir_info": {"editable": True}})

    with pytest.raises(ProjectError) as error:
        resolve_installed_revision(
            lambda: record,
            lambda _: CheckoutState(head=REVISION, clean=True, published=False),
        )

    assert error.value.code == "project_revision_unpublished"
    assert "push" in error.value.detail


def test_installed_revision_fails_without_an_origin() -> None:
    with pytest.raises(ProjectError) as error:
        resolve_installed_revision(lambda: None)

    assert error.value.code == "project_revision_unresolved"

    with pytest.raises(ProjectError) as error:
        resolve_installed_revision(lambda: json.dumps({"url": "file:///x"}))

    assert error.value.code == "project_revision_unresolved"


def test_cli_init_from_this_checkout_pins_its_head_only_when_fetchable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The pin must be a commit `uv sync` can fetch and that is the running code.

    A checkout mid-change (tracked files modified) or ahead of every remote
    is refused with the code that says why, so the outcome depends on the
    state of this repository when the test runs.
    """
    directory = tmp_path / "fleet"
    head = _git(ROOT, "rev-parse", "HEAD")
    clean = not _git(ROOT, "status", "--porcelain", "--untracked-files=no")
    published = bool(_git(ROOT, "branch", "--remotes", "--contains", head))

    exit_code = main(["init", str(directory)])

    captured = capsys.readouterr()
    if clean and published:
        payload = json.loads(captured.out)
        assert exit_code == 0
        assert payload["status"] == "ok"
        assert payload["project"]["name"] == "fleet"
        assert payload["project"]["revision"] == head
        assert payload["project"]["git"] == "initialized"
        assert payload["next"][-1] == "uv run cloudfall config validate"
    else:
        payload = json.loads(captured.err)
        assert exit_code == 2
        expected = (
            "project_revision_uncommitted"
            if not clean
            else "project_revision_unpublished"
        )
        assert payload["error"]["code"] == expected
        assert not directory.exists()


def test_cli_init_accepts_a_description(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = tmp_path / "fleet"

    exit_code = main(
        ["init", str(directory), "--rev", REVISION, "--description", "Acme CRM hosts"]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["project"]["git"] == "initialized"
    assert "Acme CRM hosts" in (directory / "README.md").read_text(encoding="utf-8")

    exit_code = main(
        ["init", str(tmp_path / "other"), "--rev", REVISION, "--description", " "]
    )

    payload = json.loads(capsys.readouterr().err)
    assert exit_code == 2
    assert payload["error"]["code"] == "invalid_argument"


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
