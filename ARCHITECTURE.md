# Architecture

Cloudfall is built wedge-first: take a SaaS off a cloud PaaS such as Render and
run it on one fresh Debian host with a hardened baseline, monitoring, deploys,
and rollback. The architecture, however, is designed from day one for the
larger goal behind the wedge: an AI agent safely operating 10–20 standalone
SaaS applications across a fleet of dedicated Debian servers without
Kubernetes. This document describes the durable design shared by both stories;
[`ROADMAP.md`](ROADMAP.md) is the authoritative source for what is implemented
today.

Every layer keeps the same invariants: no action without declared state, no
status without evidence, no compliance without audit.

## Control plane stack

```text
Human → AI agent → Platform SDK → Platform state (YAML) → Execution engine → Servers
```

The agent operates against structured platform state through a stable SDK
instead of inventing shell commands or discovering infrastructure over SSH.
Every mutating operation goes through the engine's explicit playbook
contracts and produces receipts; status is derived from validated
observations, never inferred.

## Module boundaries

Cloudfall is a monorepo with three architectural modules:

- [`state/`](state/README.md) — declarative configuration and JSON Schemas
- [`sdk/`](sdk/README.md) — the stable Python API consumed by agents and tooling
- [`engine/`](engine/README.md) — Ansible-based execution of explicit plans

The boundaries are strict even inside one repository: state contains no
execution logic, the SDK reads and validates state without Ansible internals,
the engine never silently rewrites state, and external entry points call the
SDK rather than Ansible directly. Mutating SDK operations execute through the
engine's command-line and playbook contracts, never its internals. The modules
may split into separate repositories later if independent release cycles or
access control require it.

## Platform state

State is declarative configuration only, organized as typed resources
validated against versioned JSON Schemas: servers, host profiles, projects,
components, services, domains, logging stacks, and SSH public keys.

State never contains secret values; schemas and validation reject them.
Secrets exist as references, materialized into environment files outside the
state directory and consumed by systemd via `EnvironmentFile`.

## Project and component model

A project is an independent SaaS product that composes components; a
component (frontend, backend, worker, scheduler) owns its own deployment.
Components are named `crm-backend` style rather than by path, and each may
deploy to one or more servers.

On disk, each project owns a Linux user and a `/srv/apps/<project>/`
directory with one subdirectory per component. Releases unpack into
`releases/` and a `current` symlink points at the active release, which is
what makes rollback a symlink switch.

Components come from one repository each by preference, with monorepos also
supported.

## Deployment strategy

Artifacts are built on the management host, never on production servers:

```text
Git ref → build artifact (hashed tarball + release metadata) → digest-verified
transfer → unpack into releases/ → locked dependency materialization →
environment file → systemd unit → health-check gate → symlink switch
```

A failed health check triggers automatic rollback to the previous release.
Releases are retained until a cleanup policy removes them, so explicit
rollback to any retained release stays available. Deployment approval is
automatic or manual per project.

## Supported workloads

Applications run as native systemd services on long-lived Debian hosts, not
in containers. The primary stacks are Python (uv, Gunicorn/Uvicorn, Celery)
and Node.js; anything that runs as a systemd service with a health endpoint
fits the model.

## Infrastructure model

Servers are traditional long-lived Debian hosts (not immutable), bootstrapped
with RAID1, described by reusable host profiles, and treated as "all-fit-all":

- Infrastructure services (PostgreSQL today; Redis, MySQL, Elasticsearch, and
  friends as the catalog grows) stay pinned to declared servers
- Application components are movable: reassigning `crm-backend` from `h1,h2`
  to `h3` is a state change followed by convergence
- Failover is manual with easy reassignment rather than automated
  orchestration

## Networking

Nginx terminates HTTP with virtual hosts rendered from `Domain` resources and
Let's Encrypt TLS. Cloudflare proxying is used where appropriate, and future
load balancing is Cloudflare plus Nginx, with HAProxy optional later.

## Long-term vision: fleet operation

The wedge proves the model on one host. The same state, audit, and deploy
machinery is designed to extend to fleet operation without architectural
change:

- **Catalog breadth** — Redis, MySQL, Elasticsearch, RabbitMQ, and Node
  runtime services following the PostgreSQL pattern: pinned installs,
  loopback-only binds, backup policies, audited evidence
- **Multi-server assignment** — components spread across servers and moved by
  editing state
- **Secrets manager integration** — environment files generated from a
  central secrets manager instead of locally maintained files, keeping the
  platform/project/component hierarchy
- **Fleet observability** — per-service exporters and alerting layered on the
  existing Loki/Grafana/Alloy stack
- **Backup and restore** — declared backup policies with restore-proof
  commands as first-class SDK operations

## Status

Implementation status, milestone exit criteria, and what remains before the
wedge is proven live are tracked in [`ROADMAP.md`](ROADMAP.md).
