# State

This module is the declarative source of truth for Cloudfall. It will contain
versioned JSON Schemas and YAML documents for servers, projects, components,
clusters, domains, monitoring, backups, and secret references.

It must not contain credentials or execution logic. Secret values remain in
Infisical; state stores references only.

## v1 contract

Schemas live in `schemas/v1/` and use JSON Schema Draft 2020-12. Every resource
has an `apiVersion`, `kind`, strict `metadata.id`, and kind-specific `spec`.
Unknown properties are rejected.

The current contract includes `Server`, `HostProfile`, `Project`, `Component`,
`Domain`, `SshPublicKey`, and `LoggingStack` desired-state resources. A
`LoggingStack` pins the guarded parallel migration, backend and collector
placement, package versions, loopback listeners, mTLS paths, retention, and
explicit journal/file sources. A `Domain` pins the proxy
and origin servers, public names, edge mode, TLS policy, configuration evidence
path, and end-to-end health contract. Public keys are non-secret and are
scoped to an environment; private keys never belong in state. A server profile
captures the required Debian/systemd baseline, software RAID level and
capacity, mounts, packages, services, and an explicit allowlist of
configuration files whose metadata or hash may be inspected.

`observed-server.schema.json` defines the normalized, read-only evidence emitted
by the engine. Observations are runtime evidence, not desired state, and belong
under ignored controller storage such as `tmp/observed/`. They include
structured NVMe SMART health when available; configuration contents and
credentials must never be written there.

`observed-domain.schema.json` validates controller-side DNS, edge, TLS, origin,
and public HTTP evidence. `deployment-receipt.schema.json` validates receipts
written only after an Ansible proxy run has applied and validated its final
configuration. Together with server observations, these inputs derive planned,
ready-to-deploy, deployed, configured, and healthy service states.

`examples/` contains a complete valid desired-state set. `tests/invalid/`
contains deliberately invalid desired state, while
`tests/observed/compliant/` contains validated audit fixtures.

Real fleet inventory belongs in a separate private state repository. Keep
examples and test fixtures synthetic; provider identifiers, network
allocations, and desired profiles for live hosts never belong here.
