# Render migration guide

This guide walks a Render Blueprint (`render.yaml`) application onto one
self-hosted Debian server with Cloudfall. It covers what maps automatically,
what needs manual work, and the vocabulary differences between the two
platforms.

> **Status:** the importer and the `migrate` orchestrator are exercised by
> tests and CI; the end-to-end live migration is still in the proving batch
> (see the [roadmap](../ROADMAP.md)). Expect rough edges and read the import
> report carefully.

## Name mapping

Render and Cloudfall describe the same things with different words:

| Render | Cloudfall |
|---|---|
| Blueprint (`render.yaml`) | Config: YAML resources under your config directory |
| Web service (`type: web`) | Component with an HTTP health check and an assigned listen port |
| Private service (`type: pserv`) | Component with a listen port and no public domain |
| Background worker (`type: worker`) | Component with a service-active health gate (no fabricated HTTP check) |
| Managed PostgreSQL (`databases:`) | `Service` of kind PostgreSQL: localhost-only bind, application-owned databases, peer authentication, no database passwords |
| Custom domain | `Domain` resource: Nginx virtual host plus Let's Encrypt TLS |
| Environment variables and env groups | Environment files kept outside the config directory; the config never contains secret values |
| Instance type / plan | Your server (VPS or bare metal) plus its server type (`ServerType`) |
| Deploy from GitHub | `cloudfall deploy`: artifact built from a git ref, health-check gate, symlink switch, automatic rollback |
| Render dashboard | Local operations dashboard (`task dashboard`) |
| Service (Render's umbrella term) | Split into two concepts: infrastructure **services** (PostgreSQL, Nginx) and application **components** |

Not imported today; each lands in the gap report instead of being guessed at:

| Render | What to do instead |
|---|---|
| Cron job (`type: cron`) | Recreate the schedule as a systemd timer; timers are modeled and audited |
| Static site (`type: static`) | Serve via an Nginx site manually for now |
| Redis / key-value (`type: redis`, `keyvalue`) | Not in the v1 service catalog yet |
| Docker runtime (`runtime: docker` / `image`) | Not supported in v1; native Python and Node builds only |

## What you need

- A fresh Debian 13 server (a Hetzner Cloud VPS is the proven reference)
  with root SSH access
- Your `render.yaml` blueprint and access to your application repositories
- Python 3.14 and [`uv`](https://docs.astral.sh/uv/) on the machine you run
  Cloudfall from (your workstation works; no management server is required)

## Step 1: import the blueprint

```console
uv run cloudfall import render render.yaml --application acme --server h1
```

No `render.yaml`? Import the live workspace straight from the Render API
instead — put your API key alone into a file and run:

```sh
uv run cloudfall import render-api --api-key-file ~/.render/api-key \
  --application acme --server h1
```

The API path discovers services, workers, cron jobs, and managed PostgreSQL
(including its real major version), reads the actual environment variable
values, and rewrites any value that matches a managed database's connection
string to the local peer-authenticated socket URL. Secret values the API
does not expose are left empty in the environment file with an
action-required gap entry. Both paths share the same mapping and the same
gap report.

This maps every supported Render service onto Cloudfall config: web,
private, and worker services become components, managed PostgreSQL becomes
a PostgreSQL service with application-owned databases, and custom domains
become TLS-required domain routes.

Each web and private component is assigned an explicit listen port (starting
at 8100), written as `PORT` into that component's environment file. Your
application must honor `PORT`, exactly as it does on Render.

## Step 2: read the import report

The importer never guesses silently. `IMPORT-REPORT.md` records:

- everything that was **not** imported (cron, static, key-value, and
  container services), with a reason per entry
- every assumption it made (assigned ports, health checks, runtimes)
- every action required before cutover, including data migration and DNS
  steps

Treat the required-actions list as your migration checklist.

## Step 3: complete the config

Merge the emitted fragment with the resources the blueprint cannot know
about: your `Server` and its server type (`ServerType`). Then validate:

```console
uv run cloudfall config validate config/production
```

## Step 4: fill in environment files

The importer creates environment files outside the config directory,
including any `envVarGroups` from the blueprint. Copy the real secret values
from the Render dashboard into these files; secrets never enter the config,
and validation rejects them if they do.

## Step 5: run the migration plan

```console
uv run cloudfall migrate config/production --build acme-api=main
uv run cloudfall migrate config/production --build acme-api=main --yes
```

Without `--yes` the command shows the plan. With it, the plan executes step
by step: server baseline and firewall, infrastructure services, artifact
builds, health-gated deployments, HTTP routes, a DNS-verification pause at
the cutover moment, TLS issuance, and a final evidence pass requiring a
compliant audit and healthy routes. Progress persists after every step, so
a failure or the DNS pause resumes exactly where it stopped.

## Step 6: migrate the data

Managed-Postgres dump and restore is currently a required-action item in the
import report rather than an automated step: dump from Render's managed
database, restore into the Cloudfall-managed PostgreSQL, and verify row
counts before cutting over. A guided playbook is on the roadmap.

## Step 7: cut over DNS

The migrate plan pauses at the cutover moment. Lower DNS TTLs ahead of time,
point your domains at the new server, verify the routes, then resume the
plan; it finishes with TLS issuance and the final audit.

## After the migration

- `task audit` proves the server matches the config, with distinct exit
  codes for drift
- `task dashboard` renders the evidence-derived operations dashboard
- `cloudfall deploy` / `rollback` / `restart` / `health` replace Render's
  deploy buttons, with structured JSON results
