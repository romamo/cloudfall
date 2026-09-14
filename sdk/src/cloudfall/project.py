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
from enum import Enum
from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from cloudfall.domain import ResourceKind

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping

_GIT_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_PROJECT_NAME_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?$")
_SCP_LIKE_SOURCE_PATTERN = re.compile(
    r"^(?P<user>[\w.-]+)@(?P<host>[\w.-]+):(?P<path>[\w./-]+)$"
)
_PROJECT_NAME_MAX_LENGTH = 64
_PROJECT_DESCRIPTION_MAX_LENGTH = 512
_GIT_TIMEOUT_SECONDS = 30
_GIT_NOT_A_REPOSITORY_EXIT_CODE = 128

DEFAULT_SOURCE_URL = "https://github.com/romamo/cloudfall.git"

SECRETS_DIRECTORY = "secrets"
"""Project directory holding sops-encrypted secret fragments."""

SOPS_CONFIGURATION_FILE = ".sops.yaml"
"""Project file telling sops which key encrypts the secret fragments."""

SECRETS_GUIDE_PATH = "docs/secrets-guide.md"
"""Path of the secrets guide inside the Cloudfall repository."""

EXAMPLES_PATH = "config/examples"
"""Path of the reference resource set inside the Cloudfall repository."""

_ERROR_NAME_UNDERIVED = "project_name_underived"
_ERROR_DIRECTORY_MISSING = "project_directory_missing"
_ERROR_DIRECTORY_UNRESOLVED = "project_directory_unresolved"
_ERROR_REVISION_UNRESOLVED = "project_revision_unresolved"
_ERROR_REVISION_UNCOMMITTED = "project_revision_uncommitted"
_ERROR_REVISION_UNPUBLISHED = "project_revision_unpublished"
_ERROR_NOT_A_DIRECTORY = "project_directory_not_a_directory"
_ERROR_NOT_EMPTY = "project_directory_not_empty"
_ERROR_GIT_UNAVAILABLE = "project_git_unavailable"
_ERROR_GIT_FAILED = "project_git_failed"


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
        scp_like = _SCP_LIKE_SOURCE_PATTERN.fullmatch(self.value) is not None
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

    @property
    def browse_url(self) -> str:
        """Return the https location of the repository for links to its files.

        ``https://host/owner/repo.git``, ``ssh://git@host/owner/repo.git``, and
        ``git@host:owner/repo.git`` all browse at ``https://host/owner/repo``.
        """
        scp_like = _SCP_LIKE_SOURCE_PATTERN.fullmatch(self.value)
        if scp_like is not None:
            host, path = scp_like.group("host"), scp_like.group("path")
        else:
            parsed = urlparse(self.value)
            host, path = parsed.hostname or "", parsed.path
        path = path.strip("/").removesuffix(".git")
        return f"https://{host}/{path}"

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
class ProjectDescription:
    """One line saying what a project manages, for its README and metadata."""

    value: str

    def __post_init__(self) -> None:
        """Require one trimmed, printable line that TOML can hold unescaped."""
        if not self.value or self.value != self.value.strip():
            message = f"project description must be a trimmed line: {self.value!r}"
            raise ValueError(message)
        if not self.value.isprintable() or any(c in self.value for c in '"\\'):
            message = (
                "project description must be one printable line without "
                f"quotes or backslashes: {self.value!r}"
            )
            raise ValueError(message)
        if len(self.value) > _PROJECT_DESCRIPTION_MAX_LENGTH:
            message = (
                "project description exceeds "
                f"{_PROJECT_DESCRIPTION_MAX_LENGTH} characters"
            )
            raise ValueError(message)

    @classmethod
    def from_boundary(cls, value: object) -> ProjectDescription:
        """Coerce a boundary value while keeping internal APIs strictly typed."""
        if not isinstance(value, str):
            message = (
                f"project description must be a string, got {type(value).__name__}"
            )
            raise TypeError(message)
        return cls(value.strip())

    def __str__(self) -> str:
        """Return the serialized description."""
        return self.value


@dataclass(frozen=True, slots=True)
class InitOptions:
    """Everything ``cloudfall init`` needs to lay out one project."""

    directory: Path
    name: ProjectName
    revision: GitRevision
    source: GitSourceUrl = GitSourceUrl(DEFAULT_SOURCE_URL)
    description: ProjectDescription | None = None


class GitSetup(Enum):
    """What ``cloudfall init`` did about version control for the project."""

    INITIALIZED = "initialized"
    """A new repository was created in the project directory."""

    ENCLOSED = "enclosed"
    """The directory already lies inside a repository, which was left alone."""


@dataclass(frozen=True, slots=True)
class ProjectScaffold:
    """Files written by ``cloudfall init``, for the CLI and agent envelope."""

    directory: Path
    name: ProjectName
    revision: GitRevision
    source: GitSourceUrl
    files: tuple[str, ...]
    git: GitSetup

    def as_dict(self) -> dict[str, object]:
        """Serialize the scaffold result for system boundaries."""
        return {
            "status": "ok",
            "project": {
                "directory": str(self.directory),
                "name": str(self.name),
                "revision": str(self.revision),
                "source": str(self.source),
                "git": self.git.value,
            },
            "files": list(self.files),
            "next": [
                f"cd {self.directory} && uv sync",
                "uv run cloudfall add ssh-key ~/.ssh/id_ed25519.pub --owner <you>",
                "uv run cloudfall add server h1 --address <ip-or-hostname>",
                "uv run cloudfall config validate",
            ],
        }


@dataclass(frozen=True, slots=True)
class CheckoutState:
    """What a source checkout says about the commit it is running."""

    head: str
    clean: bool
    """No tracked file differs from ``HEAD``, so ``HEAD`` is the running code."""
    published: bool
    """``HEAD`` is reachable from a remote-tracking branch, so it can be fetched."""


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


def project_path(value: str) -> Path:
    """Return a runtime path, refusing a relative path that leaves the project.

    Relative paths resolve against the project (see ``project_context``), so
    one whose normalized form climbs above it (``../../x``, ``tmp/../../x``)
    would read or write outside the project without saying so. Locations
    outside the project stay possible, but only as an explicit absolute path.
    """
    path = Path(value)
    if not path.is_absolute() and Path(os.path.normpath(value)).parts[:1] == (
        "..",
    ):
        message = (
            f"relative path {value!r} leaves the project directory; "
            "pass an absolute path to use a location outside the project"
        )
        raise ValueError(message)
    return path


def resolve_installed_revision(
    read_direct_url: Callable[[], str | None] | None = None,
    inspect_checkout: Callable[[Path], CheckoutState] | None = None,
) -> GitRevision:
    """Return the commit the running Cloudfall was installed from.

    A wheel installed from git records its commit in ``direct_url.json``; an
    editable source checkout records the checkout location, whose ``HEAD`` is
    the commit, provided the checkout is clean (else ``HEAD`` is not the code
    that is running) and ``HEAD`` is on a remote branch (else ``uv sync``
    cannot fetch it). Anything else cannot be pinned and must be given
    explicitly.
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
        inspect = inspect_checkout or _inspect_checkout
        return _pinnable_head(checkout, inspect(checkout))
    message = (
        "the installed cloudfall distribution is neither a git install nor a "
        f"source checkout ({url!r}); pass --rev with the commit to pin"
    )
    raise ProjectError(_ERROR_REVISION_UNRESOLVED, message)


def _pinnable_head(checkout: Path, state: CheckoutState) -> GitRevision:
    head = GitRevision.from_boundary(state.head)
    if not state.clean:
        message = (
            f"the source checkout {checkout} has uncommitted changes, so HEAD "
            f"{head} is not the code that is running; commit them or pass --rev"
        )
        raise ProjectError(_ERROR_REVISION_UNCOMMITTED, message)
    if not state.published:
        message = (
            f"HEAD {head} of the source checkout {checkout} is on no remote "
            "branch, so `uv sync` could not fetch it; push it or pass --rev"
        )
        raise ProjectError(_ERROR_REVISION_UNPUBLISHED, message)
    return head


def _installed_direct_url() -> str | None:
    return metadata.distribution("cloudfall").read_text("direct_url.json")


def _git_binary() -> str:
    git = shutil.which("git")
    if git is None:
        message = "git is required but is not installed"
        raise ProjectError(_ERROR_GIT_UNAVAILABLE, message)
    return git


def _run_git(directory: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(  # noqa: S603 - fixed binary, fixed arguments.
            [_git_binary(), "-C", str(directory), *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        message = f"git {arguments[0]} in {directory} timed out: {error}"
        raise ProjectError(_ERROR_GIT_FAILED, message) from error


def _git_or_fail(directory: Path, *arguments: str) -> str:
    completed = _run_git(directory, *arguments)
    if completed.returncode != 0:
        message = (
            f"git {arguments[0]} in {directory} failed with exit code "
            f"{completed.returncode}: {completed.stderr.strip()}"
        )
        raise ProjectError(_ERROR_GIT_FAILED, message)
    return completed.stdout


def _inspect_checkout(checkout: Path) -> CheckoutState:
    head = _git_or_fail(checkout, "rev-parse", "HEAD").strip()
    changes = _git_or_fail(checkout, "status", "--porcelain", "--untracked-files=no")
    remote_branches = _git_or_fail(checkout, "branch", "--remotes", "--contains", head)
    return CheckoutState(
        head=head, clean=not changes.strip(), published=bool(remote_branches.strip())
    )


def _initialize_git(directory: Path) -> GitSetup:
    """Create a repository in the directory unless one already encloses it."""
    enclosing = _run_git(directory, "rev-parse", "--is-inside-work-tree")
    if enclosing.returncode == 0 and enclosing.stdout.strip() == "true":
        return GitSetup.ENCLOSED
    if enclosing.returncode != _GIT_NOT_A_REPOSITORY_EXIT_CODE:
        message = (
            f"git rev-parse in {directory} failed with exit code "
            f"{enclosing.returncode}: {enclosing.stderr.strip()}"
        )
        raise ProjectError(_ERROR_GIT_FAILED, message)
    _git_or_fail(directory, "init", "--quiet")
    return GitSetup.INITIALIZED


def init_project(
    options: InitOptions,
    initialize_git: Callable[[Path], GitSetup] | None = None,
) -> ProjectScaffold:
    """Lay out a new project in an empty or absent directory."""
    directory = options.directory
    if directory.exists() and not directory.is_dir():
        message = f"project path exists and is not a directory: {directory}"
        raise ProjectError(_ERROR_NOT_A_DIRECTORY, message)
    if directory.is_dir() and any(directory.iterdir()):
        message = f"project directory is not empty: {directory}"
        raise ProjectError(_ERROR_NOT_EMPTY, message)
    directory.mkdir(parents=True, exist_ok=True)
    git = (initialize_git or _initialize_git)(directory)

    files: dict[str, str] = {
        "pyproject.toml": _pyproject(options),
        ".gitignore": _GITIGNORE,
        SOPS_CONFIGURATION_FILE: _SOPS_CONFIGURATION,
        "README.md": _readme(options, git),
    }
    for kind in ResourceKind:
        files[f"{kind.directory}/.gitkeep"] = ""
    files[f"{SECRETS_DIRECTORY}/.gitkeep"] = ""
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
        git=git,
    )


def _pyproject(options: InitOptions) -> str:
    description = (
        str(options.description)
        if options.description is not None
        else "Cloudfall project: fleet, applications, and operating declarations"
    )
    return f"""[project]
name = "{options.name}"
version = "0"
description = "{description}"
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


_SOPS_CONFIGURATION = f"""\
# sops encrypts every file matching a rule below with the listed age keys,
# so fragments under {SECRETS_DIRECTORY}/ are safe to commit. One-time setup:
#
#   age-keygen -o ~/.config/cloudfall/age.key
#   export SOPS_AGE_KEY_FILE=~/.config/cloudfall/age.key
#
# Replace the `age` value with the public key (age1...) that age-keygen
# printed, then write fragments with `sops {SECRETS_DIRECTORY}/production/<app>.env`.
creation_rules:
  - path_regex: {SECRETS_DIRECTORY}/.*\\.env$
    age: age1replace-with-your-public-key
"""


def _readme(options: InitOptions, git: GitSetup) -> str:
    tmp = RUNTIME_DIRECTORY
    secrets = SECRETS_DIRECTORY
    browse = options.source.browse_url
    revision = options.revision
    secrets_guide = f"{browse}/blob/{revision}/{SECRETS_GUIDE_PATH}"
    examples = f"{browse}/tree/{revision}/{EXAMPLES_PATH}"
    about = (
        str(options.description)
        if options.description is not None
        else (
            "Say what this project manages: which servers, which applications "
            "run on them, and who operates them. Pass `--description` to "
            "`cloudfall init` to fill this in at creation time."
        )
    )
    git_note = (
        "This directory is a fresh git repository: add a private remote and\n"
        "make the first commit, then install Cloudfall and validate:"
        if git is GitSetup.INITIALIZED
        else "This directory lies inside an existing git repository, so commit\n"
        "it there. Install Cloudfall and validate:"
    )
    rows = "\n".join(
        f"| `{kind.directory}/` | `{kind.value}` |" for kind in ResourceKind
    )
    return f"""# {options.name}

A [Cloudfall][cloudfall] project: the fleet, the applications that run on
it, and the declarations that operate them, in one private repository. Every
`cloudfall` command run from inside this directory finds it on its own; from
elsewhere, pass `--project` or set `CLOUDFALL_PROJECT`.

## About

{about}

## Layout

| Directory | Resource kind |
|---|---|
{rows}

Only these directories are read as resources, so playbooks, roles, docs, and
tooling files may live anywhere else in the project. `{tmp}/` holds evidence,
receipts, rendered inventory, built artifacts, and rendered environment files
and is ignored by git. Secret values never enter the resources: declare
`secretRefs` and keep sops-encrypted fragments in `{secrets}/`, which
`.sops.yaml` encrypts to your age key once you put the key there (see the
[secrets guide][secrets-guide]).

## Start

{git_note}

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

Edit the written files freely; the [reference set][examples] in the
Cloudfall repository shows every kind. Then bring the host under management
and prove it:

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
`uv sync` to move the project to a newer release. The links below point at
the pinned commit.

[cloudfall]: {browse}
[secrets-guide]: {secrets_guide}
[examples]: {examples}
"""
