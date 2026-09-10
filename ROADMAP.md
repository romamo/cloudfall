# Roadmap

Cloudfall is built toward one complete story first:

> A SaaS running on a PaaS such as Render gets one fresh Debian bare-metal or
> VPS host, runs Cloudfall, and within an hour has: a hardened baseline, firewall,
> monitoring, logging, PostgreSQL, Nginx with TLS, its application deployed
> from GitHub with health checks and rollback, and a DNS cutover checklist.

Every milestone keeps the invariants that define Cloudfall: no action without
declared config, no status without evidence, no compliance without audit.

## M0 — Publishable base

- Public repository with license, CI, and contribution docs ✔
- Schemas, the validation CLI and Python API, audit pipeline, and generic
  engine roles ✔

## M1 — Layer 1: server baseline

One command turns an installed Debian host into a compliant Cloudfall server.

- Baseline packages, application users, `/srv/apps`, UTC enforcement, SSH
  hardening from declared `SshPublicKey` resources, unattended security
  upgrades ✔ (`task server:baseline`)
- nftables firewall role: default-deny inbound, allowances declared in the
  config, firewall evidence in observations and audit ✔
- Systemd timer units modeled and audited alongside services ✔
- Host metrics through the Loki/Grafana/Alloy stack ✔ — Alloy's embedded
  unix exporter pushes to a loopback Prometheus over the existing mTLS
  gateway, so no standalone `node_exporter` port is exposed

Exit: the baseline run is idempotent (second run reports no changes) and
`cloudfall audit` reports the host compliant, including firewall and timers.
**Met on 2026-09-08** on a disposable Hetzner Cloud Debian 13 server,
including live host metrics through the logging stack; see the
[Hetzner proving-run report](docs/proving-runs/2026-09-08-hetzner-m1.md) and
the earlier [container dry run](docs/proving-runs/2026-09-08-debian-container-m1.md).
Each run caught and fixed a real bug (a firewall-template rendering error and
an observation-schema rejection of escaped systemd unit names), which is the
point of proving runs. Bare-metal specifics (software RAID, storage
provisioning) remain unexercised.

## M2 — Layer 2: service catalog v1

Installable, audited infrastructure services, starting with exactly two.

- A `Service` schema: kind, version, servers, ports, bind policy, backup
  policy ✔
- PostgreSQL role: pinned install, localhost-only bind by default,
  per-application databases and users from the config (peer authentication,
  no database passwords), scheduled dumps, and a restore-proof command ✔
  (`task services:deploy`)
- Nginx site role: virtual hosts rendered from `Domain` resources, certbot
  with timer compliance, canonical client-address forwarding, and
  deployment receipts ✔ (`task domains:deploy`)
- Audit integration: packages, units, config hashes, and listening-socket
  evidence proving each declared service binds only to its loopback
  address ✔

Catalog breadth (Redis, RabbitMQ, Elasticsearch, MySQL, Node runtimes)
follows the same pattern afterward.

Exit: **met on 2026-09-08** on a disposable Hetzner Cloud Debian 13 server —
loopback-only PostgreSQL with a peer-authenticated application database, and
an Nginx route with a real Let's Encrypt certificate issued through the
role's webroot flow; see the
[M2/M3 proving-run report](docs/proving-runs/2026-09-08-hetzner-m2-m3.md).

## M3 — Layer 3: deploy slice

`deploy()` with rollback — the heart of one-command operation.

- Artifact builder on the management host (git ref → hashed tarball with
  schema-valid release metadata) ✔ (`task artifact:build`)
- Deploy role: digest-verified transfer, `releases/` unpacking, locked
  dependency materialization via pinned uv, environment-file installation,
  systemd unit from `Component.service`, health-check gate, symlink switch,
  automatic rollback on failed health checks, and release receipts ✔
  (`task deploy`)
- CLI and Python API operations `deploy()`, `rollback()`, `restart()`,
  `health()` with structured JSON results ✔ (`cloudfall deploy|rollback|restart|health`,
  executing through the engine's process boundaries; explicit rollback to
  any retained release included)
- Secrets v1: environment files generated from secret references (a local
  environment file per component is supported today; reference-driven
  generation pending)

Exit: the example backend component deploys end to end on a fresh host, and a
bad release rolls back automatically. **Met on 2026-09-08** on the same
disposable Hetzner host as the M2 exit: a uv-locked ASGI component deployed
through `cloudfall deploy` with a passing health gate, and a broken release
failed its gate and rolled back automatically with the service staying
healthy; see the
[M2/M3 proving-run report](docs/proving-runs/2026-09-08-hetzner-m2-m3.md).
The run fixed six engine/CLI bugs. The false origin warning it left open for
single-host topologies was fixed in the M4/M5 run: domain evidence now
records an explicit origin skip when origin and proxy share a host.

## M4 — Migration importer

- `cloudfall import render`: map `render.yaml` resources to Cloudfall
  applications, components, services, and domains, with environment files
  kept outside the config and a structured gap report for anything
  unmappable ✔
  (blueprint files; the Render API and cron jobs are pending)
- Data migration: managed-Postgres dump and restore with verification ✔
  (`cloudfall data migrate`, the `--data` step of `cloudfall migrate`, and
  the confirm-gated `migrate_database` MCP tool: per-table row-count
  verification, a non-empty-target refusal guard, secret-file hygiene, and
  a `DataMigrationReceipt`; proven live in the
  [guided data-migration report](docs/proving-runs/2026-09-08-guided-data-migration.md))
- Cutover generator: DNS TTL lowering, parallel-run verification, switch, and
  a rollback window (DNS steps surface in the import report today)
- v1 limitation: native Python and Node builds; container runtimes are a
  tracked decision ✔ (documented and enforced — container services land in
  the gap report)

Exit: a real PaaS application migrated onto one host with its data, TLS,
monitoring, and rollback path. **Met on 2026-09-08** in two stages: a
realistic blueprint migrated end to end
([M4/M5 report](docs/proving-runs/2026-09-08-hetzner-m4-m5.md)), then an
application actually hosted on Render — built by Render from this
repository, serving rows from its managed PostgreSQL — cut over with its
data to a byte-identical response on the Cloudfall host
([cutover report](docs/proving-runs/2026-09-08-render-cutover.md)), and
finally the TTL-lowered DNS record cutover itself on an owned domain, with
the migrate plan pausing at `dns-verify` and resuming after the flip
([DNS cutover report](docs/proving-runs/2026-09-08-dns-cutover.md)). Every
wedge step is now proven live, including the guided data migration.

## M5 — Agent layer

- MCP server over the Python API: read-only tools exposed freely; mutating tools
  gated by explicit confirmation and always writing receipts ✔
  (`cloudfall-mcp` with seventeen annotated tools covering import, artifact
  build, baseline/services/domains convergence, deploy/rollback/restart,
  and the full evidence pipeline)
- A `migrate` orchestrator chaining M1–M4 as a resumable plan ✔
  (`cloudfall migrate` and the `migrate` MCP tool: persisted step progress,
  a DNS-verification pause at the cutover moment, and a final evidence pass
  requiring a compliant audit and healthy routes)

Exit: an AI agent completes the M4 migration through MCP without shell access
to the servers. **Met on 2026-09-08**: an AI agent drove the blueprint import
and the full ten-step migration (baseline through the final evidence pass)
purely through `cloudfall-mcp` with the confirmation handshake, taking a
fresh Debian host to a TLS-served, audit-compliant application in about
seven minutes, and a later session resumed the persisted plan as a no-op —
see the
[M4/M5 proving-run report](docs/proving-runs/2026-09-08-hetzner-m4-m5.md).

## M6 — Next: from proven wedge to routine migrations

M0–M5 proved the wedge end to end. The next milestones make the migration
routine and widen what it can carry, in rough priority order:

- **Catalog breadth** — Redis, MySQL, Elasticsearch, RabbitMQ, and Node
  runtime services following the PostgreSQL pattern: pinned installs,
  loopback-only binds, backup policies, audited evidence
- **Render API import** — import live services and cron jobs through the
  Render API instead of requiring a `render.yaml` blueprint
- **Cutover generator** — TTL lowering, parallel-run verification, the DNS
  switch, and a rollback window generated as explicit plan steps rather
  than surfacing only in the import report
- **Secrets v2** — environment files generated from secret references in a
  central secrets manager, replacing locally maintained per-component files
- **Bare-metal provisioning** — software RAID and storage layout for
  dedicated servers per the
  [hybrid storage design](docs/hybrid-storage-design.md), the one part of
  the server model still unexercised live
- **Backup and restore as first-class operations** — declared backup
  policies with restore-proof commands on the CLI and MCP surface

Exit: a second real application migrated by someone other than the author,
using only the public documentation.

## M7 — Full observability: metrics and alerting

The prerequisite for everything after it: an operator acts on signals, and
without alerting there are no signals. Layered on the existing
Loki/Grafana/Alloy stack.

- **Per-service metrics** — exporters for each catalog service (PostgreSQL
  first) declared in config and audited like any other unit, flowing through
  the same mTLS gateway as host metrics
- **Alert rules from declared config** — thresholds and conditions as typed
  resources, rendered into the stack; an alert fires as evidence, not just a
  notification
- **Notification channels** — declared delivery (email/webhook to start),
  with alert delivery itself audited

Exit: an induced service failure on a proving host raises a declared alert
with evidence within one minute, and `cloudfall audit` reports alert-rule
compliance alongside everything else. **Met on 2026-09-10** on a disposable
Hetzner Cloud Debian 13 server: stopping PostgreSQL raised the declared
alert as pending in 28 seconds and firing at 86 seconds per its declared
`for: 1m` dampening, the declared webhook received the payload, the
mid-failure audit separated drift (dead listener) from status (firing rule,
compliant), and recovery returned the host to 20/20 compliant — see the
[M7 proving-run report](docs/proving-runs/2026-09-10-hetzner-m7.md). The run
found and fixed one real latency bug (Prometheus `evaluation_interval` was
stuck at its 1-minute default), which is the point of proving runs.

## M8 — Operator runtime: the always-on agent, propose mode

A persistent agent process that watches the alert stream and drift reports,
diagnoses, and proposes — the human confirms through the existing MCP
handshake. No autonomy yet: this milestone is the loop, not the license.

- **Operator daemon** — long-running process subscribed to alerts and
  scheduled drift checks, maintaining the fleet's observed state
- **Diagnose-and-propose** — for each incident, an evidence-backed diagnosis
  and a concrete proposed operation (existing CLI/MCP verbs only), delivered
  with the receipts that justify it
- **Every proposal receipted** — proposals, confirmations, executions, and
  outcomes all land in the same evidence pipeline as manual operations

Exit: an induced failure is detected, diagnosed, and remediated end to end
with the human contributing only a confirmation — no human diagnosis, no
shell access.

## M9 — Graduated autonomy

Autonomy is granted per operation class, earned by evidence, never global.
The order is fixed by the dependency chain: M7 provides the signals, M8 the
loop, M9 the license.

- **Level 2: autonomous for provenly reversible operations** — failed-deploy
  rollback, service restart, certificate renewal; executed without asking,
  receipt delivered after the fact, reversibility backed by the operation
  class's receipt history
- **Level 3: autonomy within declared policy** — bounds live in the same
  typed state as everything else (which operations, which components, quiet
  hours, rate limits); the operator refuses outside them
- **Confirm forever** — DNS cutover, data deletion, and database promotion
  remain behind explicit confirmation regardless of level

Exit: on a proving host, a deliberately broken release is rolled back
autonomously within declared policy — alert to healthy service with zero
human involvement — and the receipt trail shows detection, decision, action,
and verified outcome.
