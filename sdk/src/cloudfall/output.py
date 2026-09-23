"""The single writer for every JSON document the ``cloudfall`` CLI prints.

Each document carries a ``meta`` object naming the output contract and the
running tool, so an agent that parsed yesterday's shape can tell when today's
may differ without a separate ``--version`` call:

``schema_version``
    ``MAJOR.MINOR`` of the output contract shared by every command. Bump
    MINOR when a command gains a key; bump MAJOR when a key is removed,
    renamed, or changes meaning. It moves independently of the tool version.

``tool_version``
    The installed ``cloudfall`` package version, the same string
    ``cloudfall --version`` prints.

Each document also carries a ``warnings`` list, empty unless something in it
needs the caller's attention. A key is removed only after a MINOR release in
which it appears in ``DEPRECATED_FIELDS``; while it does, every document that
contains it warns ``FIELD_DEPRECATED`` with its replacement and the version
that drops it. Every change to the contract is recorded in
``SCHEMA_CHANGELOG``, which ``cloudfall changelog`` prints.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from importlib.metadata import version
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import TextIO


@dataclass(frozen=True, order=True, slots=True)
class SchemaVersion:
    """``MAJOR.MINOR`` version of the CLI output contract."""

    major: int
    minor: int

    def __post_init__(self) -> None:
        """Reject negative components."""
        if self.major < 0 or self.minor < 0:
            message = f"invalid schema version: {self.major}.{self.minor}"
            raise ValueError(message)

    @classmethod
    def parse(cls, value: str) -> SchemaVersion:
        """Parse ``MAJOR.MINOR``; anything else is rejected."""
        match = re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", value)
        if match is None:
            message = f"invalid schema version: {value!r}, expected MAJOR.MINOR"
            raise ValueError(message)
        return cls(int(match[1]), int(match[2]))

    def __str__(self) -> str:
        """Return the wire form, ``MAJOR.MINOR``."""
        return f"{self.major}.{self.minor}"


@dataclass(frozen=True, slots=True)
class FieldPath:
    """Dotted path of a key from the document root, such as ``inventory.servers``."""

    value: str

    def __post_init__(self) -> None:
        """Require non-empty dot-separated key names."""
        if not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*", self.value
        ):
            message = f"invalid field path: {self.value!r}"
            raise ValueError(message)

    def present_in(self, document: Mapping[str, object]) -> bool:
        """Return whether every key along the path exists in ``document``."""
        node: object = document
        for key in self.value.split("."):
            if not isinstance(node, Mapping) or key not in node:
                return False
            node = node[key]
        return True


@dataclass(frozen=True, slots=True)
class DeprecatedField:
    """A key still written for compatibility, and what replaces it."""

    path: FieldPath
    replacement: FieldPath
    removed_in: SchemaVersion

    def warning(self) -> dict[str, object]:
        """Return the ``FIELD_DEPRECATED`` warning for a document holding the key."""
        return {
            "code": "FIELD_DEPRECATED",
            "message": (
                f"field '{self.path.value}' is deprecated and is removed in "
                f"schema {self.removed_in}; use '{self.replacement.value}'"
            ),
            "removed_in": str(self.removed_in),
            "context": {
                "field": self.path.value,
                "replacement": self.replacement.value,
            },
        }


@dataclass(frozen=True, slots=True)
class SchemaChange:
    """One release of the output contract."""

    version: SchemaVersion
    released: date
    added: tuple[FieldPath, ...]
    removed: tuple[FieldPath, ...]
    changed: tuple[FieldPath, ...]

    @property
    def breaking(self) -> bool:
        """A release is breaking when it removes or changes a key."""
        return bool(self.removed or self.changed)

    def as_dict(self) -> dict[str, object]:
        """Return the changelog entry as written to the wire."""
        return {
            "version": str(self.version),
            "date": self.released.isoformat(),
            "breaking": self.breaking,
            "added": [path.value for path in self.added],
            "removed": [path.value for path in self.removed],
            "changed": [path.value for path in self.changed],
        }


@dataclass(frozen=True, slots=True)
class ResponseMeta:
    """Versions stamped into every document the CLI writes."""

    schema_version: SchemaVersion
    tool_version: str

    def as_dict(self) -> dict[str, str]:
        """Return the ``meta`` object as written to the wire."""
        return {
            "schema_version": str(self.schema_version),
            "tool_version": self.tool_version,
        }


SCHEMA_CHANGELOG: tuple[SchemaChange, ...] = (
    SchemaChange(
        version=SchemaVersion(1, 0),
        released=date(2026, 9, 23),
        added=(
            FieldPath("meta.schema_version"),
            FieldPath("meta.tool_version"),
            FieldPath("warnings"),
        ),
        removed=(),
        changed=(),
    ),
)
"""Newest last. Add an entry with every change to the output contract."""

OUTPUT_SCHEMA_VERSION = SCHEMA_CHANGELOG[-1].version
MINIMUM_SCHEMA_MAJOR = 1
"""The oldest MAJOR ``--schema-version`` still accepts."""

DEPRECATED_FIELDS: tuple[DeprecatedField, ...] = ()
"""Keys on their way out; each warns until the MAJOR named by ``removed_in``."""

TOOL_VERSION = version("cloudfall")
RESPONSE_META = ResponseMeta(OUTPUT_SCHEMA_VERSION, TOOL_VERSION)


def schema_changes_since(since: SchemaVersion | None) -> list[dict[str, object]]:
    """Return the changelog entries newer than ``since``, newest first."""
    return [
        change.as_dict()
        for change in reversed(SCHEMA_CHANGELOG)
        if since is None or change.version > since
    ]


def supported_schema_major(value: str) -> int:
    """Validate a ``--schema-version`` pin against the majors this build writes."""
    if not re.fullmatch(r"[1-9][0-9]*", value):
        message = f"invalid schema version: {value!r}, expected a MAJOR number"
        raise ValueError(message)
    major = int(value)
    if not MINIMUM_SCHEMA_MAJOR <= major <= OUTPUT_SCHEMA_VERSION.major:
        message = (
            f"schema version {major} is not supported; this build writes "
            f"{MINIMUM_SCHEMA_MAJOR} to {OUTPUT_SCHEMA_VERSION.major}"
        )
        raise ValueError(message)
    return major


def schema_versions() -> dict[str, str]:
    """Return the current and oldest accepted output contract versions."""
    return {
        "current": str(OUTPUT_SCHEMA_VERSION),
        "minimum": f"{MINIMUM_SCHEMA_MAJOR}.0",
    }


def write_result(payload: Mapping[str, object]) -> None:
    """Write a result document to stdout."""
    _write(sys.stdout, payload)
    # A pipe makes stdout block-buffered; flush so long-running commands such
    # as `operator run --interval` deliver each document when it is written.
    sys.stdout.flush()


def write_error(payload: Mapping[str, object]) -> None:
    """Write an error document to stderr."""
    _write(sys.stderr, payload)


def stamp(
    payload: Mapping[str, object],
    deprecated_fields: tuple[DeprecatedField, ...] = DEPRECATED_FIELDS,
) -> dict[str, object]:
    """Return ``payload`` with the ``meta`` and ``warnings`` the writer owns."""
    for owned in ("meta", "warnings"):
        if owned in payload:
            message = f"command payloads must not set {owned!r}; the writer owns it"
            raise ValueError(message)
    warnings = [
        deprecated.warning()
        for deprecated in deprecated_fields
        if deprecated.path.present_in(payload)
    ]
    return {**payload, "meta": RESPONSE_META.as_dict(), "warnings": warnings}


def _write(stream: TextIO, payload: Mapping[str, object]) -> None:
    stream.write(f"{json.dumps(stamp(payload), sort_keys=True)}\n")
