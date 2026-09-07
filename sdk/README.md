# SDK

This module provides the stable Python API used by AI agents, future CLI and MCP
entry points, and other operational tooling.

Its first milestone is read-only: load state, validate it strictly, resolve
references, and expose inventory queries without depending on Ansible internals.

The SDK validates v1 Server, HostProfile, Project, and Component resources and
builds a read-only typed index. The `atlas state validate` command is its first
system boundary.

`LoggingStack` validation additionally resolves backend, collector, project,
and component placement; enforces one stack per environment; and prevents the
first migration slice from disabling legacy log agents. Inventory output
contains certificate paths and package pins but never secret values.

`atlas inventory show` projects validated state into typed server, project, and
component records. It supports component-to-server placement queries and omits
secret references from serialized output.

`atlas audit` first validates normalized `ObservedServer` JSON snapshots, then
compares every server with its referenced `HostProfile`. Checks cover OS and
service manager, RAID level/health/capacity, mounted filesystems, required and
forbidden packages, service state, and allowlisted configuration evidence.

```console
uv run atlas audit state/examples --observed tmp/observed
```

`atlas dashboard build` combines the same validated audit data with operational
signals such as disk pressure, failed services, and stale evidence. It emits a
dependency-free static dashboard plus `operations.json` for other clients:

```console
uv run atlas dashboard build state/examples \
  --observed tmp/observed --output tmp/dashboard
```

The report and exit code distinguish compliant state, detected drift, malformed
inputs, and missing observations. This gives agents a stable decision boundary
without requiring them to infer server state from ad hoc SSH commands.
