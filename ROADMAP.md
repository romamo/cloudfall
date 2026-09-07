# Roadmap

Cloudfall is built toward one complete story first:

> A SaaS running on a PaaS such as Render gets one fresh Debian bare-metal or
> VPS host, runs Cloudfall, and within an hour has: a hardened baseline, firewall,
> monitoring, logging, PostgreSQL, Nginx with TLS, its application deployed
> from GitHub with health checks and rollback, and a DNS cutover checklist.

Every milestone keeps the invariants that define Cloudfall: no action without
declared state, no status without evidence, no compliance without audit.

## M0 — Publishable base

- Public repository with license, CI, and contribution docs ✔
- Schemas, validation SDK, audit pipeline, and generic engine roles ✔

## M1 — Layer 1: server baseline

One command turns an installed Debian host into a compliant Cloudfall server.

- Baseline packages, project users, `/srv/apps`, UTC enforcement, SSH
  hardening from declared `SshPublicKey` resources, unattended security
  upgrades ✔ (`task server:baseline`)
- nftables firewall role: default-deny inbound, allowances declared in state,
  firewall evidence in observations and audit ✔
- Systemd timer units modeled and audited alongside services ✔
- Host metrics through the Loki/Grafana/Alloy stack ✔ — Alloy's embedded
  unix exporter pushes to a loopback Prometheus over the existing mTLS
  gateway, so no standalone `node_exporter` port is exposed

Exit: the baseline run is idempotent (second run reports no changes) and
`cloudfall audit` reports the host compliant, including firewall and timers.
Remaining before exit: a live idempotence and observability run on a
disposable host.

## M2 — Layer 2: service catalog v1

Installable, audited infrastructure services, starting with exactly two.

- A `Service` schema: kind, version, servers, ports, bind policy, backup
  policy ✔
- PostgreSQL role: pinned install, localhost-only bind by default,
  per-project databases and users from state (peer authentication, no
  database passwords), scheduled dumps, and a restore-proof command ✔
  (`task services:deploy`)
- Nginx site role: virtual hosts rendered from `Domain` resources, certbot
  with timer compliance, canonical client-address forwarding
- Audit integration: packages, units, config hashes, listening-socket
  evidence

Catalog breadth (Redis, RabbitMQ, Elasticsearch, MySQL, Node runtimes)
follows the same pattern afterward.

## M3 — Layer 3: deploy slice

`deploy()` with rollback — the heart of one-command operation.

- Artifact builder on the management host (git ref → build → tarball with
  release metadata)
- Deploy role: `releases/` unpacking, environment-file rendering, systemd
  unit from `Component.service`, health-check gate, symlink switch, automatic
  rollback on failed health checks, deployment receipts
- SDK methods `deploy()`, `rollback()`, `restart()`, `health()` with
  structured JSON results
- Secrets v1: environment files generated from secret references

Exit: the example backend component deploys end to end on a fresh host, and a
bad release rolls back automatically.

## M4 — Migration importer

- `cloudfall import render`: map `render.yaml` / Render API resources to Cloudfall
  projects, components, cron jobs, and services, with a gap report for
  anything unmappable
- Data migration: managed-Postgres dump and restore with verification
- Cutover generator: DNS TTL lowering, parallel-run verification, switch, and
  a rollback window
- v1 limitation: native Python and Node builds; container runtimes are a
  tracked decision

Exit: a real PaaS application migrated onto one host with its data, TLS,
monitoring, and rollback path.

## M5 — Agent layer

- MCP server over the SDK: read-only tools exposed freely; mutating tools
  gated by explicit confirmation and always writing receipts
- A `migrate` orchestrator chaining M1–M4 as a resumable plan

Exit: an AI agent completes the M4 migration through MCP without shell access
to the servers.
