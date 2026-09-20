"""The one module that imports ansible-core.

ansible-core's Python API carries no stability promise, so every import of
``ansible.*`` lives here and the rest of the SDK sees typed value objects
instead. Reading an inventory in process costs one import (about 150 ms per
CLI invocation) and is in memory afterwards, which matters because the
agent's hot path reads the fleet far more often than it runs a playbook.

When the in-process loader fails on an ansible-core release, the same
function falls back to the ``ansible-inventory`` command and says so in
``AnsibleInventory.in_process``, so a broken internal API degrades to a
slower read rather than to no read at all.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

_HOSTNAME_PATTERN = re.compile(r"^[^\s]{1,253}$")
_GROUP_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_INVENTORY_TIMEOUT_SECONDS = 120
_META_KEY = "_meta"
_HOSTVARS_KEY = "hostvars"
_HOSTS_KEY = "hosts"
_ALL_GROUP = "all"

_ERROR_SOURCE_MISSING = "ansible_inventory_missing"
_ERROR_INVENTORY_UNREADABLE = "ansible_inventory_unreadable"
_ERROR_COMMAND_UNAVAILABLE = "ansible_command_unavailable"


class AnsibleReadError(RuntimeError):
    """Fail-fast inventory read error with a stable machine-readable code."""

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


class _InProcessReadError(RuntimeError):
    """ansible-core's Python API could not serve the read on this release."""


@dataclass(frozen=True, slots=True)
class InventorySource:
    """An Ansible inventory: one file, or a directory of them."""

    value: Path

    def __post_init__(self) -> None:
        """Enforce that the source exists at construction time."""
        if not self.value.exists():
            message = f"inventory source does not exist: {self.value}"
            raise AnsibleReadError(_ERROR_SOURCE_MISSING, message)

    @classmethod
    def from_boundary(cls, value: object) -> InventorySource:
        """Coerce a boundary value while keeping internal APIs strictly typed."""
        if not isinstance(value, str | Path):
            message = f"inventory source must be a path, got {type(value).__name__}"
            raise TypeError(message)
        return cls(Path(value).expanduser())

    def __str__(self) -> str:
        """Return the serialized path."""
        return str(self.value)


@dataclass(frozen=True, slots=True)
class InventoryHostname:
    """Name of a host as the team's inventory spells it."""

    value: str

    def __post_init__(self) -> None:
        """Enforce one non-empty, whitespace-free token."""
        if not _HOSTNAME_PATTERN.fullmatch(self.value):
            message = f"inventory hostname must be one bare token: {self.value!r}"
            raise ValueError(message)

    @classmethod
    def from_boundary(cls, value: object) -> InventoryHostname:
        """Coerce a boundary value while keeping internal APIs strictly typed."""
        if not isinstance(value, str):
            message = f"inventory hostname must be a string, got {type(value).__name__}"
            raise TypeError(message)
        return cls(value.strip())

    def __str__(self) -> str:
        """Return the serialized name."""
        return self.value


@dataclass(frozen=True, slots=True)
class GroupName:
    """Name of an inventory group a host belongs to."""

    value: str

    def __post_init__(self) -> None:
        """Enforce the identifier shape Ansible requires of a group."""
        if not _GROUP_NAME_PATTERN.fullmatch(self.value):
            message = f"group name must be an Ansible identifier: {self.value!r}"
            raise ValueError(message)

    @classmethod
    def from_boundary(cls, value: object) -> GroupName:
        """Coerce a boundary value while keeping internal APIs strictly typed."""
        if not isinstance(value, str):
            message = f"group name must be a string, got {type(value).__name__}"
            raise TypeError(message)
        return cls(value.strip())

    def __str__(self) -> str:
        """Return the serialized name."""
        return self.value


@dataclass(frozen=True, slots=True)
class AnsibleHost:
    """One host of the team's inventory with its merged variables."""

    name: InventoryHostname
    groups: tuple[GroupName, ...]
    variables: Mapping[str, object]

    def variable(self, name: str) -> object | None:
        """Return one merged variable, or ``None`` when the host has none."""
        return self.variables.get(name)


@dataclass(frozen=True, slots=True)
class AnsibleInventory:
    """Every host of one inventory source, read once."""

    source: InventorySource
    hosts: tuple[AnsibleHost, ...]
    in_process: bool
    """The Python API served the read; ``False`` means the command did."""

    def as_dict(self) -> dict[str, object]:
        """Serialize the read for system boundaries, without variables."""
        return {
            "source": str(self.source),
            "inProcess": self.in_process,
            "hosts": [
                {
                    "name": str(host.name),
                    "groups": [str(group) for group in host.groups],
                }
                for host in self.hosts
            ],
        }


def read_inventory(
    source: InventorySource, *, in_process: bool = True
) -> AnsibleInventory:
    """Return every host of the inventory with Ansible's own precedence applied.

    The Python API is tried first and the ``ansible-inventory`` command is
    the fallback, so a release that moves the internal API costs latency
    rather than the read. Pass ``in_process=False`` to take the command
    without trying the API, which is what an operator does when a release
    has broken it.
    """
    if not in_process:
        return _read_via_command(source)
    try:
        return _read_in_process(source)
    except (ImportError, _InProcessReadError):
        return _read_via_command(source)


def _read_in_process(source: InventorySource) -> AnsibleInventory:
    from ansible.errors import AnsibleError  # noqa: PLC0415 - lazy by design
    from ansible.inventory.manager import (  # noqa: PLC0415 - lazy by design
        InventoryManager,
    )
    from ansible.parsing.dataloader import (  # noqa: PLC0415 - lazy by design
        DataLoader,
    )
    from ansible.vars.manager import VariableManager  # noqa: PLC0415 - lazy by design

    try:
        loader = DataLoader()
        manager = InventoryManager(loader=loader, sources=[str(source)])
        variables = VariableManager(loader=loader, inventory=manager)
        hosts = tuple(
            AnsibleHost(
                name=InventoryHostname.from_boundary(host.name),
                groups=_group_names(str(group.name) for group in host.get_groups()),
                variables=_variables(variables.get_vars(host=host)),
            )
            for host in manager.get_hosts()
        )
    except AnsibleError as error:
        raise _InProcessReadError(str(error)) from error
    return AnsibleInventory(source=source, hosts=_sorted(hosts), in_process=True)


def _read_via_command(source: InventorySource) -> AnsibleInventory:
    executable = shutil.which("ansible-inventory")
    if executable is None:
        message = (
            "the ansible-core Python API could not read the inventory and "
            "ansible-inventory is not on PATH"
        )
        raise AnsibleReadError(_ERROR_COMMAND_UNAVAILABLE, message)
    completed = subprocess.run(  # noqa: S603 - resolved binary, fixed arguments.
        [executable, "--inventory", str(source), "--list"],
        check=False,
        capture_output=True,
        text=True,
        timeout=_INVENTORY_TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        message = (
            f"ansible-inventory failed with exit code {completed.returncode}: "
            f"{completed.stderr.strip()}"
        )
        raise AnsibleReadError(_ERROR_INVENTORY_UNREADABLE, message)
    return AnsibleInventory(
        source=source,
        hosts=_sorted(_hosts_from_listing(completed.stdout)),
        in_process=False,
    )


def _hosts_from_listing(listing: str) -> tuple[AnsibleHost, ...]:
    try:
        parsed = json.loads(listing)
    except json.JSONDecodeError as error:
        message = f"ansible-inventory did not emit JSON: {error}"
        raise AnsibleReadError(_ERROR_INVENTORY_UNREADABLE, message) from error
    if not isinstance(parsed, dict):
        message = "ansible-inventory did not emit an inventory object"
        raise AnsibleReadError(_ERROR_INVENTORY_UNREADABLE, message)
    document: Mapping[str, object] = parsed
    hostvars = _hostvars(document)
    groups: dict[str, list[str]] = {}
    for key, value in document.items():
        if key == _META_KEY or not isinstance(value, dict):
            continue
        members = value.get(_HOSTS_KEY)
        if not isinstance(members, list):
            continue
        for member in members:
            if isinstance(member, str):
                groups.setdefault(member, []).append(key)
    return tuple(
        AnsibleHost(
            name=InventoryHostname.from_boundary(name),
            # `all` holds children rather than hosts in the listing, while
            # every host belongs to it; the in-process read reports it.
            groups=_group_names({_ALL_GROUP, *groups.get(name, [])}),
            variables=_variables(variables),
        )
        for name, variables in hostvars.items()
    )


def _hostvars(document: Mapping[str, object]) -> Mapping[str, Mapping[str, object]]:
    meta = document.get(_META_KEY)
    if not isinstance(meta, dict):
        message = "ansible-inventory emitted no _meta section"
        raise AnsibleReadError(_ERROR_INVENTORY_UNREADABLE, message)
    hostvars = meta.get(_HOSTVARS_KEY)
    if not isinstance(hostvars, dict):
        message = "ansible-inventory emitted no _meta.hostvars section"
        raise AnsibleReadError(_ERROR_INVENTORY_UNREADABLE, message)
    return {
        str(name): _variables(variables)
        for name, variables in hostvars.items()
        if isinstance(variables, dict)
    }


def _variables(raw: object) -> Mapping[str, object]:
    if not isinstance(raw, dict):
        return {}
    return {str(key): value for key, value in raw.items()}


def _group_names(names: Iterable[str]) -> tuple[GroupName, ...]:
    groups = tuple(GroupName.from_boundary(name) for name in names)
    return tuple(sorted(groups, key=lambda group: group.value))


def _sorted(hosts: tuple[AnsibleHost, ...]) -> tuple[AnsibleHost, ...]:
    return tuple(sorted(hosts, key=lambda host: host.name.value))
