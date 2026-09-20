# Changelog

Notable changes to Cloudfall. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[semantic versioning](https://semver.org/) once the project reaches 1.0.

## [Unreleased]

## [0.2.0] — 2026-09-20

### Added

- `cloudfall-mcp` exposes `add_ssh_key`, `add_server_type`, and `add_server`
  so an agent can declare the fleet without writing YAML by hand; each
  returns the `cloudfall add` envelope, writes into the project only, and
  re-validates it. `cloudfall init` stays CLI-only because the server runs
  inside an existing project (#3)
- `cloudfall init` lays out the secrets setup (`secrets/` and a `.sops.yaml`
  template), runs `git init` unless the directory already lies inside a
  repository, links the secrets guide and the reference examples at the
  pinned commit, and takes `--description` for an About section in the
  README and the `description` in `pyproject.toml`
- `cloudfall init` writes `AGENTS.md`, the operating contract for AI agents
  in the project: canonical invocation, every command sorted by effect with
  the gate each server-changing command demands, the output contract and
  exit codes, what may be read under `tmp/`, the secrets rule, and git
  discipline; plus a one-line `CLAUDE.md` pointing at it. The
  classification comes from the new `cloudfall.commands` catalog, which
  the tests check against both argparse trees (#2)
- `cloudfall init` from a source checkout refuses to pin `HEAD` when tracked
  files are modified (`project_revision_uncommitted`) or when `HEAD` is on
  no remote branch (`project_revision_unpublished`), so a project never pins
  a commit that is not the running code or that `uv sync` cannot fetch
- Fleet repositories consume Cloudfall as a package: the wheel bundles the
  v1 schema catalog and the Ansible engine, and every `--schemas` and
  `--engine` default resolves to the bundled copies (or to the source tree
  when run from a checkout), so no submodule or sibling checkout is needed
- `cloudfall-engine playbook run` executes a bundled playbook by name or a
  fleet playbook by path under the engine's Ansible configuration, with
  `--roles` directories searched before the bundled roles and `--check`,
  `--diff`, `--syntax-check`, `--limit`, `--tags`, and `--extra-vars`
  passed through; `cloudfall-engine playbook list` names the bundled ones
- Timer-driven restore drill: an optional `backup.restoreCheckOnCalendar`
  schedule installs an audited restore-check service and timer for
  PostgreSQL and Redis, so backup restorability is verified continuously
  (M10; proven live on 2026-09-11 together with alert delivery to an
  external destination)

### Changed

- `ansible-core` is a runtime dependency rather than a development one, so
  an installed `cloudfall` package can run its engine
- **Breaking: common-vocabulary rename across schemas, CLI, and layout.**
  Resource kinds `Project` → `Application` and `HostProfile` → `ServerType`
  (schema files renamed to match); the `Server` spec field `profile` →
  `serverType`; the `Component` spec field `project` → `application`; the
  top-level `state/` directory → `config/` with resource directories
  `projects/` → `applications/` and `host-profiles/` → `server-types/`;
  `cloudfall state validate` → `cloudfall config validate`; importer flag
  `--project` → `--application`; Python API `validate_state` →
  `validate_config`, `ValidatedState` → `ValidatedConfig`,
  `StateValidationError` → `ConfigValidationError`; Taskfile variable
  `STATE_DIR` → `CONFIG_DIR`; inventory payload key `hostProfiles` →
  `serverTypes` and observed-server key `profile` → `serverType`. Existing
  config directories must be migrated by renaming the directories and the
  `kind`/`profile`/`project` fields; no compatibility aliases are provided
- `cloudfall-mcp --help` now documents every option and the confirmation
  handshake
- README quickstart covers cloning, `uv sync`, and automatic Python 3.14
  provisioning; the Task prerequisite is documented with a direct `uv run`
  equivalent
- Roadmap gained a forward-looking M6 section (catalog breadth, Render API
  import, cutover generator, secrets v2, bare-metal provisioning, backup and
  restore operations, fleet observability)
- `cloudfall`, `cloudfall-engine`, and `cloudfall-mcp` match long options
  exactly and report usage errors as the JSON error envelope
  (`invalid_argument`, exit 2) instead of argparse prose; unrecognized
  options are named without their values
- **Breaking: `cloudfall deploy`, `rollback`, `restart`, and `data migrate`
  change servers only with `--yes`.** Without it they run every
  controller-side check (declared component or service, verified artifact,
  declared database, source URL file) and print a `status: plan` preview
  naming the target servers, exit `0`, matching `cloudfall migrate` and the
  MCP confirmation handshake; scripts and Taskfile targets must add `--yes`
- Runtime path options (`--output`, `--receipts`, `--inventory-file`,
  `--observed`, `--plan-file`, and the other evidence and receipt
  directories) on all three entry points, and the MCP tools' output and plan
  paths, refuse a relative path that climbs out of the project
  (`tmp/../../x`); a location outside the project must be given as an
  absolute path

### Fixed

- A guessed `--api-key` or `--source-url` no longer binds to
  `--api-key-file`/`--source-url-file` and echoes the secret in the
  resulting file-not-found error
- Malformed component, service, proposal, application, server, and release
  ids fail as `invalid_argument` before any work starts instead of crashing
  with a traceback on exit 1
- JSON documents are flushed as they are written, so `operator run
  --interval` under a pipe or journald delivers each pass instead of
  holding it in the buffer and losing it on termination
- A missing or unreadable gateway CA, certificate, or key reports
  `operator_gateway_material_invalid` instead of an uncaught traceback
- `cloudfall init` suggests `uv run cloudfall config validate` as the next
  step; the previous `config validate .` hint no longer parsed

## [0.1.0] — 2026-09-08

First public milestone: the Render-to-Hetzner wedge proven live end to end
on disposable Hetzner Cloud Debian 13 servers (see
[`docs/proving-runs/`](docs/proving-runs/)).

### Added

- Declarative state module: versioned v1 JSON Schemas for `Server`,
  `HostProfile`, `Project`, `Component`, `Service`, `Domain`, `SshPublicKey`,
  and `LoggingStack`, plus observation, receipt, and artifact schemas
- `cloudfall` CLI and Python API: state validation, typed inventory,
  config-versus-observed drift audit with distinct exit codes, service
  lifecycle status, and the evidence-derived operations dashboard
  (static build and live `dashboard serve`)
- `cloudfall-engine`: deterministic Ansible inventory rendering and the
  artifact builder (git ref to hashed tarball with release metadata)
- Engine roles: Debian bootstrap, UTC time baseline, SSH access hardening,
  nftables default-deny firewall, unattended security upgrades, read-only
  inspection, PostgreSQL with peer-auth databases and backup timers,
  Nginx/TLS domain routes with Let's Encrypt issuance, and the guarded
  Loki/Grafana/Alloy logging stack over an mTLS gateway
- Health-gated deploy slice: digest-verified artifact transfer, symlink
  releases, automatic rollback on failed health checks, and release receipts
- `cloudfall import render`: `render.yaml` blueprint importer with a
  structured gap report (`IMPORT-REPORT.md`)
- `cloudfall data migrate`: guided managed-Postgres dump and restore with
  row-count verification and a `DataMigrationReceipt`
- `cloudfall migrate`: resumable end-to-end orchestrator with persisted step
  progress and a DNS-verification pause at the cutover moment
- `cloudfall-mcp`: seventeen annotated MCP tools; read-only evidence tools
  exposed freely, every server-changing tool gated behind a two-step
  confirmation handshake
