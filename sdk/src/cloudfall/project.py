"""Create Cloudfall projects.

A project is one directory holding everything Cloudfall needs to run one
user's stack: the fleet (servers, server types, SSH public keys), the
applications (applications, components, services, domains), the operating
declarations (alert rules, operator policies, logging stacks), and the
``pyproject.toml`` that pins the Cloudfall revision the project runs with.
Every ``cloudfall`` command runs inside a project: the current directory
when it is one, else ``--project``, else ``CLOUDFALL_PROJECT``.
Evidence, receipts, and rendered files land under ``tmp/`` inside the
project and are never committed.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from cloudfall.domain import ResourceKind

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping

_GIT_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_PROJECT_NAME_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?$")
_PROJECT_NAME_MAX_LENGTH = 64
_GIT_TIMEOUT_SECONDS = 30

DEFAULT_SOURCE_URL = "https://github.com/romamo/cloudfall.git"

_ERROR_NAME_UNDERIVED = "project_name_underived"
_ERROR_DIRECTORY_MISSING = "project_directory_missing"
_ERROR_DIRECTORY_UNRESOLVED = "project_directory_unresolved"
_ERROR_REVISION_UNRESOLVED = "project_revision_unresolved"
_ERROR_NOT_A_DIRECTORY = "project_directory_not_a_directory"
_ERROR_NOT_EMPTY = "project_directory_not_empty"


RUNTIME_DIRECTORY = "tmp"
"""Project directory holding evidence and receipts; never a resource source."""

PROJECT_DIRECTORY_VARIABLE = "CLOUDFALL_PROJECT"
"""Environment variable naming the project when ``--project`` is absent."""


class ProjectError(RuntimeError):
    """Fail-fast project error with a stable machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        """Record the failure code and human-readable detail."""
        self.code = code
        self.detail = message
        super().__init__(f"{code}: {message}")

    def as_dict(self) -> dict[str, object]:
        """Serialize the error envelope for system boundaries."""
        return {
            "status": "error",
            "error": {"code": self.code, "message": self.detail},
        }


@dataclass(frozen=True, slots=True)
class GitRevision:
    """Full 40-character commit hash a project pins Cloudfall to."""

    value: str

    def __post_init__(self) -> None:
        """Enforce the full-hash invariant at construction time."""
        if not _GIT_REVISION_PATTERN.fullmatch(self.value):
            message = f"git revision must be a full 40-hex commit hash: {self.value!r}"
            raise ValueError(message)

    @classmethod
    def from_boundary(cls, value: object) -> GitRevision:
        """Coerce a boundary value while keeping internal APIs strictly typed."""
        if not isinstance(value, str):
            message = f"git revision must be a string, got {type(value).__name__}"
            raise TypeError(message)
        return cls(value.strip().lower())

    def __str__(self) -> str:
        """Return the serialized hash."""
        return self.value


@dataclass(frozen=True, slots=True)
class GitSourceUrl:
    """Location of the Cloudfall repository a project installs from."""

    value: str

    def __post_init__(self) -> None:
        """Accept https and ssh git locations only."""
        parsed = urlparse(self.value)
        https = parsed.scheme == "https" and bool(parsed.netloc)
        ssh = parsed.scheme == "ssh" and bool(parsed.netloc)
        scp_like = re.fullmatch(r"[\w.-]+@[\w.-]+:[\w./-]+", self.value) is not None
        if not (https or ssh or scp_like):
            message = f"git source must be an https or ssh location: {self.value!r}"
            raise ValueError(message)

    @classmethod
    def from_boundary(cls, value: object) -> GitSourceUrl:
        """Coerce a boundary value while keeping internal APIs strictly typed."""
        if not isinstance(value, str):
            message = f"git source must be a string, got {type(value).__name__}"
            raise TypeError(message)
        return cls(value.strip())

    def __str__(self) -> str:
        """Return the serialized location."""
        return self.value


@dataclass(frozen=True, slots=True)
class ProjectName:
    """Package name of a project, as written into its ``pyproject.toml``."""

    value: str

    def __post_init__(self) -> None:
        """Enforce a normalized PEP 503 style name."""
        if not _PROJECT_NAME_PATTERN.fullmatch(self.value):
            message = (
                "project name must be lowercase letters, digits, '.', '_' or '-' "
                f"and start and end with a letter or digit: {self.value!r}"
            )
            raise ValueError(message)
        if len(self.value) > _PROJECT_NAME_MAX_LENGTH:
            message = f"project name exceeds 64 characters: {self.value!r}"
            raise ValueError(message)

    @classmethod
    def from_boundary(cls, value: object) -> ProjectName:
        """Coerce a boundary value while keeping internal APIs strictly typed."""
        if not isinstance(value, str):
            message = f"project name must be a string, got {type(value).__name__}"
            raise TypeError(message)
        return cls(value.strip().lower())

    @classmethod
    def from_directory(cls, directory: Path) -> ProjectName:
        """Derive the name from the directory, lowercased and dash-joined."""
        raw = directory.resolve().name
        normalized = re.sub(r"[^a-z0-9._-]+", "-", raw.lower()).strip("-._")
        if not normalized:
            message = f"cannot derive a project name from directory {directory}"
            raise ProjectError(_ERROR_NAME_UNDERIVED, message)
        return cls(normalized)

    def __str__(self) -> str:
        """Return the serialized name."""
        return self.value


@dataclass(frozen=True, slots=True)
class InitOptions:
    """Everything ``cloudfall init`` needs to lay out one project."""

    directory: Path
    name: ProjectName
    revision: GitRevision
    source: GitSourceUrl = GitSourceUrl(DEFAULT_SOURCE_URL)


@dataclass(frozen=True, slots=True)
class ProjectScaffold:
    """Files written by ``cloudfall init``, for the CLI and agent envelope."""

    directory: Path
    name: ProjectName
    revision: GitRevision
    source: GitSourceUrl
    files: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        """Serialize the scaffold result for system boundaries."""
        return {
            "status": "ok",
            "project": {
                "directory": str(self.directory),
                "name": str(self.name),
                "revision": str(self.revision),
                "source": str(self.source),
            },
            "files": list(self.files),
            "next": [
                f"cd {self.directory} && uv sync",
                "uv run cloudfall add ssh-key ~/.ssh/id_ed25519.pub --owner <you>",
                "uv run cloudfall add server h1 --address <ip-or-hostname>",
                "uv run cloudfall config validate .",
            ],
        }


def is_project(directory: Path) -> bool:
    """Return whether a directory holds at least one resource kind directory."""
    return directory.is_dir() and any(
        (directory / kind.directory).is_dir() for kind in ResourceKind
    )


def resolve_project_directory(
    explicit: Path | None, environment: Mapping[str, str], current: Path
) -> Path:
    """Pick the project directory a command runs in.

    Precedence: ``--project``, then ``CLOUDFALL_PROJECT``, then the
    current directory when it is a project. An explicitly named directory
    must exist; whether it holds resources is validation's concern.
    """
    if explicit is not None:
        candidate, origin = explicit, "--project"
    elif environment.get(PROJECT_DIRECTORY_VARIABLE):
        candidate = Path(environment[PROJECT_DIRECTORY_VARIABLE])
        origin = PROJECT_DIRECTORY_VARIABLE
    elif is_project(current):
        return current.resolve()
    else:
        message = (
            f"{current} is not a project: it has no resource directory such as "
            f"{ResourceKind.SERVER.directory}/. Run from inside a project, pass "
            f"--project, or set {PROJECT_DIRECTORY_VARIABLE}"
        )
        raise ProjectError(_ERROR_DIRECTORY_UNRESOLVED, message)
    if not candidate.is_dir():
        message = f"project directory from {origin} does not exist: {candidate}"
        raise ProjectError(_ERROR_DIRECTORY_MISSING, message)
    return candidate.resolve()


@contextmanager
def project_context(
    explicit: Path | None, environment: Mapping[str, str]
) -> Iterator[Path]:
    """Run the body as if started inside the resolved project directory.

    Relative paths, including every ``tmp/`` default, then land in the
    project, whatever directory the command was launched from. The previous
    working directory is restored afterwards.
    """
    previous = Path.cwd()
    directory = resolve_project_directory(explicit, environment, previous)
    os.chdir(directory)
    try:
        yield directory
    finally:
        os.chdir(previous)


def resolve_installed_revision(
    read_direct_url: Callable[[], str | None] | None = None,
    run_git: Callable[[Path], str] | None = None,
) -> GitRevision:
    """Return the commit the running Cloudfall was installed from.

    A wheel installed from git records its commit in ``direct_url.json``; an
    editable source checkout records the checkout location, whose ``HEAD`` is
    the commit. Anything else cannot be pinned and must be given explicitly.
    """
    direct_url = (read_direct_url or _installed_direct_url)()
    if direct_url is None:
        message = (
            "the installed cloudfall distribution records no origin; "
            "pass --rev with the commit to pin"
        )
        raise ProjectError(_ERROR_REVISION_UNRESOLVED, message)
    record = json.loads(direct_url)
    if not isinstance(record, dict):
        message = f"direct_url.json is not an object: {direct_url!r}"
        raise ProjectError(_ERROR_REVISION_UNRESOLVED, message)
    vcs_info = record.get("vcs_info")
    if isinstance(vcs_info, dict) and "commit_id" in vcs_info:
        return GitRevision.from_boundary(vcs_info["commit_id"])
    dir_info = record.get("dir_info")
    url = record.get("url")
    if (
        isinstance(dir_info, dict)
        and isinstance(url, str)
        and url.startswith("file://")
    ):
        checkout = Path(urlparse(url).path)
        return GitRevision.from_boundary((run_git or _git_head)(checkout))
    message = (
        "the installed cloudfall distribution is neither a git install nor a "
        f"source checkout ({url!r}); pass --rev with the commit to pin"
    )
    raise ProjectError(_ERROR_REVISION_UNRESOLVED, message)


def _installed_direct_url() -> str | None:
    return metadata.distribution("cloudfall").read_text("direct_url.json")


def _git_head(checkout: Path) -> str:
    git = shutil.which("git")
    if git is None:
        message = (
            f"git is required to read HEAD of the source checkout {checkout}; "
            "pass --rev with the commit to pin"
        )
        raise ProjectError(_ERROR_REVISION_UNRESOLVED, message)
    try:
        completed = subprocess.run(  # noqa: S603 - fixed binary, checkout path.
            [git, "-C", str(checkout), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        message = f"reading HEAD of the source checkout {checkout} failed: {error}"
        raise ProjectError(_ERROR_REVISION_UNRESOLVED, message) from error
    return completed.stdout.strip()


def init_project(options: InitOptions) -> ProjectScaffold:
    """Lay out a new project in an empty or absent directory."""
    directory = options.directory
    if directory.exists() and not directory.is_dir():
        message = f"project path exists and is not a directory: {directory}"
        raise ProjectError(_ERROR_NOT_A_DIRECTORY, message)
    if directory.is_dir() and any(directory.iterdir()):
        message = f"project directory is not empty: {directory}"
        raise ProjectError(_ERROR_NOT_EMPTY, message)
    directory.mkdir(parents=True, exist_ok=True)

    files: dict[str, str] = {
        "pyproject.toml": _pyproject(options),
        ".gitignore": _GITIGNORE,
        "README.md": _readme(options),
    }
    for kind in ResourceKind:
        files[f"{kind.directory}/.gitkeep"] = ""
    for relative, content in files.items():
        path = directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return ProjectScaffold(
        directory=directory,
        name=options.name,
        revision=options.revision,
        source=options.source,
        files=tuple(files),
    )


def _pyproject(options: InitOptions) -> str:
    return f"""[project]
name = "{options.name}"
version = "0"
description = "Cloudfall project: fleet, applications, and operating declarations"
requires-python = ">=3.14"
dependencies = ["cloudfall"]

[tool.uv]
package = false

# Cloudfall is an installed package: the schema catalog and the Ansible engine
# ship inside the wheel. Bump `rev` to move this project to a newer Cloudfall
# commit, then run `uv sync`. Add the `mcp` extra (`cloudfall[mcp]`) to run
# the `cloudfall-mcp` agent server.
[tool.uv.sources]
cloudfall = {{ git = "{options.source}", rev = "{options.revision}" }}
"""


_GITIGNORE = f"""# Runtime state: evidence, receipts, rendered inventory, built
# artifacts, rendered environment files, and the persisted migration plan
{RUNTIME_DIRECTORY}/

# Local environment and tooling
.venv/
__pycache__/
"""


def _readme(options: InitOptions) -> str:
    tmp = RUNTIME_DIRECTORY
    rows = "\n".join(
        f"| `{kind.directory}/` | `{kind.value}` |" for kind in ResourceKind
    )
    return f"""# {options.name}

A [Cloudfall](https://github.com/romamo/cloudfall) project: the fleet, the
applications that run on it, and the declarations that operate them, in one
private repository. Every `cloudfall` command run from inside this directory
finds it on its own; from elsewhere, pass `--project` or set
`CLOUDFALL_PROJECT`.

## Layout

| Directory | Resource kind |
|---|---|
{rows}

Only these directories are read as resources, so playbooks, roles, docs, and
tooling files may live anywhere else in the project. `{tmp}/` holds evidence,
receipts, rendered inventory, built artifacts, and rendered environment files
and is ignored by git. Secret values never enter the project: declare
`secretRefs` and keep encrypted secrets in `secrets/` (see the secrets guide).

## Start

```console
uv sync
uv run cloudfall config validate
```

Validation fails until the project declares its first resources. Declare
your SSH key and your first server; the server's type is created from the
bundled Debian 13 baseline when it does not exist yet:

```console
uv run cloudfall add ssh-key ~/.ssh/id_ed25519.pub --owner <you>
uv run cloudfall add server h1 --address <ip-or-hostname>
uv run cloudfall config validate
```

Edit the written files freely; the reference set in the Cloudfall repository
under `config/examples/` shows every kind. Then bring the host under
management and prove it:

```console
uv run cloudfall-engine inventory render --output {tmp}/ansible-inventory.json
uv run cloudfall-engine playbook run inspect --inventory {tmp}/ansible-inventory.json \\
  --extra-vars cloudfall_inspect_output_directory=$PWD/{tmp}/observed
uv run cloudfall audit --observed {tmp}/observed
```

Add applications by hand or with `cloudfall import render`, then drive the
whole migration with the resumable plan:

```console
uv run cloudfall migrate --build <component>=main
uv run cloudfall migrate --build <component>=main --yes
```

Cloudfall is pinned to one commit in `pyproject.toml`; bump `rev` and run
`uv sync` to move the project to a newer release.
"""
