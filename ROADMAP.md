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
The run fixed six engine/CLI bugs; the remaining known gap is a false origin
warning from `services status` on single-host topologies.

## M4 — Migration importer

- `cloudfall import render`: map `render.yaml` resources to Cloudfall
  applications, components, services, and domains, with environment files
  kept outside the config and a structured gap report for anything
  unmappable ✔
  (blueprint files; the Render API and cron jobs are pending)
- Data migration: managed-Postgres dump and restore with verification
  (currently a required-action item in the import report; a guided playbook
  is pending)
- Cutover generator: DNS TTL lowering, parallel-run verification, switch, and
  a rollback window (DNS steps surface in the import report today)
- v1 limitation: native Python and Node builds; container runtimes are a
  tracked decision ✔ (documented and enforced — container services land in
  the gap report)

Exit: a real PaaS application migrated onto one host with its data, TLS,
monitoring, and rollback path.

## M5 — Agent layer

- MCP server over the Python API: read-only tools exposed freely; mutating tools
  gated by explicit confirmation and always writing receipts ✔
  (`cloudfall-mcp` with fifteen annotated tools covering import, artifact
  build, baseline/services/domains convergence, deploy/rollback/restart,
  and the full evidence pipeline)
- A `migrate` orchestrator chaining M1–M4 as a resumable plan ✔
  (`cloudfall migrate` and the `migrate` MCP tool: persisted step progress,
  a DNS-verification pause at the cutover moment, and a final evidence pass
  requiring a compliant audit and healthy routes)

Exit: an AI agent completes the M4 migration through MCP without shell access
to the servers. The tool surface for that exit exists; the live run joins
the proving batch.
