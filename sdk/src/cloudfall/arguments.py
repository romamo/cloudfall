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
import json
import sys
from typing import TYPE_CHECKING, Any, NoReturn

from cloudfall.domain import ReleaseId, ResourceId
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
        sys.stderr.write(f"{json.dumps(payload, sort_keys=True)}\n")
        raise SystemExit(2)


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


def project_path_argument(value: str) -> Path:
    """Convert a runtime path that must stay inside the project directory."""
    try:
        return project_path(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error
