# Proving run: real DNS cutover of demo.cloudfall.dev

| | |
| --- | --- |
| **Date** | 2026-09-08 |
| **Scope** | The last unexercised wedge step: a record cutover on an owned domain |
| **Source** | Render free-tier web service + managed PostgreSQL 17, custom domain `demo.cloudfall.dev` |
| **Target** | Hetzner Cloud cx23 (fsn1), stock Debian 13, deleted after the run |
| **Verdict** | Migrate paused at DNS as designed, resumed after the flip, byte-identical data on the owned domain |

## Method

The [`examples/render-demo`](../../examples/render-demo) service was
redeployed to Render with fresh seeded rows, and `demo.cloudfall.dev` (a
subdomain of an owned Cloudflare-managed zone) was attached as its custom
domain: a DNS-only CNAME to the `onrender.com` host with a 60-second TTL —
the import report's "lower the DNS TTL" step done literally. Render verified
the domain and served the rows over its own TLS at
`https://demo.cloudfall.dev`.

The migration then ran as before through `cloudfall-mcp` (blueprint import,
ten-step migrate with the confirmation handshake) with `demo.cloudfall.dev`
as the declared domain.

## Results

- **The DNS pause is real.** The confirmed migrate run completed five steps
  (baseline, services, build, health-gated deploy, HTTP routes) and then
  **paused** at `dns-verify` with an exact operator instruction: the domain
  resolved to Render's addresses instead of the declared proxy. The
  application sat deployed and healthy on the target behind the un-flipped
  record — the parallel-run state the wedge describes
- **Propagation behaves like production.** After the record flipped to an A
  record on the target (via the Cloudflare API), authoritative DNS updated
  in ~20 seconds, but the controller's system resolver held the cached CNAME
  chain for a while; a resume attempt during that window paused again with
  the same instruction rather than proceeding on stale state. Once the
  resolver caught up (~30 seconds more), the resumed plan completed all ten
  steps: DNS verified, Let's Encrypt issued for `demo.cloudfall.dev`,
  audit compliant 20/20, route healthy
- **Data**: `pg_dump` from Render's managed database restored into the
  migrated service over the peer-auth socket
- **Cutover proof**: `GET /items` returned **byte-identical JSON** from
  `render-demo-kwf7.onrender.com` and from `https://demo.cloudfall.dev`,
  now served by nginx on the Cloudfall host with a certificate issued to
  `CN=demo.cloudfall.dev`

## Caveats

- No live traffic flowed during the flip, so blue/green behavior under load
  was not observed
- The dump/restore remains manual per the import report; the guided
  data-migration playbook is still pending
- Bare-metal RAID/storage provisioning remains the one layer proven only in
  design documents

## Teardown

Render service and database deleted, the `demo.cloudfall.dev` record
removed, and the Hetzner server and key destroyed.
