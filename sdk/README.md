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
uv run cloudfall deploy state/examples crm-backend --release <release-id>
uv run cloudfall rollback state/examples crm-backend --release <release-id>
uv run cloudfall restart state/examples crm-backend
uv run cloudfall health state/examples crm-backend
```

`deploy` refuses to run when the built artifact is missing, misidentified, or
fails digest verification, and refuses to report success if the engine wrote
no release receipt. `health` exits `0` when every declared server passes the
component's declared health check and `1` otherwise.

The SDK validates v1 Server, HostProfile, Project, and Component resources and
builds a read-only typed index. The `cloudfall state validate` command is its first
system boundary.

`LoggingStack` validation additionally resolves backend, collector, project,
and component placement; enforces one stack per environment; and prevents the
first migration slice from disabling legacy log agents. Inventory output
contains certificate paths and package pins but never secret values.

`cloudfall inventory show` projects validated state into typed server, project, and
component records. It supports component-to-server placement queries and omits
secret references from serialized output.

`cloudfall audit` first validates normalized `ObservedServer` JSON snapshots, then
compares every server with its referenced `HostProfile`. Checks cover OS and
service manager, RAID level/health/capacity, mounted filesystems, required and
forbidden packages, service state, and allowlisted configuration evidence.

```console
uv run cloudfall audit state/examples --observed tmp/observed
```

`cloudfall dashboard build` combines the same validated audit data with operational
signals such as disk pressure, failed services, and stale evidence. It emits a
dependency-free static dashboard plus `operations.json` for other clients:

```console
uv run cloudfall dashboard build state/examples \
  --observed tmp/observed --output tmp/dashboard
```

The report and exit code distinguish compliant state, detected drift, malformed
inputs, and missing observations. This gives agents a stable decision boundary
without requiring them to infer server state from ad hoc SSH commands.
