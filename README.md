# Atlas

Atlas is an AI-native, self-hosted control plane for operating standalone SaaS
applications across dedicated Debian servers without Kubernetes.

The repository is organized as a monorepo with three modules:

- [`state/`](state/README.md) defines and validates declarative platform state.
- [`engine/`](engine/README.md) executes explicit plans against servers.
- [`sdk/`](sdk/README.md) provides the stable API used by agents and tooling.

See [`PROJECT_CONTEXT.md`](PROJECT_CONTEXT.md) for the current architecture,
requirements, and implementation sequence.

The first guarded replacement-logging slice is available for a parallel pilot.
It deploys Loki, loopback-only Grafana, an mTLS ingestion gateway, and Alloy
without changing legacy agents. Follow the
[`logging service guide`](docs/logging-service-guide.md); production state does
not declare a logging stack by default.

New two-drive servers use a RAID1 system area plus independent storage tails
for data that is replicated to other hosts. Review the
[`hybrid storage design`](docs/hybrid-storage-design.md), then follow the
destructive, new-server-only
[`storage provisioning guide`](docs/new-server-storage-guide.md). Neither
document authorizes storage changes on an existing server.

## Dependency boundaries

- State contains configuration and schemas only.
- The SDK reads and validates state without depending on Ansible internals.
- The engine consumes validated inputs and never silently rewrites state.
- Future CLI and MCP entry points call the SDK rather than Ansible directly.

These boundaries allow the modules to be split into separate repositories later
if their release cycles or access-control requirements diverge.

## Validate example state

Atlas requires Python 3.14 and uses `uv` for dependency and command execution.

```console
uv run atlas state validate state/examples
```

The command validates every YAML document against the v1 JSON Schemas and then
checks cross-resource references. Successful and failed results are emitted as
structured JSON.

The SDK can also return a non-secret platform inventory:

```console
uv run atlas inventory show state/examples
```

The engine converts that typed inventory into deterministic Ansible JSON:

```console
uv run atlas-engine inventory render state/examples
```

## Check the bootstrap role

The bootstrap role assumes Debian is already installed, RAID is configured, and
the inventory SSH account can connect and become root. It installs baseline
packages and creates project users and `/srv/apps` directory structures.

```console
task ansible:syntax
```

The equivalent commands are defined in [`Taskfile.yml`](Taskfile.yml). No live
host changes are made by this syntax-and-lint workflow.

## Inspect servers and audit drift

Each `Server` references a reusable `HostProfile` that describes its required
Debian version, software RAID, mounted filesystem capacity, packages, systemd
services, and allowlisted configuration evidence.

Render inventory and collect a read-only snapshot from every reachable server:

```console
task inspect
```

Snapshots are written on the controller to `tmp/observed/<server>.json` with
mode `0600`. The directory is ignored by Git. The collector records hardware,
block devices, mounts, `/proc/mdstat`, the `mdadm` scan, installed-package
facts, systemd service facts, structured read-only NVMe SMART evidence when
`smartctl` is available, and allowlisted file metadata or SHA-256 hashes. It
never copies configuration-file contents.

Compare those observations with desired state:

```console
task audit
```

The direct SDK command is:

```console
uv run atlas audit state/examples --observed tmp/observed
```

The command emits one JSON report. Exit code `0` means compliant, `1` means
drift, `2` means invalid state or observations, and `3` means compliance is
unknown because an observation is missing. A schema-valid compliant example is
available in `state/tests/observed/compliant/`.

## Build the operations dashboard

Atlas can project validated state, observations, and audit results into a local
read-only dashboard:

```console
task dashboard STATE_DIR=state/examples
```

The build writes `tmp/dashboard/index.html` and a stable machine-readable
`tmp/dashboard/operations.json`. The initial task queue is evidence-derived: it
includes desired-state drift, missing or stale observations, persistent
filesystems at 85%/95% warning and critical thresholds, and failed systemd
services. Failed SMART health, NVMe critical warnings, exhausted endurance,
low spare capacity, and media errors become critical tasks. Tasks cannot be
acknowledged or executed in this first slice.

Public services have a separate evidence-derived lifecycle. Desired `Domain`
resources are planned; active referenced servers make them ready to deploy; a
successful Ansible receipt makes them deployed; server inspection establishes
whether the service and configuration are compliant; and DNS, edge, TLS,
origin, and public HTTP probes establish route health:

```console
task inspect STATE_DIR=state/examples
task services:inspect STATE_DIR=state/examples
task services:status STATE_DIR=state/examples
task dashboard STATE_DIR=state/examples
```

The equivalent direct status command is:

```console
uv run atlas services status state/examples \
  --observed tmp/observed \
  --service-observed tmp/observed-services \
  --deployments tmp/deployments
```

Missing receipts or observations remain visible as `no` or `unknown`; Atlas
does not infer deployment merely because a playbook exists.
