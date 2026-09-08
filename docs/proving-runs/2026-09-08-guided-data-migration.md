# Proving run: the guided data-migration playbook

| | |
| --- | --- |
| **Date** | 2026-09-08 |
| **Scope** | The roadmap's pending item: dump and restore as a first-class guided operation |
| **Source** | Render free-tier managed PostgreSQL 17 (two tables, 350 rows) |
| **Target** | Hetzner Cloud cx23 (fsn1), stock Debian 13, deleted after the run |
| **Verdict** | Guided migration verified live, including its refusal guard; one template bug fixed |

## What was built

Data migration is now a first-class guided operation instead of a manual
required-action in the import report:

- **Engine**: the `cloudfall_data_migration` role and `data.yml` playbook —
  the source connection URL lives only in a controller-side file, is staged
  root-only on the target, and both the URL file and the dump are removed in
  an `always` block; the role refuses PostgreSQL-foreign services, undeclared
  databases, and non-empty target databases; restored per-table row counts
  must match the source exactly (one deterministic `query_to_xml` count query
  runs on both sides); a `DataMigrationReceipt` with per-table counts lands
  on the controller
- **CLI**: `cloudfall data migrate <state-dir> <service> --database <name>
  --source-url-file <file>`
- **Orchestrator**: `cloudfall migrate --data <database>=<url-file>` inserts
  a `data:<database>` step between `services` and the deploys, validated
  against declared databases at plan time
- **MCP**: a confirm-gated `migrate_database` tool
- The import report's data action now names the guided command instead of
  describing a manual procedure

## Live verification

A Render free-tier PostgreSQL was seeded with two tables (`items`: 100 rows,
`orders`: 250 rows) and a disposable Hetzner host converged through baseline
and services. Then, through `cloudfall-mcp`:

- The tool call **without** `confirm` returned the confirmation-required
  preview and changed nothing
- The confirmed call dumped from Render on the target, restored over the
  peer-authenticated socket as the `demo` owner, and verified matching
  per-table counts; direct `psql` afterwards confirmed 100 and 250 rows
- A second confirmed call was **refused**: "already contains 2 tables; data
  migration refuses to overwrite existing data"
- The receipt recorded both tables with counts, the owner, server, and
  timestamp; the staged URL file and dump were verified gone from the host

## Findings

1. **Receipt rendering bug caught live**: the per-table receipt template's
   regex replacement double-escaped its backreferences under YAML
   single-quote semantics, producing invalid JSON. The migration itself
   (dump, restore, verification, cleanup) had already succeeded; only the
   receipt write failed. Fixed
2. Repository lint had been failing on `examples/render-demo` since it was
   committed (`ruff check .` covers everything and the example app follows
   its own conventions); examples are now excluded from the package's lint
   domain

## Caveats

- Verification is exact per-table row counts, not content checksums; a
  checksum mode is a possible follow-up
- The non-empty-target guard has no override; re-running a migration
  requires dropping the restored tables deliberately

## Cost

One cx23 for ~20 minutes plus a free-tier Render database: a few cents.
