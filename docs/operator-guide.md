# Operator guide

The operator is the always-on half of Cloudfall: a management-host process
that watches declared alerts and audited drift, writes evidence-backed
remediation proposals, and executes them only after an explicit approval.
It is deliberately deterministic — it never invents actions, only maps
declared triggers onto the engine entry points you already use.

## Prerequisites

- A deployed logging stack with an `alerting` block (see the
  [logging service guide](logging-service-guide.md)) and at least one
  declared `AlertRule`
- The gateway's read-only alerts route, included automatically when the
  backend role runs: `GET /api/v1/alerts` over the same mTLS gateway that
  receives logs and metrics
- A client certificate for the management host, signed by the same CA that
  validates collector certificates (`collectors.clientTls`); the operator is
  just another mTLS client of the gateway

## Watching

One pass (fetch alerts, propose, exit):

```sh
uv run cloudfall operator run state/production \
  --gateway-ca certs/ca.crt \
  --gateway-cert certs/operator.crt \
  --gateway-key certs/operator.key
```

Always-on with audited drift checks every 15 minutes:

```sh
uv run cloudfall operator run state/production \
  --gateway-ca certs/ca.crt \
  --gateway-cert certs/operator.crt \
  --gateway-key certs/operator.key \
  --interval 30 --drift-interval 900
```

The alerts endpoint is derived from the declared gateway
(`https://<serverName>:<port>/api/v1/alerts`); pass `--gateway-url` to
override. Drift checks run the read-only `inspect.yml` playbook, refresh
observations under `--observed`, and audit them; drifted checks become
proposals split by remediation: service checks propose `services.yml`,
everything else proposes `baseline.yml`.

Every proposal is a schema-validated receipt in `--proposals`
(default `tmp/operator/proposals`): the trigger evidence (alert labels or
drifted checks), a diagnosis, the exact operation, and later its outcome.
An open or failed proposal blocks duplicates for the same trigger — a
failed remediation deliberately stays visible until a human looks at it.

To run persistently, wrap the command in a systemd service on the
management host:

```ini
[Unit]
Description=Cloudfall operator (propose mode)
After=network-online.target

[Service]
WorkingDirectory=/opt/cloudfall
ExecStart=/usr/local/bin/uv run cloudfall operator run state/production \
  --gateway-ca certs/ca.crt --gateway-cert certs/operator.crt \
  --gateway-key certs/operator.key --interval 30 --drift-interval 900
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

## Reviewing and approving

```sh
uv run cloudfall operator list state/production
uv run cloudfall operator show state/production op-20260910t150000-abcdef1234567890
uv run cloudfall operator approve state/production op-... \
  --gateway-ca certs/ca.crt --gateway-cert certs/operator.crt \
  --gateway-key certs/operator.key
```

Approval executes the proposal's engine playbook and then verifies the
trigger actually resolved: an alert-triggered proposal must see its alert
stop firing, a drift-triggered proposal must see its drifted checks audit
compliant again (`--verify-timeout`, default 180 s). The receipt records
`verified` or `failed` with timestamps either way — the operator never
reports success it cannot prove. Gateway TLS flags are only needed when
approving alert-triggered proposals; drift approvals verify through the
audit.

## Through an agent

`cloudfall-mcp` exposes the same surface with the standard confirmation
handshake — start it with the gateway material to enable the watch tool:

```sh
uv run cloudfall-mcp --state state/production \
  --gateway-ca certs/ca.crt --gateway-cert certs/operator.crt \
  --gateway-key certs/operator.key
```

- `operator_proposals` (read-only) — list receipts with trigger, diagnosis,
  and outcome
- `operator_watch` (read-only; `drift=true` adds an audited drift pass) —
  one watch pass
- `operator_approve` — previews the exact command and diagnosis, then
  requires `confirm=true` to execute and verify

The human stays the approver: the agent can watch, read, and relay the
diagnosis, but nothing mutates a server without the explicit confirm.
