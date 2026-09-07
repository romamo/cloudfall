# Contributing

Thanks for your interest in Atlas. The project is young and the fastest way to
help is to try the wedge workflow on a disposable Debian host and report where
it breaks.

## Development environment

Atlas requires Python 3.14 and [`uv`](https://docs.astral.sh/uv/). All commands
run through `uv run`.

```console
uv sync
uv run pytest
```

## Checks

Every change must pass the same suite CI runs:

```console
uv run ruff check .
uv run mypy
uv run pytest
uv run atlas state validate state/examples
uv run ansible-lint engine/ansible
```

## Ground rules

- **Fail fast.** No broad exception handling, no swallowed errors, no
  placeholders. Validation rejects malformed input immediately
- **Typed domain.** Domain concepts use strict value objects, not bare
  primitives; wide unions belong only at system boundaries
- **Module boundaries.** `state/` contains configuration and schemas only; the
  SDK reads and validates state without Ansible internals; the engine consumes
  validated inputs and never silently rewrites state
- **Synthetic fixtures only.** Example state, tests, and docs must never
  contain real hostnames, addresses, provider identifiers, credentials, or
  hardware serials. Playbooks and roles in this repository may reference only
  resources that exist in `state/examples`
- **Evidence over inference.** Status is derived from validated observations
  and receipts; nothing reports success it cannot prove

## Pull requests

Keep changes scoped to one concern. Include tests for behavior changes and
update the relevant README when a workflow changes. By contributing you agree
that your contributions are licensed under the AGPL-3.0-or-later license of
this repository.
