"""Every JSON document names its output contract and the running tool."""

from __future__ import annotations

import json
from datetime import date
from importlib.metadata import version
from pathlib import Path

import pytest
from cloudfall.cli import main
from cloudfall.output import (
    RESPONSE_META,
    SCHEMA_CHANGELOG,
    DeprecatedField,
    FieldPath,
    SchemaChange,
    SchemaVersion,
    stamp,
    write_result,
)

ROOT = Path(__file__).parents[2]
EXAMPLES = ROOT / "config" / "examples"
META = {"schema_version": "1.0", "tool_version": version("cloudfall")}


def _run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    try:
        code = main(argv)
    except SystemExit as exit_info:
        code = int(str(exit_info.code))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_version_flag_prints_the_package_and_schema_versions(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, out, err = _run(["--version"], capsys)

    assert (code, err) == (0, "")
    assert json.loads(out) == {
        "status": "ok",
        "version": version("cloudfall"),
        "schemaVersions": {"current": "1.0", "minimum": "1.0"},
        "meta": META,
        "warnings": [],
    }


def test_results_and_errors_carry_the_same_meta_and_warnings(
    capsys: pytest.CaptureFixture[str],
) -> None:
    ok, result, _ = _run(["config", "validate", "--project", str(EXAMPLES)], capsys)
    failed, _, error = _run(
        ["config", "validate", "--project", str(ROOT / "missing")], capsys
    )
    usage_code, _, usage = _run(["deploy"], capsys)

    assert (ok, failed, usage_code) == (0, 2, 2)
    for document in (json.loads(result), json.loads(error), json.loads(usage)):
        assert document["meta"] == RESPONSE_META.as_dict() == META
        assert document["warnings"] == []


@pytest.mark.parametrize("owned", ["meta", "warnings"])
def test_a_payload_cannot_set_what_the_writer_owns(owned: str) -> None:
    with pytest.raises(ValueError, match="writer owns it"):
        write_result({"status": "ok", owned: {}})


def test_a_deprecated_field_warns_while_it_is_still_written() -> None:
    deprecated = DeprecatedField(
        path=FieldPath("inventory.servers"),
        replacement=FieldPath("inventory.hosts"),
        removed_in=SchemaVersion(2, 0),
    )

    with_field = stamp({"inventory": {"servers": []}}, (deprecated,))
    without_field = stamp({"inventory": {"hosts": []}}, (deprecated,))

    assert with_field["warnings"] == [
        {
            "code": "FIELD_DEPRECATED",
            "message": (
                "field 'inventory.servers' is deprecated and is removed in "
                "schema 2.0; use 'inventory.hosts'"
            ),
            "removed_in": "2.0",
            "context": {"field": "inventory.servers", "replacement": "inventory.hosts"},
        }
    ]
    assert without_field["warnings"] == []


def test_changelog_lists_contract_changes_newest_first(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, out, _ = _run(["changelog"], capsys)
    since_code, since_out, _ = _run(["changelog", "--since", "1.0"], capsys)

    assert (code, since_code) == (0, 0)
    entries = json.loads(out)["entries"]
    assert entries[0] == {
        "version": "1.0",
        "date": "2026-09-23",
        "breaking": False,
        "added": ["meta.schema_version", "meta.tool_version", "warnings"],
        "removed": [],
        "changed": [],
    }
    assert [entry["version"] for entry in entries] == [
        str(change.version) for change in reversed(SCHEMA_CHANGELOG)
    ]
    assert json.loads(since_out)["entries"] == []


def test_a_removal_or_change_is_breaking() -> None:
    def change(**fields: tuple[FieldPath, ...]) -> SchemaChange:
        return SchemaChange(
            version=SchemaVersion(2, 0),
            released=date(2027, 1, 1),
            added=fields.get("added", ()),
            removed=fields.get("removed", ()),
            changed=fields.get("changed", ()),
        )

    assert not change(added=(FieldPath("a"),)).breaking
    assert change(removed=(FieldPath("a"),)).breaking
    assert change(changed=(FieldPath("a"),)).breaking


def test_changelog_since_rejects_a_bare_major(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, _, err = _run(["changelog", "--since", "1"], capsys)

    assert code == 2
    assert "expected MAJOR.MINOR" in json.loads(err)["error"]["message"]


def test_schema_version_pin_accepts_the_current_major(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, out, _ = _run(["--schema-version", "1", "changelog"], capsys)

    assert code == 0
    assert json.loads(out)["meta"]["schema_version"] == "1.0"


@pytest.mark.parametrize(
    ("pin", "reason"),
    [("2", "not supported"), ("0", "expected a MAJOR number"), ("1.0", "MAJOR")],
)
def test_schema_version_pin_fails_before_any_command_runs(
    pin: str, reason: str, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = _run(["--schema-version", pin, "changelog"], capsys)

    assert (code, out) == (2, "")
    error = json.loads(err)["error"]
    assert error["code"] == "invalid_argument"
    assert reason in error["message"]


@pytest.mark.parametrize("value", ["1", "1.0.0", "v1.0", "01.0", "1.x", ""])
def test_schema_version_is_major_dot_minor(value: str) -> None:
    with pytest.raises(ValueError, match="invalid schema version"):
        SchemaVersion.parse(value)


def test_schema_versions_order_numerically() -> None:
    assert SchemaVersion.parse("1.10") > SchemaVersion.parse("1.9")
