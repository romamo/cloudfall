# Neon migration guide

This guide walks a Neon-hosted PostgreSQL database onto one self-hosted
Debian server with Cloudfall. Unlike the [Render guide](render-migration-guide.md)
there is no blueprint to import: Neon hosts only your database, so the
config is written by hand and the migration is a guided dump-and-restore
with the same tooling the Render path uses.

> **Status:** the guided data migration (`cloudfall data migrate`) is
> proven live against an external managed PostgreSQL — dump, restore,
> per-table row-count verification, refusal guards, and a
> `DataMigrationReceipt` (see the
> [proving run](proving-runs/2026-09-08-guided-data-migration.md)). The
> source was Render; any reachable PostgreSQL URL, including Neon's,
> takes the same path.

## Name mapping

Neon and Cloudfall describe overlapping things with different words:

| Neon | Cloudfall |
|---|---|
| Application | `Service` of kind PostgreSQL on a declared server |
| Default branch | The declared databases of that service |
| Compute endpoint | The PostgreSQL systemd unit: always on, no cold starts |
| Connection pooler (built-in PgBouncer) | Not needed for a co-located app: peer-authenticated local socket, no passwords, no TLS in the middle |
| Scheduled backups | Declared `backup` block: `pg_dump` on a systemd timer with retention, plus the restore drill (`cloudfall backup verify`) |
| Point-in-time restore / history | Last-dump recovery today; continuous WAL archiving and PITR are roadmap M11 |
| Snapshots | Your provider's volume snapshots, taken outside Cloudfall |
| Autoscaling / scale-to-zero | Fixed server capacity: for an always-on workload this is what you were paying serverless prices for |
| Neon console | Local operations dashboard (`task dashboard`) and `cloudfall audit` |
| Metrics page | Per-service exporters flowing through the mTLS gateway (declared `metrics` block) |

## What you give up

Be deliberate about these; nothing below fails loudly after cutover, it is
simply absent:

- **Copy-on-write branching** — no equivalent; scratch databases are
  created and seeded explicitly
- **Restore to any second** — recovery granularity is the backup timer
  until M11 lands WAL archiving; if last-dump recovery is not acceptable,
  wait for M11 or archive WAL manually
- **Multi-node failover** — single host until M11; failover is manual by
  design today (see [ARCHITECTURE](../ARCHITECTURE.md))

## What you need

- A Debian 13 server already converged through baseline and services
  (a Hetzner Cloud VPS is the proven reference), or a fresh one plus an
  hour to get there
- The Neon application's connection details and console access
- Python 3.14 and [`uv`](https://docs.astral.sh/uv/) on the machine you
  run Cloudfall from

## Step 1: declare the PostgreSQL service

One `Service` resource, with the major version matching Neon's (the Neon
console shows it under application settings):

```yaml
---
apiVersion: cloudfall/v1
kind: Service
metadata:
  id: postgresql-main
  description: Primary PostgreSQL, migrated from Neon
spec:
  serviceKind: postgresql
  environment: production
  server: h1
  bind:
    address: 127.0.0.1
    port: 5432
  postgresql:
    majorVersion: "17"
    databases:
      - name: app
        application: app
  backup:
    directory: /var/backups/cloudfall/postgresql-main
    onCalendar: "*-*-* 02:00:00 UTC"
    retentionDays: 14
  metrics:
    enabled: true
```

Declare one entry under `databases` per Neon database you are keeping.
Validate and converge:

```console
uv run cloudfall config validate config/production
uv run cloudfall services config/production
```

## Step 2: pre-flight checks on the Neon side

Run these against the Neon database before dumping; each is a cheap query
now versus a surprise after cutover:

- **Extensions** — `SELECT extname FROM pg_extension;` — everything
  listed must exist on Debian (`postgresql-contrib` covers the common
  ones; anything Neon-specific such as `pg_tiktoken` or `neon` does not
  carry and must be dropped or replaced first)
- **Version** — `SHOW server_version;` must match the declared
  `majorVersion` at the major level
- **Branches** — only the branch you dump from migrates; anything
  worth keeping on another branch is a separate database to declare and
  migrate, everything else dies with the application
- **Size** — `SELECT pg_size_pretty(pg_database_size(current_database()));`
  to estimate the write-freeze window; dump plus restore for a
  10–20 GB database is minutes, not hours

## Step 3: stage the source URL

Use the **direct** (unpooled) connection string, not the `-pooler`
endpoint: `pg_dump` holds a session the transaction-mode pooler cannot
serve. In the Neon console this is the connection string with pooling
toggled off. Keep `sslmode=require`. If the compute is suspended, any
connection wakes it.

The URL goes into a controller-side file whose only content is the URL;
it is staged root-only on the target and removed afterwards, and it never
enters the config:

```console
printf '%s\n' 'postgresql://user:pass@ep-xxx.eu-central-1.aws.neon.tech/app?sslmode=require' \
  > tmp/neon-app-url
chmod 600 tmp/neon-app-url
```

## Step 4: freeze writes and migrate the data

The dump is a consistent MVCC snapshot, but writes that land on Neon
after the dump starts are lost. Put the application into maintenance
mode or stop its writers, then:

```console
uv run cloudfall data migrate config/production postgresql-main \
  --database app --source-url-file tmp/neon-app-url
```

The guided migration dumps from Neon on the target host, restores over
the peer-authenticated socket as the declared owner, verifies per-table
row counts match the source exactly, removes the staged URL file and the
dump, and writes a `DataMigrationReceipt` with per-table counts. It
refuses non-empty target databases, so a re-run requires deliberately
dropping what was restored.

One database at a time; repeat per declared database. If the application
is also moving in the same operation, `cloudfall migrate --data
app=tmp/neon-app-url` inserts this step into the orchestrated plan
between services and deploys.

Note the one-time egress: Neon meters the dump like any other transfer,
so a 14 GB dump costs about a dollar — the last transfer bill this
database will produce.

## Step 5: cut over the application

Point the application's `DATABASE_URL` at the migrated database. For a
co-located app this is the local peer-authenticated socket URL (no
password, no TLS, no pooler); for an app elsewhere, route it the same
way as any other declared service. Deploy, watch health, and keep writes
frozen until the app is verified against the new database.

## Step 6: wind down Neon

- Keep the Neon application **paused, not deleted**, for a rollback window
  (a week is plenty); scale-to-zero makes waiting nearly free
- Verify the first scheduled backup ran and run the restore drill:
  `cloudfall backup run` / `cloudfall backup verify` — the point of
  leaving a managed provider is that restorability is now your receipt
  to hold
- Then delete the Neon application; billing stops with it

## After the migration

- `task audit` proves the server matches the config, with distinct exit
  codes for drift
- The declared alert rules (a `postgresql-down` example ships in
  `config/examples/alert-rules/`) put the database under the same
  detect-diagnose-remediate loop as everything else
- The backup timer, restore drill, and `BackupReceipt` replace Neon's
  managed backups; PITR and a standby arrive with roadmap M11
