"""Catalog of every command an agent may run in a project, by effect.

The catalog is the one place that says which commands change servers and
which do not. ``cloudfall init`` renders it into the project's ``AGENTS.md``
so an agent reads the same classification the CLI enforces, and the tests
check it against the argparse trees, so a command added to a parser
without a catalog entry fails the suite instead of going unclassified.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class CommandEffect(Enum):
    """The furthest a command's side effects reach."""

    READ = "read"
    """Reads the project, evidence under ``tmp/``, or servers; writes nothing
    but evidence and reports under ``tmp/``."""

    PROJECT = "project"
    """Writes files into the project on the controller; servers untouched."""

    SERVERS = "servers"
    """Changes servers, always through the engine, always with a receipt."""


class Stability(Enum):
    """How long a top-level output key is promised to keep its name and meaning."""

    STABLE = "stable"
    """Removed or changed only in a MAJOR schema release, after at least one
    MINOR release in which documents holding it warn ``FIELD_DEPRECATED``."""

    EXPERIMENTAL = "experimental"
    """Not yet released; may change in any release without a warning."""


@dataclass(frozen=True, slots=True)
class OutputKey:
    """One top-level key of a command's stdout document."""

    name: str
    stability: Stability = Stability.STABLE
    optional: bool = False
    """Written only under some conditions, such as a flag or a result."""


@dataclass(frozen=True, slots=True)
class OutputShape:
    """One shape of a command's stdout document, and when it is written.

    Only top-level keys are declared; what each key holds is open. The
    writer adds ``meta`` and ``warnings`` to every document, so every shape
    declares them as stable.
    """

    keys: tuple[OutputKey, ...]
    when: str | None = None

    def json_schema(self) -> dict[str, object]:
        """Return the JSON Schema of this shape, with a stability per key."""
        keys = (*self.keys, *_WRITER_KEYS)
        schema: dict[str, object] = {
            "type": "object",
            "properties": {
                key.name: {"x-stability": key.stability.value} for key in keys
            },
            "required": sorted(key.name for key in keys if not key.optional),
            "additionalProperties": False,
        }
        if self.when is not None:
            schema["description"] = self.when
        return schema


_WRITER_KEYS = (OutputKey("meta"), OutputKey("warnings"))


def _shape(*keys: str, when: str | None = None) -> OutputShape:
    """Declare a shape from key names: ``name?`` is optional, ``name~`` experimental."""
    return OutputShape(
        keys=tuple(
            OutputKey(
                key.rstrip("?~"),
                Stability.EXPERIMENTAL if "~" in key else Stability.STABLE,
                optional="?" in key,
            )
            for key in keys
        ),
        when=when,
    )


@dataclass(frozen=True, slots=True)
class CommandContract:
    """One leaf command, its effect, and the gate that guards it."""

    program: str
    """Entry point: ``cloudfall`` or ``cloudfall-engine``."""

    name: str
    """Space-joined subcommand path, such as ``data migrate``."""

    effect: CommandEffect

    summary: str
    """One clause saying what the command does, for the operating contract."""

    gate: str | None = None
    """What has to happen before the command changes servers."""

    output: tuple[OutputShape, ...] = ()
    """Each shape of the JSON document the command writes to stdout."""

    @property
    def invocation(self) -> str:
        """Return the command as typed from the project directory."""
        return f"uv run {self.program} {self.name}"

    def output_schema(self) -> dict[str, object]:
        """Return the JSON Schema of the command's stdout documents."""
        if len(self.output) == 1:
            return self.output[0].json_schema()
        return {"oneOf": [shape.json_schema() for shape in self.output]}


_YES = "`--yes`; without it the command validates and prints the plan only"

_ADDED = (_shape("status", "project", "added"),)
_IMPORTED = (
    _shape(
        "status",
        "application",
        "components",
        "services",
        "domains",
        "written",
        "environmentFiles",
        "report",
        "gaps",
    ),
)
_PROPOSAL = (
    _shape(
        "apiVersion", "kind", "metadata", "spec", when="the proposal, without `status`"
    ),
)
_BACKUP = (_shape("status", "receipt", "path"),)
_EXECUTED = ("status", "action", "component", "servers", "healthy")


def _plan(subject: str, *extra: str) -> OutputShape:
    return _shape(
        "status",
        "action",
        subject,
        "servers",
        "wouldRun",
        *extra,
        "instruction",
        when="without `--yes`: the plan, nothing runs",
    )


CLI_COMMANDS: tuple[CommandContract, ...] = (
    CommandContract(
        "cloudfall",
        "init",
        CommandEffect.PROJECT,
        "lay out a new project directory",
        output=(_shape("status", "project", "files", "next"),),
    ),
    CommandContract(
        "cloudfall",
        "changelog",
        CommandEffect.READ,
        "list changes to the JSON output contract, newest first",
        output=(_shape("status~", "entries~", "schemaVersions~"),),
    ),
    CommandContract(
        "cloudfall",
        "add ssh-key",
        CommandEffect.PROJECT,
        "declare an `SshPublicKey` from a public key file",
        output=_ADDED,
    ),
    CommandContract(
        "cloudfall",
        "add server-type",
        CommandEffect.PROJECT,
        "declare a `ServerType` from the bundled Debian 13 baseline",
        output=_ADDED,
    ),
    CommandContract(
        "cloudfall",
        "add server",
        CommandEffect.PROJECT,
        "declare a `Server`, creating its type on first use",
        output=_ADDED,
    ),
    CommandContract(
        "cloudfall",
        "config validate",
        CommandEffect.READ,
        "validate every resource against the schemas and cross-references",
        output=(_shape("status", "resources", "byKind"),),
    ),
    CommandContract(
        "cloudfall",
        "inventory show",
        CommandEffect.READ,
        "show the typed, secret-free platform inventory",
        output=(
            _shape(
                "status",
                "inventory",
                "ansible?",
                when="`ansible` only when the fleet is read from an Ansible inventory",
            ),
        ),
    ),
    CommandContract(
        "cloudfall",
        "operations list",
        CommandEffect.READ,
        "list the operations an agent may run, with their risk levels",
        output=(_shape("status", "directory", "operations", "byRisk"),),
    ),
    CommandContract(
        "cloudfall",
        "operations show",
        CommandEffect.READ,
        "show one declared operation with its inputs and verify step",
        output=(_shape("status", "operation"),),
    ),
    CommandContract(
        "cloudfall",
        "operations propose",
        CommandEffect.READ,
        "run one operation in check mode and record what it would change",
        output=(_shape("status", "decision"),),
    ),
    CommandContract(
        "cloudfall",
        "operations approve",
        CommandEffect.SERVERS,
        "approve one recorded proposal, run it for real, and verify it",
        gate=_YES,
        output=(
            _shape("status", "decision", "next", when="without `--yes`"),
            _shape("status", "decision", when="with `--yes`"),
        ),
    ),
    CommandContract(
        "cloudfall",
        "operations decisions",
        CommandEffect.READ,
        "list what was proposed, what check mode showed, and who approved",
        output=(_shape("status", "directory", "decisions"),),
    ),
    CommandContract(
        "cloudfall",
        "why",
        CommandEffect.READ,
        "answer why the agent did that from the record, by host, operation or time",
        output=(
            _shape(
                "status~",
                "query~",
                "directory~",
                "count~",
                "answers~",
                when="`--format json`, the default; `--format html` is not JSON",
            ),
        ),
    ),
    CommandContract(
        "cloudfall",
        "observe",
        CommandEffect.READ,
        "collect one read-only snapshot per server into evidence",
        output=(
            _shape("status", "requested", "observed", "missing", "output", "exitCode"),
        ),
    ),
    CommandContract(
        "cloudfall",
        "audit",
        CommandEffect.READ,
        "compare the declared config with observed server snapshots",
        output=(_shape("status", "summary", "unmatchedObservations", "servers"),),
    ),
    CommandContract(
        "cloudfall",
        "services inspect",
        CommandEffect.READ,
        "probe DNS, TLS, origin, and public routes into evidence",
        output=(_shape("status", "observations"),),
    ),
    CommandContract(
        "cloudfall",
        "services status",
        CommandEffect.READ,
        "derive each public service's lifecycle from current evidence",
        output=(_shape("status", "services"),),
    ),
    CommandContract(
        "cloudfall",
        "health",
        CommandEffect.READ,
        "probe one component's declared health check on its servers",
        output=(
            _shape(
                "status",
                "action",
                "component",
                "servers",
                "healthy",
                "detail?",
                when="`detail` only when the component is unhealthy",
            ),
        ),
    ),
    CommandContract(
        "cloudfall",
        "dashboard build",
        CommandEffect.READ,
        "build the static read-only operations dashboard",
        output=(_shape("status", "health", "tasks", "dashboard"),),
    ),
    CommandContract(
        "cloudfall",
        "dashboard serve",
        CommandEffect.READ,
        "serve the read-only dashboard over HTTP (long-running)",
        output=(_shape("status", "dashboard", when="once, before the server starts"),),
    ),
    CommandContract(
        "cloudfall",
        "import render",
        CommandEffect.READ,
        "map a `render.yaml` blueprint onto config fragments under `tmp/`",
        output=_IMPORTED,
    ),
    CommandContract(
        "cloudfall",
        "import render-api",
        CommandEffect.READ,
        "map a live Render workspace onto config fragments under `tmp/`",
        output=_IMPORTED,
    ),
    CommandContract(
        "cloudfall",
        "secrets render",
        CommandEffect.READ,
        "render one component's secret references into a `0600` file under `tmp/`",
        output=(
            _shape(
                "status",
                "component",
                "environmentFile",
                "sha256",
                "keys",
                "declaredKeys",
                "references",
                "receipt",
            ),
        ),
    ),
    CommandContract(
        "cloudfall",
        "operator run",
        CommandEffect.READ,
        "watch declared alerts and drift, write proposal receipts",
        output=(
            _shape(
                "status",
                "pass",
                "proposed",
                "skipped",
                "openProposals",
                when="the alerts pass, and the drift pass when it is due",
            ),
            _shape(
                "status",
                "pass",
                "executed",
                "withheld",
                when="the autonomy pass, when the inventory declares policies",
            ),
        ),
    ),
    CommandContract(
        "cloudfall",
        "operator list",
        CommandEffect.READ,
        "list proposal receipts",
        output=(_shape("status", "proposals"),),
    ),
    CommandContract(
        "cloudfall",
        "operator show",
        CommandEffect.READ,
        "show one proposal receipt",
        output=_PROPOSAL,
    ),
    CommandContract(
        "cloudfall",
        "deploy",
        CommandEffect.SERVERS,
        "activate one built release behind its health check",
        gate=_YES,
        output=(
            _plan("component", "release"),
            _shape(*_EXECUTED, "release", "receipt", when="with `--yes`"),
        ),
    ),
    CommandContract(
        "cloudfall",
        "rollback",
        CommandEffect.SERVERS,
        "switch one component back to an existing release",
        gate=_YES,
        output=(
            _plan("component", "release"),
            _shape(*_EXECUTED, "release", when="with `--yes`"),
        ),
    ),
    CommandContract(
        "cloudfall",
        "restart",
        CommandEffect.SERVERS,
        "restart one component behind its health check",
        gate=_YES,
        output=(_plan("component"), _shape(*_EXECUTED, when="with `--yes`")),
    ),
    CommandContract(
        "cloudfall",
        "data migrate",
        CommandEffect.SERVERS,
        "dump an external database and restore it into a declared service",
        gate=_YES,
        output=(
            _plan("service"),
            _shape(
                "status",
                "action",
                "service",
                "database",
                "servers",
                "verification",
                "receipt",
                when="with `--yes`",
            ),
        ),
    ),
    CommandContract(
        "cloudfall",
        "migrate",
        CommandEffect.SERVERS,
        "run the resumable end-to-end migration plan",
        gate=_YES,
        output=(
            _shape(
                "status",
                "steps",
                "completed",
                "next?",
                when="`plan` without `--yes`; `ok` when every step is done",
            ),
            _shape(
                "status",
                "steps",
                "completed",
                "next?",
                "step",
                "error",
                when="`paused` (exit 3) or `error` (exit 1) at `step`",
            ),
        ),
    ),
    CommandContract(
        "cloudfall",
        "backup run",
        CommandEffect.SERVERS,
        "run the declared backup for one service on its server",
        gate="the service must declare the backup; a receipt is written",
        output=_BACKUP,
    ),
    CommandContract(
        "cloudfall",
        "backup verify",
        CommandEffect.SERVERS,
        "restore the newest backup into a scratch database on the server",
        gate="the service must declare the backup; a receipt is written",
        output=_BACKUP,
    ),
    CommandContract(
        "cloudfall",
        "operator approve",
        CommandEffect.SERVERS,
        "execute one proposal and verify that its trigger resolves",
        gate="the proposal id names a receipt a human has reviewed",
        output=_PROPOSAL,
    ),
)

ENGINE_COMMANDS: tuple[CommandContract, ...] = (
    CommandContract(
        "cloudfall-engine",
        "inventory render",
        CommandEffect.READ,
        "render the Ansible JSON inventory from the validated config",
    ),
    CommandContract(
        "cloudfall-engine",
        "artifact build",
        CommandEffect.READ,
        "clone, package, and hash one component release under `tmp/artifacts`",
    ),
    CommandContract(
        "cloudfall-engine",
        "playbook list",
        CommandEffect.READ,
        "list the playbooks bundled with the engine",
    ),
    CommandContract(
        "cloudfall-engine",
        "playbook run",
        CommandEffect.SERVERS,
        "run one bundled or project playbook against the inventory "
        "(`inspect` only reads; every other playbook converges servers)",
        gate="a human runs it or names the playbook to run",
    ),
)

COMMANDS: tuple[CommandContract, ...] = (*CLI_COMMANDS, *ENGINE_COMMANDS)


def commands_with_effect(effect: CommandEffect) -> tuple[CommandContract, ...]:
    """Return every cataloged command whose side effects reach ``effect``."""
    return tuple(command for command in COMMANDS if command.effect is effect)
