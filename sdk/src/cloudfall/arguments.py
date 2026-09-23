"""Strict argument parsing shared by every Cloudfall command-line entry point.

Agents guess flag names. With argparse's default prefix matching a guessed
``--api-key`` silently binds to ``--api-key-file``, and its "unrecognized
arguments" error repeats every stray value verbatim, so a secret an agent
passes by mistake lands in stderr. The parser here matches long options
exactly, reports every usage error as the same JSON envelope the commands
use, and names unrecognized options without their values.
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING, Any, NoReturn

from cloudfall.domain import ReleaseId, ResourceId
from cloudfall.manifest import build_manifest
from cloudfall.output import (
    TOOL_VERSION,
    SchemaVersion,
    schema_versions,
    supported_schema_major,
    write_error,
    write_result,
)
from cloudfall.project import project_path

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

INVALID_ARGUMENT = "invalid_argument"


class StrictArgumentParser(argparse.ArgumentParser):
    """Argument parser without abbreviations that fails with a JSON error.

    Subparsers are created with the parent's class, so every nested command
    inherits the same behavior.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401 - mirrors ArgumentParser
        """Create the parser with long-option abbreviations disabled."""
        kwargs["allow_abbrev"] = False
        super().__init__(*args, **kwargs)

    def error(self, message: str) -> NoReturn:
        """Emit the usage error as a JSON envelope and exit with code 2."""
        payload = {
            "status": "error",
            "error": {"code": INVALID_ARGUMENT, "message": f"{self.prog}: {message}"},
        }
        write_error(payload)
        raise SystemExit(2)


class VersionAction(argparse.Action):
    """``--version``: print the tool version as a JSON result and exit 0."""

    def __init__(self, option_strings: Sequence[str], dest: str) -> None:
        """Register a flag that takes no value."""
        super().__init__(
            option_strings,
            dest,
            nargs=0,
            default=argparse.SUPPRESS,
            help="print the cloudfall version as JSON and exit",
        )

    def __call__(
        self,
        parser: argparse.ArgumentParser,  # noqa: ARG002 - argparse Action signature
        namespace: argparse.Namespace,  # noqa: ARG002
        values: object,  # noqa: ARG002
        option_string: str | None = None,  # noqa: ARG002
    ) -> NoReturn:
        """Write the version document before any required argument is checked."""
        write_result(
            {
                "status": "ok",
                "version": TOOL_VERSION,
                "schemaVersions": schema_versions(),
            }
        )
        raise SystemExit(0)


class SchemaAction(argparse.Action):
    """``--schema``: print every command's flags and output contract, exit 0."""

    def __init__(self, option_strings: Sequence[str], dest: str) -> None:
        """Register a flag that takes no value."""
        super().__init__(
            option_strings,
            dest,
            nargs=0,
            default=argparse.SUPPRESS,
            help=(
                "print every command with its flags, effect, and output schema "
                "(a stability tier per top-level key) as JSON and exit"
            ),
        )

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,  # noqa: ARG002 - argparse Action signature
        values: object,  # noqa: ARG002
        option_string: str | None = None,  # noqa: ARG002
    ) -> NoReturn:
        """Write the manifest before any required argument is checked."""
        write_result(build_manifest(parser))
        raise SystemExit(0)


def root_parser(prog: str) -> StrictArgumentParser:
    """Create a command's top-level parser, with ``--version``."""
    parser = StrictArgumentParser(prog=prog)
    parser.add_argument("--version", action=VersionAction)
    parser.add_argument("--schema", "--print-schema", action=SchemaAction)
    parser.add_argument(
        "--schema-version",
        type=schema_major_argument,
        metavar="MAJOR",
        help=(
            "fail unless this build still writes output schema MAJOR, so a "
            "pinned caller stops at a breaking release instead of misreading it"
        ),
    )
    return parser


def parse_arguments(
    parser: StrictArgumentParser, argv: Sequence[str] | None
) -> argparse.Namespace:
    """Parse ``argv``, rejecting unrecognized input without echoing values."""
    arguments, unrecognized = parser.parse_known_args(argv)
    if unrecognized:
        options = sorted(
            {token.partition("=")[0] for token in unrecognized if token.startswith("-")}
        )
        values = len(unrecognized) - sum(
            1 for token in unrecognized if token.startswith("-")
        )
        parts = []
        if options:
            parts.append(f"unrecognized options {' '.join(options)}")
        if values:
            parts.append(f"{values} unexpected value(s), not shown")
        parser.error("; ".join(parts))
    return arguments


def resource_id_argument(value: str) -> ResourceId:
    """Convert a resource id argument, failing as a usage error."""
    try:
        return ResourceId.from_boundary(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def release_id_argument(value: str) -> ReleaseId:
    """Convert a release id argument, failing as a usage error."""
    try:
        return ReleaseId.from_boundary(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def schema_major_argument(value: str) -> int:
    """Convert a ``--schema-version`` pin, failing as a usage error."""
    try:
        return supported_schema_major(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def schema_version_argument(value: str) -> SchemaVersion:
    """Convert a ``MAJOR.MINOR`` output schema version, failing as a usage error."""
    try:
        return SchemaVersion.parse(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def project_path_argument(value: str) -> Path:
    """Convert a runtime path that must stay inside the project directory."""
    try:
        return project_path(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error
