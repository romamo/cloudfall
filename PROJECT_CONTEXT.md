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

The monorepo foundation and first executable state-validation slice are in
place.

Monorepo modules:

`state/` `engine/` `sdk/`

Implemented v1 schemas:

server.schema.json host-profile.schema.json project.schema.json
component.schema.json observed-server.schema.json

The SDK loads YAML, validates it against JSON Schema Draft 2020-12, checks
cross-resource references, and exposes a typed read-only index. The
`cloudfall state validate` command emits structured success and error responses.

The SDK also exposes typed non-secret inventory projections and placement
queries. The engine renders those projections as deterministic Ansible JSON with
server host variables and environment, project, and component groups.

Servers reference typed HostProfile resources. A read-only Ansible inspection
role collects normalized OS, hardware, block-device, filesystem, software RAID,
package, service, and allowlisted configuration metadata/hash evidence into
local JSON snapshots. The SDK validates those snapshots before comparing them
with desired profiles. `cloudfall audit` emits structured per-check drift reports
and distinct exit codes for compliant, drifted, invalid, and unknown results.

An initial `cloudfall_bootstrap` role prepares an already-installed Debian system by
installing baseline packages, creating project service accounts, and creating
the `/srv/apps` project/component directory tree. Syntax validation and the
ansible-lint production profile pass; live-host validation remains outstanding.

The first replacement-logging slice is also implemented for a parallel pilot.
`LoggingStack` state declares one loopback Loki/Grafana backend, an mTLS-only
ingestion gateway, Alloy collectors, bounded retention, package pins, and
explicit journal/file sources. The playbook validates native configurations
and never changes legacy agents. No production stack is declared and live-host
validation remains outstanding.

------------------------------------------------------------------------

## Current Progress

Completed:

-   Overall architecture
-   Deployment model
-   Infrastructure model
-   Project/component abstraction
-   Secrets strategy
-   Monitoring direction
-   Logging strategy
-   Platform layering
-   AI-first control plane
-   Monorepo structure and module boundaries
-   Server, project, and component v1 schemas
-   HostProfile and ObservedServer v1 schemas
-   Valid and deliberately invalid state fixtures
-   Read-only state validation SDK and CLI
-   Typed SDK inventory and component/server placement queries
-   Deterministic Ansible JSON inventory renderer
-   Read-only Debian server observation collector
-   Desired-versus-observed RAID, mount, package, service, and config audit
-   Debian bootstrap Ansible role and Task workflows
-   Strict test, type-check, and lint configuration
-   Artifact deployment decision
-   User/directory conventions
-   Rollback strategy
-   Guarded parallel Loki/Grafana/Alloy logging pilot

Architecture is approximately 95% complete.

------------------------------------------------------------------------

## Remaining Tasks

1.  Add cluster and artifact schemas when required by real state
2.  Validate bootstrap idempotence on a disposable Debian host
3.  Integrate Infisical references and environment-file generation
4.  Add artifact builder and release metadata
5.  Deploy first backend component with health checks and rollback
6.  Add GitHub Actions runner
7.  Implement MCP server

------------------------------------------------------------------------

## Recommended Next Steps

Immediate priorities:

1.  Model current infrastructure requirements in `Server` and `HostProfile`
    resources.

2.  Run `task inspect` to capture current server evidence, followed by
    `task audit` to establish the first drift baseline.

3.  Extend schemas only where the real infrastructure exposes a missing
    concept.

4.  Run the bootstrap role twice on a disposable Debian host and verify the
    second run reports no changes.

5.  Deploy a single backend component end-to-end.

Success milestone:

A brand-new Debian server can be provisioned and one backend component
deployed using a single high-level command through the platform.
