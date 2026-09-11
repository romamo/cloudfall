# Architecture

Cloudfall is built wedge-first: take a SaaS off a cloud PaaS such as Render and
run it on one fresh Debian host with a hardened baseline, monitoring, deploys,
and rollback. The architecture, however, is designed from day one for the
larger goal behind the wedge: an AI agent safely operating 10–100 standalone
SaaS applications across a fleet of dedicated Debian servers without
Kubernetes (see the [fleet goals](docs/fleet-goals.md)). This document
describes the durable design shared by both stories;
[`ROADMAP.md`](ROADMAP.md) is the authoritative source for what is implemented
today.

Every layer keeps the same invariants: no action without declared config, no
status without evidence, no compliance without audit.

## Control plane stack

```text
Human / AI agent ──edits──► Config (YAML)
Human / AI agent ──runs───► CLI / Python API ──► Execution engine ──► Servers
```

The config is a store, not a pipeline stage: humans and agents edit it
directly, the CLI validates and reads it, and the engine converges servers
to it. The agent operates against structured config through a stable CLI
and Python API instead of inventing shell commands or discovering
infrastructure over SSH. Every mutating operation goes through the engine's
explicit playbook contracts and produces receipts; status is derived from
validated observations, never inferred.

## Module boundaries

Cloudfall is a monorepo with three architectural modules:

- [`config/`](config/README.md) — the config: declarative YAML resources and
  their JSON Schemas
- [`sdk/`](sdk/README.md) — the `cloudfall` CLI and Python API consumed by
  agents and tooling
- [`engine/`](engine/README.md) — internal execution machinery: Ansible-based
  execution of explicit plans

The boundaries are strict even inside one repository: the config contains no
execution logic, the CLI reads and validates the config without Ansible
internals, the engine never silently rewrites the config, and external entry
points call the CLI rather than Ansible directly. Mutating operations execute
through the engine's command-line and playbook contracts, never its
internals. The modules may split into separate repositories later if
independent release cycles or access control require it.

## Config

The config is declarative only, organized as typed resources validated
against versioned JSON Schemas: servers, server types, applications,
components, services, domains, logging stacks, alert rules, operator
policy, and SSH public keys.

The config never contains secret values; schemas and validation reject them.
Secrets exist as references, rendered from a sops/age-encrypted secrets
directory into environment files outside the config directory and consumed
by systemd via `EnvironmentFile`.

## Application and component model

An application is an independent SaaS
product that composes components; a component (frontend, backend, worker,
scheduler) owns its own deployment. Components are named `crm-backend` style
rather than by path, and each may deploy to one or more servers.

On disk, each application owns a Linux user and a `/srv/apps/<application>/`
directory with one subdirectory per component. Releases unpack into
`releases/` and a `current` symlink points at the active release, which is
what makes rollback a symlink switch.

Components come from one repository each by preference, with monorepos also
supported.

## Deployment strategy

Artifacts are built on the management host — the machine running Cloudfall,
which is a workstation for one-off migrations or a small always-on host
once the operator runs persistently — never on production servers:

```text
Git ref → build artifact (hashed tarball + release metadata) → digest-verified
transfer → unpack into releases/ → locked dependency materialization →
environment file → systemd unit → health-check gate → symlink switch
```

A failed health check triggers automatic rollback to the previous release.
Releases are retained until a cleanup policy removes them, so explicit
rollback to any retained release stays available. Deployment approval is
automatic or manual per application.

## Supported workloads

Applications run as native systemd units on long-lived Debian hosts, not
in containers. The primary stacks are Python (uv, Gunicorn/Uvicorn, Celery)
and Node.js; anything that runs as a systemd service with a health endpoint
fits the model.

## Infrastructure model

Servers are traditional long-lived Debian hosts (not immutable), described
by reusable `ServerType` resources; bare-metal server types
add software RAID1 and the hybrid storage layout, while cloud VPS types do
not. Any server can run any declared service or component:

- Infrastructure services (PostgreSQL and Redis today; MySQL, Elasticsearch,
  and friends as the catalog grows) stay pinned to declared servers
- Application components are movable: reassigning `crm-backend` from `h1,h2`
  to `h3` is a config change followed by convergence
- Failover is manual with easy reassignment rather than automated
  orchestration; the planned PostgreSQL high-availability formation (M14 on
  the roadmap) amends this at the database layer only, where a standby may
  be promoted automatically but an operator never initiates a promotion
  without explicit confirmation

## Networking

Nginx terminates TLS with virtual hosts rendered from `Domain` resources and
Let's Encrypt certificates. Cloudflare proxying is used where appropriate,
and future load balancing is Cloudflare plus Nginx, with HAProxy optional
later.

## Operations layer

Operating servers is the same evidence discipline running continuously:

- **Alerting** — alert rules and notification channels are typed config
  rendered into the Loki/Grafana/Alloy stack; a firing alert is evidence,
  exposed over a read-only mTLS route on the same gateway that receives
  logs and metrics
- **Operator** — an always-on management-host process watches alerts and
  scheduled drift audits, writes every diagnosis as a schema-validated
  proposal receipt (trigger evidence, diagnosis, exact operation, outcome),
  and executes only existing engine entry points
- **Graduated autonomy** — autonomy is granted per operation class by a
  declared `OperatorPolicy` and earned by verified receipt history, never
  globally; DNS cutover, data deletion, and database promotion stay behind
  explicit confirmation regardless of autonomy level

## Long-term vision: fleet operation

The wedge proves the model on one host. The same config, audit, and deploy
machinery is designed to extend to fleet operation without architectural
change; the [fleet goals](docs/fleet-goals.md) state the requirements this
must deliver and their current status:

- **Catalog breadth** — MySQL, Elasticsearch, RabbitMQ, and Node runtime
  services following the PostgreSQL and Redis pattern: pinned installs,
  loopback-only binds, backup policies, audited evidence
- **Multi-server assignment at scale** — components spread across many
  servers and moved by editing the config; the model exists, fleet-scale
  operation is the unproven part
- **Managed secrets backends** — the sops/age provider boundary admits a
  central secrets manager later, keeping the platform/application/component
  scope hierarchy
- **Fleet observability** — per-service exporters beyond PostgreSQL and
  Redis, and alert rules spanning more than one host
- **PostgreSQL high availability** — a declared replication, failover, and
  point-in-time recovery (M13) and failover formation (M14 on the roadmap)

## Status

Implementation status, milestone exit criteria, and what remains before the
wedge is proven live are tracked in [`ROADMAP.md`](ROADMAP.md).
