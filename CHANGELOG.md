# Changelog

Notable changes to Cloudfall. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[semantic versioning](https://semver.org/) once the project reaches 1.0.

## [Unreleased]

### Changed

- `cloudfall-mcp --help` now documents every option and the confirmation
  handshake
- README quickstart covers cloning, `uv sync`, and automatic Python 3.14
  provisioning; the Task prerequisite is documented with a direct `uv run`
  equivalent
- Roadmap gained a forward-looking M6 section (catalog breadth, Render API
  import, cutover generator, secrets v2, bare-metal provisioning, backup and
  restore operations, fleet observability)

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
