# Engine

This module executes validated Cloudfall operations using Ansible. It owns
inventories, playbooks, roles, templates, deployment health checks, symlink
switching, and rollback mechanics.

The engine acts on explicit inputs and must not silently modify declarative
state.

## Parallel observability pilot

`ansible/playbooks/logging.yml` installs pinned Loki and Grafana packages plus
Prometheus on one declared backend, exposes only the Loki push and Prometheus
remote-write endpoints through an mTLS Nginx gateway, and installs Alloy on
declared collectors. Alloy ships journal and file logs and pushes host
metrics from its embedded unix exporter, so collectors open no metrics port.
Loki, Prometheus, and Grafana bind to loopback, and Grafana is provisioned
with both data sources. The roles validate pre-provisioned secret files and
native service configuration before restart, deploy one host at a time, and
never manage Filebeat or the existing Elastic path:

```console
task logging:check STATE_DIR=state/examples
task logging:deploy STATE_DIR=state/examples
```

Follow the [`logging service guide`](../docs/logging-service-guide.md) before
declaring a `LoggingStack` for live hosts.

Its first executable capability is deterministic Ansible inventory rendering:

```console
uv run cloudfall-engine inventory render state/examples
```

The renderer consumes the SDK's typed inventory and creates host variables plus
environment, project, and component groups. It never includes secret values or
secret references.

Active environment-scoped SSH public keys are rendered as
`cloudfall_ssh_public_keys`. Rendering alone does not change authorized keys.

## UTC time baseline

`ansible/playbooks/time.yml` enforces `Etc/UTC`, keeps the hardware clock in
UTC, enables systemd network time synchronization, and waits for the host clock
to synchronize. The play targets the complete Cloudfall inventory and runs one host
at a time; use `TIME_LIMIT` for a bounded deployment:

```console
task time:check STATE_DIR=state/examples TIME_LIMIT=h1
task time:deploy STATE_DIR=state/examples TIME_LIMIT=h1
```

## Server baseline

`ansible/playbooks/baseline.yml` converges a host to the managed baseline in
one run: bootstrap, the UTC time contract, declared key-only SSH access,
unattended Debian security upgrades, and the declared default-deny firewall.
It targets all servers serially and is idempotent; a second run reports no
changes:

```console
task server:baseline:check
task server:baseline
```

The firewall role renders `/etc/nftables.conf` from the rendered
`cloudfall_firewall` host variable (declared profile rules plus the server's
SSH port), validates it with `nft --check` before installation, and refuses
any ruleset that does not allow the control connection's SSH port. The
managed table replaces the complete ruleset; hosts whose profile declares no
firewall are left unmanaged.

The access role installs active environment-scoped `SshPublicKey` resources
for root, validates the complete OpenSSH configuration, and only then
disables password and keyboard-interactive authentication.

## Bootstrap role

`ansible/roles/cloudfall_bootstrap` prepares an already-installed Debian host for
application deployment. It:

- validates that the host is Debian with systemd;
- installs a minimal baseline package set without upgrading the OS;
- creates one system account per project;
- creates project shared directories; and
- creates component `releases/` and `shared/` directories.

It deliberately does not install Debian, configure RAID, change SSH or firewall
policy, fetch secrets, deploy artifacts, or start application services.

## Read-only inspection role

`ansible/roles/cloudfall_inspect` collects normalized evidence without changing the
remote host. It reads Ansible facts, the Debian package database through
`dpkg-query`, service facts, systemd timer units and unit files, the managed
`inet cloudfall` nftables table as JSON, `lsblk`, `findmnt`, `/proc/mdstat`,
`mdadm --detail --scan`, read-only NVMe reports from `smartctl`, and `stat` data for the
configuration paths allowlisted by the server's `HostProfile`.

Configuration capture is deliberately bounded to metadata and optional SHA-256
hashes; the role does not fetch file contents. The only persistent writes are
controller-side JSON snapshots, stored with mode `0600`:

```console
task inspect OBSERVATION_DIR=tmp/observed
```

`OBSERVATION_DIR` is repository-relative; the Task workflow passes an absolute
controller path to Ansible so delegated writes cannot resolve relative to the
playbook directory.

The play requires the inventory SSH account to connect and become root. A
collection failure stops the run instead of producing partial evidence that
could be mistaken for a complete audit.

Generated inventory keeps project and component collections in host variables.
This prevents variable collisions when one server belongs to multiple project
or component groups.
