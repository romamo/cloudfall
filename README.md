# Cloudfall

Cloudfall is an open-source, AI-native control plane for moving SaaS applications
off cloud PaaS platforms onto self-hosted bare metal or VPS servers — and
operating them there without Kubernetes.

The target workflow: take one fresh Debian host, run Cloudfall, and get a hardened
baseline, firewall, monitoring, logging, your infrastructure services, and
your application deployed with health checks and rollback. Then cut DNS and
stop paying PaaS margins. See the [roadmap](ROADMAP.md) for the milestone
plan; the wedge use case is a migration from Render onto a Hetzner-class
server.

> **Validated live through the deploy layer.** The server baseline,
> observability stack, service catalog, and deploy slice (M1–M3) were proven
> on 2026-09-08 on disposable Hetzner Cloud Debian 13 servers: idempotent
> convergence, compliant audits, loopback PostgreSQL with peer-auth
> databases, a real Let's Encrypt certificate on an Nginx route, health-gated
> deploys, and an automatic rollback of a bad release (see the
> [proving-run reports](docs/proving-runs/)). The Render importer and the
> `migrate` orchestrator (M4–M5) are exercised by tests and CI but not yet
> against a real server — expect rough edges there until they join the
> proving batch (see the [roadmap](ROADMAP.md))

## Why Cloudfall

- **Declarative and auditable.** Servers, applications, components, and
  domains are declared in typed YAML config validated against JSON Schemas.
  A read-only inspection pipeline collects evidence from hosts, and
  `cloudfall audit` reports drift between the config and the observed servers
  with distinct exit codes
- **Evidence over inference.** Deployments produce receipts; status is
  derived from validated observations. Cloudfall never reports success it cannot
  prove
- **AI-agent native.** Agents operate through a stable CLI and Python API
  with structured JSON results instead of inventing shell commands over SSH. The
  `cloudfall-mcp` server exposes read-only evidence tools freely and gates
  every server-changing tool behind an explicit confirmation handshake
- **systemd, not containers.** Applications run as native systemd services
  with artifact releases and symlink rollback on long-lived Debian servers

## Architecture

Three modules with strict boundaries:

- [`state/`](state/README.md) — the config: declarative YAML resources and
  their JSON Schemas
- [`sdk/`](sdk/README.md) — the `cloudfall` CLI and Python API used by
  agents and tooling
- [`engine/`](engine/README.md) — internal execution machinery (Ansible-based)

Rules: the config contains no execution logic, the CLI reads and validates
the config without Ansible internals, the engine never silently rewrites the
config, and entry points call the CLI rather than Ansible directly. Mutating
operations execute through the engine's command-line and playbook contracts,
never its internals. See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the
full architecture and the long-term fleet vision.

## Status

Cloudfall is pre-1.0. Implemented today: config validation, typed inventory,
deterministic Ansible inventory rendering, read-only server inspection,
config-versus-observed drift audit, a Debian bootstrap role, a UTC time
baseline, an nftables firewall, a guarded Loki/Grafana/Alloy logging stack,
an evidence-derived operations dashboard, a service catalog (PostgreSQL and
Nginx/TLS sites), the health-gated deploy slice with artifact releases and
symlink rollback, the `cloudfall-mcp` server, the `render.yaml` blueprint
importer, and the resumable `cloudfall migrate` orchestrator. Live-host
validation on disposable Debian targets and broader catalog breadth (Redis,
MySQL, Elasticsearch, Node runtimes) are the next milestones. See the
[roadmap](ROADMAP.md) for the milestone plan.

## Quickstart

Cloudfall requires Python 3.14 and uses [`uv`](https://docs.astral.sh/uv/):

```console
uv run cloudfall state validate state/examples
```

The command validates every YAML config document against the v1 JSON Schemas
and then checks cross-resource references. Successful and failed results are
emitted as structured JSON.

Show the non-secret platform inventory and render it as deterministic Ansible
JSON:

```console
uv run cloudfall inventory show state/examples
uv run cloudfall-engine inventory render state/examples
```

## Migrate from Render

Map a `render.yaml` blueprint onto Cloudfall config:

```console
uv run cloudfall import render render.yaml --project acme --server h1
```

Web, private, and worker services become `Component` resources (workers use
a service-active health gate instead of a fabricated HTTP check), managed
PostgreSQL becomes a `Service` with application-owned peer-authentication
databases, and custom domains become TLS-required `Domain` routes. Each web
component receives an explicit listen port written as `PORT` into an
environment file outside the config directory — the config never contains
secret values. The full walkthrough, including the Render-to-Cloudfall name
mapping, is in the [Render migration guide](docs/render-migration-guide.md).

The importer never guesses silently. `IMPORT-REPORT.md` records everything
that was not imported (cron, static, and container services today), every
assumption it made, and every action required before cutover, including data
migration and DNS steps. Merge the emitted fragment with your servers and
server types, validate, then drive the whole migration with one resumable
plan:

```console
uv run cloudfall migrate state/production --build acme-api=main
uv run cloudfall migrate state/production --build acme-api=main --yes
```

Without `--yes` the command shows the plan; with it, the plan executes step
by step — baseline, services, artifact builds, health-gated deployments,
HTTP routes, a DNS-verification pause at the cutover moment, TLS issuance,
and a final evidence pass that requires a compliant audit and healthy
routes. Progress persists after every step, so a failed step or the DNS
pause resumes exactly where it stopped.

## Inspect servers and audit drift

Each `Server` references a reusable server type (a `HostProfile` resource)
describing its required Debian version, software RAID, filesystem capacity,
packages, systemd services, and allowlisted configuration evidence.

Collect a read-only snapshot from every reachable server, then compare it
with the config:

```console
task inspect
task audit
```

Snapshots are written to `tmp/observed/<server>.json` with mode `0600` and
never include configuration-file contents. The audit emits one JSON report:
exit code `0` compliant, `1` drift, `2` invalid input, `3` compliance unknown.
Task workflows are defined in [`Taskfile.yml`](Taskfile.yml); direct `uv run`
equivalents exist for every task.

## Operations dashboard

Cloudfall renders validated config, host observations, and audit results into
a local read-only dashboard with an evidence-derived task queue and a public-service
lifecycle (planned → ready → deployed → configured → healthy):

```console
task dashboard
```

The build writes `tmp/dashboard/index.html` and machine-readable
`tmp/dashboard/operations.json`. Missing receipts or observations remain
visible as `no` or `unknown`; Cloudfall does not infer deployment merely because
a playbook exists.

## Logging and storage guides

- A guarded parallel logging slice deploys Loki, loopback-only Grafana, an
  mTLS ingestion gateway, and Alloy without touching legacy agents: see the
  [logging service guide](docs/logging-service-guide.md)
- New two-drive servers use a RAID1 system area plus independent storage
  tails: review the [hybrid storage design](docs/hybrid-storage-design.md)
  and the destructive, new-server-only
  [storage provisioning guide](docs/new-server-storage-guide.md)

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).

## License

Cloudfall is licensed under the [GNU AGPL-3.0-or-later](LICENSE). Commercial
licensing exceptions are available from the copyright holder.
