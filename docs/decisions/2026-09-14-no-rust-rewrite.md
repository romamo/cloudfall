# Decision: no Rust rewrite for now

Date: 2026-09-14. Status: decided, revisit on the triggers listed below.

## Question

Is it worth migrating Cloudfall from Python to Rust?

## Decision

No. Cloudfall stays a Python control plane over an Ansible execution engine.
The module boundaries described in [`ARCHITECTURE.md`](../../ARCHITECTURE.md)
stay strict so that a Rust component can be added behind the engine contract
later without a rewrite.

## What the code is

| Part | Size | Nature |
|---|---|---|
| SDK (`sdk/src/cloudfall`) | ~15,400 lines | YAML validation, config model, receipts, audit, CLI, MCP server, dashboard |
| Engine (`engine/src`) | ~940 lines | Thin wrapper that shells out to `ansible-playbook` |
| Ansible roles and playbooks | ~5,200 lines, 107 files | The part that touches servers |
| Tests | ~5,100 lines | |

## Why Rust does not pay

- **The runtime is Ansible, and Ansible is Python.** Everything proven live
  from M0 to M10 lives in roles, templates, and playbooks. A Rust control
  plane would still need `ansible-core` on the management host, so the main
  Rust benefit, one static binary with no interpreter, is not available
- **Nothing is CPU-bound.** Wall-clock time is SSH, apt, certbot, and
  PostgreSQL dumps. Validating a project against JSON Schema takes
  milliseconds in Python; Rust would speed up the part that does not matter
- **Stage of the product.** Version 0.1.0, one real workload in migration, one
  maintainer. The scarce resource is proving runs and user feedback. A rewrite
  freezes the roadmap for a quarter and lands where the project already is
- **Ecosystem fit.** The MCP server, jsonschema, and sops/age integration have
  mature Python libraries. The Rust MCP SDK is younger, and the schema catalog
  bundling in the wheel would have to be redone
- **Agents write Python well.** The AI-native pitch depends on agents editing
  config and calling a stable CLI and Python API. That API is a selling point
  for the Python-heavy SaaS teams that are the wedge audience

## Triggers to revisit

- **Replacing Ansible with an in-house SSH executor.** Then a Rust or Go
  controller becomes coherent, because the whole stack would be
  interpreter-free. This is a product decision, not a language one, and it
  discards the roles
- **Adding a host-side agent** for evidence collection or observation
  streaming. A small static binary per server suits Rust or Go and can coexist
  with the Python control plane
- **Distribution friction**, meaning users objecting to installing Python on
  the management host. The cheaper fix is a standalone build via PyInstaller
  or pyapp, or `uv tool install cloudfall`, which the bundled wheel already
  supports

## Consequences

- No language change on the roadmap
- Keep the engine reachable only through its command-line and playbook
  contracts, never its internals, so a future non-Python engine or agent can
  replace it piecewise
