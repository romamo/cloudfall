# SDK

This module provides the stable Python API used by AI agents, future CLI and MCP
entry points, and other operational tooling.

Most of the SDK is read-only: load state, validate it strictly, resolve
references, and expose inventory queries without depending on Ansible internals.

The lifecycle module adds the first mutating operations: `deploy()`,
`rollback()`, `restart()`, and `health()` with structured JSON results. They
verify artifacts (schema, identity, and recomputed digest) before anything
runs, then execute through the engine's process boundaries only — the
`cloudfall_engine` command-line contract for inventory rendering and the
engine's playbook contract for execution — never Ansible internals. Each
operation is split into a pure, testable execution plan and a thin executor:

```console
uv run cloudfall deploy config/examples crm-backend --release <release-id>
uv run cloudfall rollback config/examples crm-backend --release <release-id>
uv run cloudfall restart config/examples crm-backend
uv run cloudfall health config/examples crm-backend
```

`deploy` refuses to run when the built artifact is missing, misidentified, or
fails digest verification, and refuses to report success if the engine wrote
no release receipt. `health` exits `0` when every declared server passes the
component's declared health check and `1` otherwise.

`cloudfall import render` maps a Render blueprint onto schema-validated state
fragments plus environment files outside state, and returns a structured gap
report of unsupported services, assumptions, and required actions instead of
guessing silently.

## MCP server

`cloudfall-mcp` (requires the `cloudfall[mcp]` extra) exposes the agent
toolset over stdio. Read-only evidence tools — validate, inventory, audit,
service status, health probes, and both inspection collectors — are exposed
freely with read-only annotations. Everything that changes servers
(deploy, rollback, restart, and the baseline, services, and domains
convergers) is annotated destructive and demands a two-step handshake: the
first call returns a `confirmation-required` preview describing exactly what
would run; only a second call with `confirm=true` executes. Deployments
through MCP always write release receipts.

```console
uv run cloudfall-mcp --state config/examples --engine engine
```

Every tool returns a structured JSON envelope, including errors, so agents
never need to parse free-form failures. An agent connected to this server
can drive the full migration path — import a blueprint, build artifacts,
converge the baseline, services, and domains, deploy with automatic
rollback, and audit the result — without shell access to any server.

The `migrate` tool (also `cloudfall migrate` on the CLI) chains all of that
as one resumable plan with persisted step progress: baseline, services,
builds, deployments, HTTP routes, a DNS-verification pause at the cutover
moment, TLS issuance, and a final evidence pass that requires a compliant
audit and healthy routes before declaring success. Without confirmation it
returns the plan preview; interrupted or paused runs resume at the first
incomplete step.

The SDK validates v1 Server, ServerType, Application, and Component resources and
builds a read-only typed index. The `cloudfall config validate` command is its first
system boundary.

`LoggingStack` validation additionally resolves backend, collector, application,
and component placement; enforces one stack per environment; and prevents the
first migration slice from disabling legacy log agents. Inventory output
contains certificate paths and package pins but never secret values.

`cloudfall inventory show` applications validated state into typed server, application, and
component records. It supports component-to-server placement queries and omits
secret references from serialized output.

`cloudfall audit` first validates normalized `ObservedServer` JSON snapshots, then
compares every server with its referenced `ServerType`. Checks cover OS and
service manager, RAID level/health/capacity, mounted filesystems, required and
forbidden packages, service state, and allowlisted configuration evidence.

```console
uv run cloudfall audit config/examples --observed tmp/observed
```

`cloudfall dashboard build` combines the same validated audit data with operational
signals such as disk pressure, failed services, and stale evidence. It emits a
dependency-free static dashboard plus `operations.json` for other clients:

```console
uv run cloudfall dashboard build config/examples \
  --observed tmp/observed --output tmp/dashboard
```

The report and exit code distinguish compliant state, detected drift, malformed
inputs, and missing observations. This gives agents a stable decision boundary
without requiring them to infer server state from ad hoc SSH commands.
