"""Locate the schema catalog and execution engine that ship with Cloudfall.

Cloudfall runs in two shapes. Installed as a wheel, the versioned JSON
schemas and the Ansible engine travel inside the package as bundled data.
Run from a source checkout, they live in the repository tree next to the
packages. Every command-line default resolves through this module so a
fleet repository never has to know which shape it is talking to.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

import cloudfall

_SCHEMA_VERSION = "v1"
_BUNDLE = "_bundled"


class ResourceError(RuntimeError):
    """Raised when neither a bundled nor a checkout resource exists."""


def _package_directory(package: object) -> Path:
    spec = getattr(package, "__file__", None)
    if not isinstance(spec, str):
        message = f"package has no filesystem location: {package!r}"
        raise ResourceError(message)
    return Path(spec).resolve().parent


def _checkout_root() -> Path:
    # sdk/src/cloudfall -> sdk/src -> sdk -> repository root
    return _package_directory(cloudfall).parents[2]


def select_resource(bundled: Path, checkout: Path, description: str) -> Path:
    """Prefer the bundled copy, fall back to the checkout, else fail."""
    if bundled.is_dir():
        return bundled
    if checkout.is_dir():
        return checkout
    message = (
        f"{description} is missing: neither the bundled copy {bundled} nor "
        f"the source checkout copy {checkout} exists"
    )
    raise ResourceError(message)


def default_schema_directory() -> Path:
    """Return the versioned schema directory Cloudfall ships with."""
    bundled = (
        Path(str(resources.files("cloudfall").joinpath(_BUNDLE, "schemas")))
        / _SCHEMA_VERSION
    )
    checkout = _checkout_root() / "config" / "schemas" / _SCHEMA_VERSION
    return select_resource(bundled, checkout, "schema directory")


def default_engine_directory() -> Path:
    """Return the engine directory holding the bundled Ansible contracts."""
    bundled = Path(str(resources.files("cloudfall_engine").joinpath(_BUNDLE)))
    checkout = _checkout_root() / "engine"
    return select_resource(
        bundled / "ansible", checkout / "ansible", "engine directory"
    ).parent
