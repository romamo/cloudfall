# Proving run: real Render workload cut over to a Cloudfall host

| | |
| --- | --- |
| **Date** | 2026-09-08 |
| **Scope** | M4 full exit: an application actually hosted on Render, migrated with its data |
| **Source** | Render free-tier web service + managed PostgreSQL 17 (Frankfurt) |
| **Target** | Hetzner Cloud cx23 (fsn1), stock Debian 13, deleted after the run |
| **Verdict** | Byte-identical application responses before and after cutover |

## Method

The [`examples/render-demo`](../../examples/render-demo) service (health
endpoint plus a Postgres-backed `/items` endpoint) was deployed to Render for
real: a free web service built by Render from this public repository
(`rootDir: examples/render-demo`, `uv sync --frozen`) and a free managed
PostgreSQL 17 instance, wired together with `fromDatabase` and serving
seeded rows at `https://render-demo-f5sl.onrender.com/items`.

The migration then followed the same agent-driven path as the
[M4/M5 run](2026-09-08-hetzner-m4-m5.md): the directory's `render.yaml`
imported through the `import_render` MCP tool, the fragment merged, and the
ten-step plan executed through the `migrate` MCP tool with the confirmation
handshake. This time the artifact builder cloned the real GitHub repository
at `main` rather than a local `file://` checkout.

The data followed the import report's required action: the Render database's
IP allow list admitted the target server, `pg_dump` ran on the target against
Render's external connection string, and the dump restored into the local
`demo` database over the peer-authenticated socket as the application user.

## Results

- **Migration**: all ten steps completed through MCP — baseline, PostgreSQL,
  build from GitHub, health-gated deploy, HTTP routes, DNS verification,
  Let's Encrypt issuance, inspection, a compliant 20/20 audit, and a
  route-health pass
- **Data**: the `items` table dumped from Render's managed PostgreSQL and
  restored into the migrated service; row count preserved
- **Cutover proof**: `GET /items` returned **byte-identical JSON** from
  `render-demo-f5sl.onrender.com` (Render, managed database) and
  `app.188.34.153.101.sslip.io` (Cloudfall host, local peer-auth database,
  Let's Encrypt TLS)
- The importer's `DATABASE_URL` rewrite to
  `postgresql:///demo?host=/var/run/postgresql` worked unchanged in the
  deployed environment file; the PORT-deduplication fix from the previous
  run held

## Caveats

- The public hostname was an `sslip.io` name that already resolved to the
  target, so no DNS record actually changed hands; a TTL-lowered record
  cutover on an owned domain remains the one unexercised wedge step
- Render's free web service and database are modest proxies for a paying
  workload (no custom domain on the Render side, no traffic during cutover)
- The dump/restore was manual over SSH per the import report's required
  action; the guided data-migration playbook remains pending on the roadmap

## Teardown

The Render web service and database were deleted through the API and CLI,
and the Hetzner server and SSH key destroyed. Total cost: a few euro cents
on Hetzner; the Render side stayed within the free tier.
