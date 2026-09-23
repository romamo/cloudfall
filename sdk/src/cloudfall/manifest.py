"""``cloudfall --schema``: every command, its flags, and its output contract.

Flags are read from the argparse tree, so a new option appears here without
being documented twice. Effects, gates and output keys come from the command
catalog, which the tests hold to the same tree. The document is identical
between invocations of one build; ``etag`` changes only when a command,
flag, or declared output does.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING

from cloudfall.commands import CLI_COMMANDS, CommandContract

if TYPE_CHECKING:
    from collections.abc import Iterator


def build_manifest(parser: argparse.ArgumentParser) -> dict[str, object]:
    """Return the manifest of every ``cloudfall`` leaf command."""
    leaves = dict(_leaves(parser))
    contracts = {contract.name: contract for contract in CLI_COMMANDS}
    if set(leaves) != set(contracts):
        message = (
            "command catalog and parser disagree: "
            f"{sorted(set(leaves) ^ set(contracts))}"
        )
        raise ValueError(message)
    commands = {
        name: _command_entry(contracts[name], leaves[name]) for name in sorted(leaves)
    }
    global_flags = {
        name: flag
        for action in parser._actions  # noqa: SLF001 - argparse has no public walk.
        if not isinstance(action, argparse._SubParsersAction)  # noqa: SLF001
        for name, flag in _flag_entry(action)
    }
    digest = hashlib.sha256(
        json.dumps([global_flags, commands], sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {
        "status": "ok",
        "etag": f"sha256:{digest}",
        "global_flags": global_flags,
        "commands": commands,
    }


def _command_entry(
    contract: CommandContract, parser: argparse.ArgumentParser
) -> dict[str, object]:
    entry: dict[str, object] = {
        "description": contract.summary,
        "effect": contract.effect.value,
        "flags": {
            name: flag
            for action in parser._actions  # noqa: SLF001 - argparse has no public walk.
            for name, flag in _flag_entry(action)
        },
        "output_schema": contract.output_schema(),
    }
    if contract.gate is not None:
        entry["gate"] = contract.gate
    return entry


def _flag_entry(action: argparse.Action) -> Iterator[tuple[str, dict[str, object]]]:
    if isinstance(action, argparse._HelpAction):  # noqa: SLF001
        return
    long_options = [
        option for option in action.option_strings if option.startswith("--")
    ]
    positional = not action.option_strings
    name = action.dest if positional else long_options[0].removeprefix("--")
    flag: dict[str, object] = {
        "type": _flag_type(action),
        "required": bool(action.required),
        "description": action.help or "",
    }
    if positional:
        flag["positional"] = True
    if action.choices is not None:
        flag["enum_values"] = [str(choice) for choice in action.choices]
    default = _json_default(action.default)
    if default is not None:
        flag["default"] = default
    yield name, flag


def _flag_type(action: argparse.Action) -> str:
    if isinstance(action, argparse._StoreTrueAction | argparse._StoreFalseAction):  # noqa: SLF001
        return "boolean"
    if action.choices is not None:
        return "enum"
    if isinstance(action, argparse._AppendAction) or action.nargs in {"*", "+"}:  # noqa: SLF001
        return "array"
    if action.type is int:
        return "integer"
    if action.type is float:
        return "number"
    return "string"


def _json_default(value: object) -> object:
    """Return a default as JSON, or ``None`` when there is none to show."""
    if value is None or value is argparse.SUPPRESS or value is False:
        return None
    if isinstance(value, Path) and value.is_absolute():
        # Resolved on this machine (the bundled engine or schemas): the help
        # text names it, and the path would differ on every install.
        return None
    if isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, list | tuple):
        return [str(item) for item in value] or None
    return str(value)


def _leaves(
    parser: argparse.ArgumentParser, prefix: str = ""
) -> Iterator[tuple[str, argparse.ArgumentParser]]:
    subparsers = [
        action
        for action in parser._actions  # noqa: SLF001
        if isinstance(action, argparse._SubParsersAction)  # noqa: SLF001
    ]
    if not subparsers:
        yield prefix.strip(), parser
        return
    for action in subparsers:
        for name, child in action.choices.items():
            yield from _leaves(child, f"{prefix} {name}")
