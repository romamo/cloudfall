# PROJECT_CONTEXT

## Project Goal

Build an **AI-native self-hosted platform** to manage and operate 10--20
standalone SaaS applications across a fleet of dedicated Debian servers
without Kubernetes.

The platform should expose a stable control plane that an AI agent can
use to provision servers, deploy components, manage secrets, monitor
health, perform rollbacks, and orchestrate infrastructure safely.

The AI should operate against structured platform state instead of
inventing shell commands or discovering infrastructure via SSH.

------------------------------------------------------------------------

## Requirements

### Infrastructure

-   5+ dedicated Debian servers
-   Mostly static infrastructure
-   Traditional long-lived servers (not immutable)
-   RAID1 during bootstrap
-   Manual failover with easy reassignment
-   Fixed infrastructure clusters
    -   MySQL
    -   Redis
    -   Elasticsearch

### Applications

Typical stack:

-   Python
-   uv
-   Gunicorn / Uvicorn
-   Celery
-   Node.js
-   PM2 (legacy where needed)
-   systemd

Applications consist of multiple components:

-   frontend
-   backend
-   workers
-   scheduler

Each component may deploy to one or more servers.

Projects are independent SaaS products.

Preferred repository model:

-   Separate repository per component
-   Monorepos must also be supported.

### Secrets

Move from scattered `.env` files to Infisical.

Hierarchy:

-   platform
-   project
-   component

Secrets generated into EnvironmentFile consumed by systemd.

### Logging

Current:

-   Filebeat
-   Elasticsearch
-   Kibana

Decision:

Keep operational logs separate from application search indexes.

Future preference:

-   Grafana
-   Loki
-   Prometheus

### Monitoring

Current:

-   Uptime Kuma

Future:

-   Prometheus
-   Grafana
-   Node Exporter
-   MySQL Exporter
-   Redis Exporter
-   Elasticsearch Exporter

### Deployments

Desired:

-   Artifact-based deployment
-   Releases
-   Symlink switching
-   Rollback support
-   Forever retention until cleanup policy

Approval:

Automatic or manual depending on project.

------------------------------------------------------------------------

## Architecture Decisions

### Overall

Human ↓ AI Agent ↓ Platform SDK ↓ Platform State (YAML) ↓ Execution
Engine ↓ Servers

### Repository Layout

Cloudfall starts as a monorepo with three architectural modules:

-   `state/` -- declarative configuration and JSON Schemas
-   `engine/` -- Ansible-based execution
-   `sdk/` -- the stable Python API consumed by agents and tooling

The modules must keep explicit boundaries even though they share a repository.
State contains no execution logic, the engine does not silently modify state,
and external entry points use the SDK instead of invoking Ansible directly.

The modules may be split into separate repositories later if independent
release cycles or access-control requirements make that necessary.

### Platform State

Contains only declarative configuration.

Suggested structure:

-   servers/
-   projects/
-   components/
-   clusters/
-   domains/
-   monitoring/
-   backups/
-   secrets/

### Platform Engine

Responsible only for execution.

Primary engine:

-   Ansible

Task runner:

-   Task

### Platform SDK

Stable API consumed by AI.

Future methods:

-   deploy()
-   rollback()
-   move()
-   health()
-   restart()
-   backup()
-   restore()
-   inventory()

Future MCP server should expose these operations.

------------------------------------------------------------------------

## Infrastructure Model

Servers are "all-fit-all".

Infrastructure clusters remain fixed.

Applications are movable.

Component assignment example:

crm-backend → h1,h2

crm-worker → h3

Later movable by changing platform state.

------------------------------------------------------------------------

## Project Model

Project composes components.

Example:

crm

-   frontend
-   backend
-   worker
-   scheduler

Project owns composition.

Component owns deployment.

------------------------------------------------------------------------

## Component Model

Each component defines:

-   repository
-   runtime
-   deployment strategy
-   servers
-   user
-   secrets
-   service
-   health endpoint

------------------------------------------------------------------------

## Deployment Strategy

Preferred:

Build artifact on management VPS.

Flow:

GitHub → Self-hosted GitHub Actions Runner → Build artifact → Store
temporarily → Ansible deploy → Health check

No builds on production servers.

Artifacts stored temporarily on management VPS until deployment
completes.

------------------------------------------------------------------------

## Directory Layout

Preferred:

/srv/apps/

project/

backend/ frontend/ worker/ shared/

Each project owns a Linux user.

Example:

User=crm

Components share project-owned files when appropriate.

Release model:

releases/ current -\> release

------------------------------------------------------------------------

## Networking

Cloudflare used where appropriate.

Let's Encrypt for non-proxied hosts.

Future load balancing:

Cloudflare proxy + Nginx.

HAProxy optional later.

------------------------------------------------------------------------

## Technologies

Core

-   Debian
-   systemd
-   Ansible
-   Task
-   JSON Schema
-   GitHub Actions (self-hosted runner)
-   GitHub
-   SSH

Application

-   Python
-   uv
-   Gunicorn
-   Uvicorn
-   Celery
-   Node.js

Secrets

-   Infisical

Monitoring

-   Uptime Kuma
-   Prometheus
-   Grafana
-   Loki

Logging

-   Filebeat
-   Elasticsearch
-   Kibana

Validation

-   JSON Schema
-   yamllint
-   ansible-lint
-   pre-commit
-   yq

------------------------------------------------------------------------

## Naming Decisions

Component naming:

crm-backend

Preferred over path-based naming.

Deployment approval:

Automatic OR manual depending on project.

Rollback retention:

Keep until cleanup policy removes old releases.

------------------------------------------------------------------------

## Implementation Status

This document captures the durable architecture. Live milestone tracking,
including what is implemented versus outstanding, lives in
[`ROADMAP.md`](ROADMAP.md) and is the authoritative source for status.

At a high level, the monorepo modules (`state/`, `engine/`, `sdk/`) and their
boundaries are established, and the platform implements state validation,
typed inventory, deterministic Ansible inventory rendering, read-only server
inspection, desired-versus-observed drift audit, the Debian baseline (bootstrap,
firewall, UTC time, unattended upgrades), a guarded Loki/Grafana/Alloy logging
stack, a service catalog (PostgreSQL and Nginx/TLS sites), the health-gated
deploy slice with artifact releases and symlink rollback, the `cloudfall-mcp`
server, the `render.yaml` blueprint importer, the resumable `cloudfall migrate`
orchestrator, and an evidence-derived operations dashboard.

The primary outstanding work is live-host validation on disposable Debian
targets and broadening the service catalog (Redis, MySQL, Elasticsearch, Node
runtimes) along the same patterns. See the roadmap for the per-milestone
breakdown and exit criteria.
