# Deploy the first Cloudfall observability service

Status: implemented parallel-migration slice; no production `LoggingStack` is
declared yet.

This workflow installs a single-node Loki and Prometheus backend,
loopback-only Grafana, an mTLS-only Nginx ingestion gateway, and Grafana
Alloy collectors that ship both logs and host metrics. It never stops,
disables, reconfigures, or removes Filebeat, Metricbeat, Elasticsearch,
Kibana, or Logstash.

Host metrics use Alloy's embedded `prometheus.exporter.unix`, so collectors
open no additional listening port; metrics are pushed through the same mTLS
gateway to Prometheus's remote-write receiver on the backend host.

## Safety boundary

The v1 `LoggingStack` contract intentionally permits only:

- migration mode `parallel` with `preserveLegacyAgents: true`;
- Loki, Prometheus, and Grafana bound to `127.0.0.1`;
- a gateway that exposes only `/loki/api/v1/push` and `/api/v1/write` and
  requires a validated client certificate;
- TSDB schema v13 with bounded filesystem retention for logs, bounded
  Prometheus retention for metrics, and distinct backend listener ports; and
- pinned Debian package versions from Grafana's stable APT repository, with
  Prometheus installed from Debian (optionally pinned via
  `software.prometheusPackageVersion`).

The gateway does not expose Loki's or Prometheus's query, administration,
deletion, readiness, or self-metrics APIs. Grafana remains reachable only
through the backend host, for example with an SSH tunnel. This first slice is a proving deployment, not a
high-availability logging cluster. Filesystem storage must be on mirrored
storage and included in capacity monitoring and backup policy.

## 1. Prepare placement and names

Add the dedicated logging host as a `Server` and give it an accurate
`HostProfile`. Do not use the capacity-constrained public proxy or a host with
an unresolved storage finding.

Choose an internal gateway name, such as `logs.internal.example`, whose A/AAAA
record points to the logging host. The server certificate must contain that
name as a DNS subject alternative name.

Before pinning versions, confirm that all packages exist for the target Debian
release:

```console
apt-cache policy loki grafana alloy
```

Use the complete APT version strings in state. Cloudfall deliberately fails rather
than silently replacing an unavailable pin with the newest package.

## 2. Materialize secrets and mTLS files

Cloudfall state contains paths only. Use the existing PKI and secret-delivery
process to materialize these files before running either logging task:

| Backend host | Collector hosts |
| --- | --- |
| `/etc/cloudfall/logging/server.crt` | `/etc/cloudfall/logging/ca.crt` |
| `/etc/cloudfall/logging/server.key` | `/etc/cloudfall/logging/client.crt` |
| `/etc/cloudfall/logging/client-ca.crt` | `/etc/cloudfall/logging/client.key` |
| `/etc/cloudfall/logging/grafana-admin-password` | |

Each collector may use a different client certificate while keeping the same
path. The client CA must validate every collector certificate. The collector
CA must validate the gateway certificate. Do not commit certificates, keys, or
the Grafana password to this repository.

Private keys and the password must not be world-readable. The roles verify all
files before installing packages, then apply the minimum service-readable
ownership and modes.

## 3. Declare the stack and sources

Copy the synthetic
[`operations` example](../state/examples/logging-stacks/operations.yaml) into
`state/production/logging-stacks/`, then replace every example host, name,
version, path, and retention value.

Declare every journald collector host and every application file glob. A file
source can attach only the bounded labels `environment`, `server`, `source`,
`job`, `project`, and `component`; request IDs, user IDs, URLs, and other
unbounded values are not part of the state contract.

Alloy runs as the `alloy` account. Grant read and traversal access only to the
declared application paths. For example, after reviewing the path and owner:

```console
setfacl -m u:alloy:rx /var/log/crm /var/log/crm/backend
setfacl -m u:alloy:r /var/log/crm/backend/current.log
setfacl -d -m u:alloy:r /var/log/crm/backend
```

Do not recursively broaden unrelated `/var/log` permissions.

## 4. Validate and inspect the plan

```console
uv run cloudfall state validate state/production
uv run cloudfall inventory show state/production
uv run cloudfall-engine inventory render state/production \
  --output tmp/ansible-inventory.json
uv run ansible-inventory --inventory tmp/ansible-inventory.json \
  --graph cloudfall_logging_backends
uv run ansible-inventory --inventory tmp/ansible-inventory.json \
  --graph cloudfall_logging_collectors
```

Validation rejects missing servers, environment mismatches, duplicate stacks,
file sources outside component placement, secret paths outside
`/etc/cloudfall/logging`, and any attempt to disable the parallel-migration guard.

## 5. Check and deploy

The check run still requires the pre-provisioned secret files because that is a
deployment precondition:

```console
task logging:check STATE_DIR=state/production
task logging:deploy STATE_DIR=state/production
```

The deployment order is backend first and collectors second, one host at a
time. Loki, Prometheus, and Alloy configuration are validated with their
native binaries; Nginx is checked with `nginx -t`; services must then pass
readiness checks.

## 6. Verify end to end

From a collector, verify the mTLS gateway without exposing credentials in the
command history through environment variables:

```console
curl --fail --silent --show-error \
  --cacert "$CLOUDFALL_LOGGING_CA" \
  --cert "$CLOUDFALL_LOGGING_CERT" \
  --key "$CLOUDFALL_LOGGING_KEY" \
  "https://logs.internal.example:3101/loki/api/v1/push" \
  --request POST \
  --header 'Content-Type: application/json' \
  --data '{"streams":[]}'
```

Open Grafana through an SSH tunnel:

```console
ssh -L 3000:127.0.0.1:3000 cloudfall@logging-host
```

Then inspect `http://127.0.0.1:3000`, confirm the provisioned `Cloudfall Loki` data
source, and query each declared `server`, `source`, `job`, `project`, and
`component`. Compare counts, timestamps, multiline behavior, ingestion delay,
and alert coverage with the legacy Elastic path for the full proving window.

Confirm host metrics through the `Cloudfall Prometheus` data source: every
declared collector must report `node_cpu_seconds_total`,
`node_filesystem_avail_bytes`, and `node_memory_MemAvailable_bytes` series
labeled with its `server` and `environment` within two collection intervals.

## Roll back the pilot

Because Filebeat remains active, collector rollback is limited to Alloy:

```console
systemctl disable --now alloy.service
```

Do not delete Loki data or remove the legacy logging path during the proving
window. Diagnose and correct the declared source or role, rerun validation, and
deploy again. Legacy retirement remains a separate approved migration gate.
