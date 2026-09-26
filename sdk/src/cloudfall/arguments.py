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
import sys
import textwrap
from typing import TYPE_CHECKING, Any, NoReturn

from cloudfall.commands import EXIT_CODES
from cloudfall.domain import ReleaseId, ResourceId
from cloudfall.manifest import build_manifest, leaf_parsers
from cloudfall.output import (
    TOOL_VERSION,
    OutputOptions,
    SchemaVersion,
    configure_output,
    current_invocation,
    schema_versions,
    supported_schema_major,
    write_error,
    write_result,
)
from cloudfall.project import project_path

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from _typeshed import SupportsWrite

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
        self.output_options = False
        """Whether ``add_output_options`` gave this command tree ``--quiet``."""

    def print_help(self, file: SupportsWrite[str] | None = None) -> None:
        """Print help to stdout for a person, to stderr when stdout is not a TTY.

        A caller capturing stdout expects JSON there, so help text goes to
        stderr instead, and ``--quiet`` silences it like any other stderr.
        """
        if file is None:
            if sys.stdout.isatty():
                file = sys.stdout
            elif current_invocation().options.quiet:
                return
            else:
                file = sys.stderr
        super().print_help(file)

    def format_help(self) -> str:
        """Return the help text, ending with the exit codes where they apply."""
        text = super().format_help()
        if not self.output_options:
            return text
        return f"{text}\n{exit_code_help()}"

    def error(self, message: str) -> NoReturn:
        """Emit the usage error as a JSON envelope and exit with code 2."""
        payload = {
            "status": "error",
            "error": {"code": INVALID_ARGUMENT, "message": f"{self.prog}: {message}"},
        }
        raise SystemExit(write_error(payload, 2))


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


OUTPUT_FORMATS = ("json",)
"""What ``--output`` accepts. JSON is the only format, so the flag changes
nothing; it exists so a caller that asks for JSON the usual way gets it."""

_QUIET = "--quiet"
_WARNINGS_AS_ERRORS = "--warnings-as-errors"


def add_output_options(parser: StrictArgumentParser) -> None:
    """Accept the output options before the command and after every leaf command.

    ``--output json``, ``--quiet`` and ``--warnings-as-errors`` describe the
    output channels, not the command, so every command takes them, and
    every command's ``--help`` ends with the exit codes.
    """
    parser.output_options = True
    _add_output_arguments(parser, root=True)
    for _name, leaf in leaf_parsers(parser):
        if leaf is not parser:
            if isinstance(leaf, StrictArgumentParser):
                leaf.output_options = True
            _add_output_arguments(leaf, root=False)


def exit_code_help() -> str:
    """Return the exit codes as the closing section of ``--help``."""
    lines = ["exit codes:"]
    for exit_code in EXIT_CODES:
        meaning = exit_code.meaning.replace("`", "")
        lines.extend(
            textwrap.wrap(
                meaning,
                width=78,
                initial_indent=f"  {exit_code.code}  ",
                subsequent_indent="     ",
            )
        )
    return "\n".join(lines) + "\n"


def _add_output_arguments(parser: argparse.ArgumentParser, *, root: bool) -> None:
    # On a leaf, SUPPRESS keeps an absent flag from resetting the value
    # given before the command.
    parser.add_argument(
        "--output",
        dest="output_format",
        choices=OUTPUT_FORMATS,
        default=OUTPUT_FORMATS[0] if root else argparse.SUPPRESS,
        help="output format; JSON is the only one and the default",
    )
    parser.add_argument(
        _QUIET,
        action="store_true",
        default=False if root else argparse.SUPPRESS,
        help="write nothing to stderr, not even an error; read the exit code",
    )
    parser.add_argument(
        _WARNINGS_AS_ERRORS,
        action="store_true",
        default=False if root else argparse.SUPPRESS,
        help="fail, exit 1, when a result carries any warning",
    )


def parse_arguments(
    parser: StrictArgumentParser, argv: Sequence[str] | None
) -> argparse.Namespace:
    """Parse ``argv``, rejecting unrecognized input without echoing values.

    Where the parser defines the output options, they apply before parsing,
    read from the raw tokens, so a usage error honors ``--quiet``; once
    parsing succeeds, the parsed values replace them. A parser without them
    reports ``--quiet`` as the unrecognized option it is.
    """
    tokens = list(sys.argv[1:] if argv is None else argv)
    given = tokens[: tokens.index("--")] if "--" in tokens else tokens
    if parser.output_options:
        configure_output(
            OutputOptions(
                quiet=_QUIET in given, warnings_as_errors=_WARNINGS_AS_ERRORS in given
            )
        )
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
    configure_output(
        OutputOptions(
            quiet=bool(getattr(arguments, "quiet", False)),
            warnings_as_errors=bool(getattr(arguments, "warnings_as_errors", False)),
        )
    )
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
