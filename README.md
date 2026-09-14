# Cloudfall

Cloudfall is an open-source control plane that moves your applications and
databases off cloud PaaS onto one or two servers you own, plus an always-on
AI operator that runs them there. Every action produces a receipt; Cloudfall
never reports success it cannot prove. No Kubernetes.

It is built for founders and small teams at the scaling stage. The month
your cloud bill crosses $1,000 you are paying roughly $12,000 a year for
compute and managed databases that fit on one or two Hetzner-class servers
at a tenth of the price. Cloudfall's job is to make leaving safe: the
operational feel of a PaaS on hardware you own, with health-gated deploys,
rollback, monitoring, and backups that provably restore.

The target workflow: take one fresh Debian host, run Cloudfall, and get a
hardened baseline, firewall, monitoring, logging, PostgreSQL and Redis, and
your application deployed with health checks and rollback. Then cut DNS and
stop paying margins. The wedge use case is a migration from Render onto a
Hetzner-class server; see the [roadmap](ROADMAP.md) for the milestone plan
and the [manifesto](MANIFESTO.md) for the principles behind the project.

> **Proven live, not promised.** Everything implemented has run on
> disposable Hetzner Cloud Debian 13 servers: an application actually hosted
> on Render was cut over with its data and a real TTL-lowered DNS flip on an
> owned domain, a deliberately bad release was rolled back automatically
> along the way, an induced failure was remediated by the operator first
> with a single human approval and then autonomously under declared policy,
> a timer-driven drill proved a real backup restorable, and an alert crossed
> the public network to a second machine. The entire migration was driven by
> an AI agent through `cloudfall-mcp` alone. Full reports:
> [proving-runs](docs/proving-runs/). The one layer not yet exercised live
> is bare-metal RAID/storage provisioning (see the [roadmap](ROADMAP.md))

## Why Cloudfall

- **The bill buys computers, not margins.** A steady $1,000/mo cloud bill is
  mostly margin on commodity compute and managed databases. On servers you
  own, that difference becomes runway, every year
- **Evidence over inference.** Deployments produce receipts; status is
  derived from validated observations, and `cloudfall audit` reports drift
  between the declared config and the observed servers with distinct exit
  codes. Cloudfall never reports success it cannot prove
- **Good-enough databases, with proof.** Managed PostgreSQL is mostly
  insurance. Cloudfall keeps the coverage and drops the premium: loopback
  PostgreSQL and Redis with receipted backups and timer-driven restore
  drills that prove a real backup restorable instead of assuming it
- **An operator, not a pager.** The always-on operator watches declared
  alerts and audited drift, remediates what its receipts prove reversible
  under declared policy, and asks you only about what can't be undone
- **AI-agent native.** Agents operate through a stable CLI and Python API
  with structured JSON results instead of inventing shell commands over SSH.
  The `cloudfall-mcp` server exposes read-only evidence tools freely and
  gates every server-changing tool behind an explicit confirmation handshake
- **Boring on purpose.** Applications run as native systemd services with
  artifact releases and symlink rollback, behind nginx, on stock Debian. Any
  Linux admin can take over the server without Cloudfall installed

## Architecture

Three modules with strict boundaries:

- [`config/`](config/README.md) — the config: declarative YAML resources and
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

What you run all of this against is a **project**: one directory, usually a
private repository, holding your fleet (servers, server types, SSH public
keys), your applications (applications, components, services, domains), the
declarations that operate them (alert rules, operator policies, logging
stacks), and a `pyproject.toml` pinning the Cloudfall commit it runs with.
Every `cloudfall` command runs inside a project: the current directory when
it is one, or the directory named by `--project` or by the
`CLOUDFALL_PROJECT` environment variable. Relative paths, including the
`tmp/` defaults, resolve against the project, as if the command had been
started there.

## Status

Cloudfall is pre-1.0 and honestly labeled. Implemented today, and proven
live on disposable Debian targets:

- Typed YAML config validated against JSON Schemas, deterministic Ansible
  inventory rendering, read-only server inspection, and
  config-versus-observed drift audit with distinct exit codes
- Hardened Debian baseline, UTC time, nftables firewall, and a guarded
  Loki/Grafana/Alloy logging stack with declared alert rules and
  notification channels over mTLS
- A service catalog (loopback PostgreSQL, Redis, and Nginx/TLS sites) with
  sops/age secret rendering, receipted backups, and a timer-driven restore
  drill with external alert delivery
- Health-gated deploys with artifact releases and symlink rollback, plus an
  evidence-derived operations dashboard
- Render blueprint and live-API importers, guided data migration, and the
  resumable `cloudfall migrate` orchestrator with explicit cutover steps
- The `cloudfall-mcp` agent server and the always-on operator with receipted
  proposals and policy-bounded autonomy

The one implemented layer not yet validated live is bare-metal RAID/storage
provisioning. Next up is the fleet-density track: enforced resource sharing,
application mobility with node drain, PostgreSQL point-in-time recovery, and
the demand-gated failover formation, alongside broader catalog breadth. See
the [roadmap](ROADMAP.md) for the milestone plan.

## Quickstart

Cloudfall is not on PyPI yet; run it from a clone of this repository. The
only prerequisite is [`uv`](https://docs.astral.sh/uv/): it provisions the
required Python 3.14 interpreter and all dependencies automatically, so you
do not need Python 3.14 preinstalled.

```console
git clone https://github.com/romamo/cloudfall.git
cd cloudfall
uv sync
uv run cloudfall config validate --project config/examples
```

The command validates every YAML config document against the v1 JSON Schemas
and then checks cross-resource references. Successful and failed results are
emitted as structured JSON.

Show the non-secret platform inventory and render it as deterministic Ansible
JSON:

```console
uv run cloudfall inventory show --project config/examples
uv run cloudfall-engine inventory render --project config/examples
```

### Start your own project

Your servers, applications, evidence, and any playbooks of your own belong in
a project: a directory of its own, kept private. Cloudfall's wheel bundles the
schema catalog and the Ansible engine, so the project needs no checkout of
this repository. `cloudfall init` is the first command; run it straight from
git or from a clone (`uv run cloudfall init ../my-project`), and it pins the
project to the exact Cloudfall commit it ran from. Without a directory
argument it initializes the current directory, which must be empty:

```console
uvx --from git+https://github.com/romamo/cloudfall.git cloudfall init my-project
cd my-project
uv sync
```

The project holds one directory per resource kind (`servers/`,
`server-types/`, `ssh-public-keys/`, `applications/`, `components/`,
`services/`, `domains/`, `alert-rules/`, `operator-policies/`,
`logging-stacks/`), a README with the next steps, a `.gitignore` for the
runtime `tmp/` directory where evidence and receipts land, and a
`pyproject.toml`:

```toml
[project]
name = "my-project"
version = "0"
requires-python = ">=3.14"
dependencies = ["cloudfall"]

[tool.uv]
package = false

[tool.uv.sources]
cloudfall = { git = "https://github.com/romamo/cloudfall.git", rev = "<commit>" }
```

Only the kind directories are read as resources, so playbooks, roles, docs,
and tooling files may live anywhere else in the project. Commands find the
project on their own: the current directory when it is one, else
`--project`, else `CLOUDFALL_PROJECT`. Declare your SSH key and your first
server with `cloudfall add`; the server's type is created from the bundled
Debian 13 baseline when it does not exist yet, and every written file is
plain YAML you can edit (the reference set under `config/examples/` shows
every kind). Then validate, render, inspect, and audit from inside the
project:

```console
uv run cloudfall add ssh-key ~/.ssh/id_ed25519.pub --owner roman
uv run cloudfall add server h1 --address 203.0.113.10
uv run cloudfall config validate
uv run cloudfall-engine inventory render --output tmp/ansible-inventory.json
uv run cloudfall-engine playbook run inspect --inventory tmp/ansible-inventory.json --extra-vars cloudfall_inspect_output_directory=$PWD/tmp/observed
uv run cloudfall audit --observed tmp/observed
uv run cloudfall-engine playbook run playbooks/proxy.yml --roles roles
```

Bundled playbooks are addressed by name (`cloudfall-engine playbook list`
shows them); project playbooks by path, with `--roles` directories searched
before the bundled roles. To move a project to a newer Cloudfall commit, bump
`rev` and run `uv sync`.

## Migrate from Render

Map a `render.yaml` blueprint onto Cloudfall config:

```console
uv run cloudfall import render render.yaml --application acme --server h1
```

Web, private, and worker services become `Component` resources (workers use
a service-active health gate instead of a fabricated HTTP check), managed
PostgreSQL becomes a `Service` with application-owned peer-authentication
databases, and custom domains become TLS-required `Domain` routes. Each web
component receives an explicit listen port written as `PORT` into an
environment file rather than into any resource — the config never contains
secret values. The full walkthrough, including the Render-to-Cloudfall name
mapping, is in the [Render migration guide](docs/render-migration-guide.md).

The importer never guesses silently. `IMPORT-REPORT.md` records everything
that was not imported (cron, static, and container services today), every
assumption it made, and every action required before cutover, including data
migration and DNS steps. Merge the emitted fragment with your servers and
server types, validate, then drive the whole migration with one resumable
plan:

```console
uv run cloudfall migrate --build acme-api=main
uv run cloudfall migrate --build acme-api=main --yes
```

Without `--yes` the command shows the plan; with it, the plan executes step
by step — baseline, services, artifact builds, health-gated deployments,
HTTP routes, a DNS-verification pause at the cutover moment, TLS issuance,
and a final evidence pass that requires a compliant audit and healthy
routes. Progress persists after every step, so a failed step or the DNS
pause resumes exactly where it stopped.

Managed-database contents follow the same guided path: save the source
connection URL into a local file and either run `cloudfall data migrate`
directly or add `--data <database>=<url-file>` to the migration plan. The
engine dumps on the target host, restores over the peer-authenticated
socket, refuses non-empty target databases, verifies per-table row counts,
and writes a `DataMigrationReceipt`. The same path serves databases hosted
without a platform blueprint — serverless PostgreSQL providers such as
Neon — where only the data moves: see the
[Neon migration guide](docs/neon-migration-guide.md).

## Inspect servers and audit drift

Each `Server` references a reusable `ServerType`
describing its required Debian version, software RAID, filesystem capacity,
packages, systemd services, and allowlisted configuration evidence.

Collect a read-only snapshot from every reachable server, then compare it
with the config. Workflow commands use [Task](https://taskfile.dev)
(`brew install go-task` / `apt install task`); every task is a thin wrapper
over a `uv run` command, so if you prefer not to install Task, copy the
underlying command from [`Taskfile.yml`](Taskfile.yml):

```console
task inspect
task audit
```

The direct equivalent of `task audit`, for example, is:

```console
uv run cloudfall audit --project config/examples --observed tmp/observed
```

Snapshots are written to `tmp/observed/<server>.json` with mode `0600` and
never include configuration-file contents. The audit emits one JSON report:
exit code `0` compliant, `1` drift, `2` invalid input, `3` compliance unknown.

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

For a live view, run the dashboard as a server on the management host (a
VPS or your local machine):

```console
task dashboard:serve
```

`cloudfall dashboard serve` re-derives the projection from state and evidence
on a refresh interval (default 10 seconds) and serves a page that updates
itself in place; `--inspect-services` additionally probes DNS, TLS, origin,
and public routes on every refresh, so service health is live. Server
hardware evidence still comes from observation snapshots: schedule
`task inspect` (cron or a timer) on the management host to keep it fresh.
The server binds `127.0.0.1:8100` by default; set `DASHBOARD_HOST`,
`DASHBOARD_PORT`, and `DASHBOARD_REFRESH` to override. The projection stays
strictly read-only either way.

## Always-on operator

The operator is the always-on half of Cloudfall: a process on the management
host that watches declared alerts and audited drift, writes every diagnosis
as a schema-validated proposal receipt, and executes only existing engine
entry points — after an explicit approval, or autonomously for operation
classes licensed by a declared `OperatorPolicy` and earned receipt history.
DNS cutover, data deletion, and database promotion always require explicit
confirmation. See the [operator guide](docs/operator-guide.md).

## Logging, secrets, and storage guides

- A guarded parallel logging slice deploys Loki, loopback-only Grafana, an
  mTLS ingestion gateway, and Alloy without touching legacy agents: see the
  [logging service guide](docs/logging-service-guide.md)
- Declared secret references render into per-component `0600` environment
  files from a sops/age-encrypted secrets directory: see the
  [secrets guide](docs/secrets-guide.md)
- New two-drive servers use a RAID1 system area plus independent storage
  tails: review the [hybrid storage design](docs/hybrid-storage-design.md)
  and the destructive, new-server-only
  [storage provisioning guide](docs/new-server-storage-guide.md)

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).
Notable changes are recorded in the [changelog](CHANGELOG.md).

Cloudfall is developed in the open, business model included: the
[lean canvas](docs/lean-canvas.md) describes the problem, the wedge, and
how the application intends to sustain itself.

## License

Cloudfall is licensed under the [GNU AGPL-3.0-or-later](LICENSE). Commercial
licensing exceptions are available from the copyright holder.
