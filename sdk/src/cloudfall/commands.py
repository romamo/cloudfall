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

    @property
    def invocation(self) -> str:
        """Return the command as typed from the project directory."""
        return f"uv run {self.program} {self.name}"


_YES = "`--yes`; without it the command validates and prints the plan only"

CLI_COMMANDS: tuple[CommandContract, ...] = (
    CommandContract(
        "cloudfall",
        "init",
        CommandEffect.PROJECT,
        "lay out a new project directory",
    ),
    CommandContract(
        "cloudfall",
        "add ssh-key",
        CommandEffect.PROJECT,
        "declare an `SshPublicKey` from a public key file",
    ),
    CommandContract(
        "cloudfall",
        "add server-type",
        CommandEffect.PROJECT,
        "declare a `ServerType` from the bundled Debian 13 baseline",
    ),
    CommandContract(
        "cloudfall",
        "add server",
        CommandEffect.PROJECT,
        "declare a `Server`, creating its type on first use",
    ),
    CommandContract(
        "cloudfall",
        "config validate",
        CommandEffect.READ,
        "validate every resource against the schemas and cross-references",
    ),
    CommandContract(
        "cloudfall",
        "inventory show",
        CommandEffect.READ,
        "show the typed, secret-free platform inventory",
    ),
    CommandContract(
        "cloudfall",
        "audit",
        CommandEffect.READ,
        "compare the declared config with observed server snapshots",
    ),
    CommandContract(
        "cloudfall",
        "services inspect",
        CommandEffect.READ,
        "probe DNS, TLS, origin, and public routes into evidence",
    ),
    CommandContract(
        "cloudfall",
        "services status",
        CommandEffect.READ,
        "derive each public service's lifecycle from current evidence",
    ),
    CommandContract(
        "cloudfall",
        "health",
        CommandEffect.READ,
        "probe one component's declared health check on its servers",
    ),
    CommandContract(
        "cloudfall",
        "dashboard build",
        CommandEffect.READ,
        "build the static read-only operations dashboard",
    ),
    CommandContract(
        "cloudfall",
        "dashboard serve",
        CommandEffect.READ,
        "serve the read-only dashboard over HTTP (long-running)",
    ),
    CommandContract(
        "cloudfall",
        "import render",
        CommandEffect.READ,
        "map a `render.yaml` blueprint onto config fragments under `tmp/`",
    ),
    CommandContract(
        "cloudfall",
        "import render-api",
        CommandEffect.READ,
        "map a live Render workspace onto config fragments under `tmp/`",
    ),
    CommandContract(
        "cloudfall",
        "secrets render",
        CommandEffect.READ,
        "render one component's secret references into a `0600` file under `tmp/`",
    ),
    CommandContract(
        "cloudfall",
        "operator run",
        CommandEffect.READ,
        "watch declared alerts and drift, write proposal receipts",
    ),
    CommandContract(
        "cloudfall",
        "operator list",
        CommandEffect.READ,
        "list proposal receipts",
    ),
    CommandContract(
        "cloudfall",
        "operator show",
        CommandEffect.READ,
        "show one proposal receipt",
    ),
    CommandContract(
        "cloudfall",
        "deploy",
        CommandEffect.SERVERS,
        "activate one built release behind its health check",
        gate=_YES,
    ),
    CommandContract(
        "cloudfall",
        "rollback",
        CommandEffect.SERVERS,
        "switch one component back to an existing release",
        gate=_YES,
    ),
    CommandContract(
        "cloudfall",
        "restart",
        CommandEffect.SERVERS,
        "restart one component behind its health check",
        gate=_YES,
    ),
    CommandContract(
        "cloudfall",
        "data migrate",
        CommandEffect.SERVERS,
        "dump an external database and restore it into a declared service",
        gate=_YES,
    ),
    CommandContract(
        "cloudfall",
        "migrate",
        CommandEffect.SERVERS,
        "run the resumable end-to-end migration plan",
        gate=_YES,
    ),
    CommandContract(
        "cloudfall",
        "backup run",
        CommandEffect.SERVERS,
        "run the declared backup for one service on its server",
        gate="the service must declare the backup; a receipt is written",
    ),
    CommandContract(
        "cloudfall",
        "backup verify",
        CommandEffect.SERVERS,
        "restore the newest backup into a scratch database on the server",
        gate="the service must declare the backup; a receipt is written",
    ),
    CommandContract(
        "cloudfall",
        "operator approve",
        CommandEffect.SERVERS,
        "execute one proposal and verify that its trigger resolves",
        gate="the proposal id names a receipt a human has reviewed",
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
