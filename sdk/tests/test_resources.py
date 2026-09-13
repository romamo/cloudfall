"""Bundled resource resolution for schemas and the engine."""

from __future__ import annotations

from pathlib import Path

import pytest
from cloudfall.resources import (
    ResourceError,
    default_engine_directory,
    default_schema_directory,
    select_resource,
)

ROOT = Path(__file__).parents[2]


def test_default_schema_directory_is_the_versioned_catalog() -> None:
    directory = default_schema_directory()
    assert directory.name == "v1"
    assert (directory / "server.schema.json").is_file()


def test_default_engine_directory_holds_the_ansible_contracts() -> None:
    directory = default_engine_directory()
    assert (directory / "ansible" / "ansible.cfg").is_file()
    assert (directory / "ansible" / "playbooks" / "inspect.yml").is_file()
    assert (directory / "ansible" / "roles" / "cloudfall_inspect").is_dir()


def test_source_checkout_resolves_to_the_repository_tree() -> None:
    assert default_schema_directory() == ROOT / "config" / "schemas" / "v1"
    assert default_engine_directory() == ROOT / "engine"


def test_select_resource_prefers_the_bundled_copy(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    checkout = tmp_path / "checkout"
    bundled.mkdir()
    checkout.mkdir()
    assert select_resource(bundled, checkout, "thing") == bundled


def test_select_resource_falls_back_to_the_checkout(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    assert select_resource(bundled, checkout, "thing") == checkout


def test_select_resource_fails_when_both_are_missing(tmp_path: Path) -> None:
    with pytest.raises(ResourceError, match="thing is missing"):
        select_resource(tmp_path / "a", tmp_path / "b", "thing")
