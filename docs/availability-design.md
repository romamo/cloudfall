# Availability design: declared failover for every service kind

Status: proposed design. The schema block is not implemented yet; it lands
with M13 (PostgreSQL data safety and scale readiness), ahead of the
demand-gated M14 formation, so that the first implementation already fits
all service kinds.

This document defines how failover is declared in Cloudfall config. It
extends the [architecture](../ARCHITECTURE.md) stance that failover is
manual with easy reassignment: a declared formation or cluster may fail over
internally, but every operator-initiated promotion stays behind explicit
confirmation, and every availability claim must be provable by audit
evidence.

## Goal

One schema shape that declares availability for every supported service
kind, under two constraints:

1. **Honesty per kind.** Failover means different things for a cache, a
   relational primary, and a natively clustered search engine. The schema
   must not offer a uniform `failover: true` that would be a lie for some
   kinds
2. **Start at one node, expand by config.** A service declared on a single
   server must be expandable to additional nodes by editing the config and
   converging — never by reinstalling, re-importing, or redesigning. What
   makes expansion hard is never the new node; it is prerequisites that were
   skipped on the first one

## The four availability classes

| Class | Meaning of failover | Example kinds | Expressed by |
| --- | --- | --- | --- |
| Stateless | Route traffic to another server running the same thing | Nginx routes, application components | Existing multi-server assignment on `Component` and `Domain` resources plus routing (Cloudflare/DNS); no `availability` block |
| Rebuildable | Reconverge on another declared server and let it warm | Redis as cache | `availability.mode: rebuildable` |
| Formation | Replicate to a standby; promote on failure | PostgreSQL, later MySQL | `availability.mode: formation` |
| Clustered | The service manages membership and failover natively | Elasticsearch, RabbitMQ (future) | `availability.mode: clustered` |

The stateless class deliberately has no `availability` block: components and
domains already declare multi-server placement, and their failover is a
routing concern. This document governs the `Service` resource only.

## Schema

`availability` is an optional block on `Service.spec`. Absent means
`mode: single` with the kind's expansion prerequisites still enforced (see
below).

```yaml
spec:
  availability:
    mode: single            # single | rebuildable | formation | clustered
    servers: [h1, h2]       # ordered replica placement; first entry is the
                            # preferred primary / initial master
    witness: h5             # formation only: quorum tiebreaker host
    promotion: automatic    # formation only: automatic | confirm
    replication: async      # formation only: sync | async
```

| Field | Applies to | Rules |
| --- | --- | --- |
| `mode` | all | Must be allowed for the service kind (table below) |
| `servers` | all | Distinct declared `Server` ids; supersedes the single-server placement; order is meaningful |
| `witness` | formation | A declared server not present in `servers`; may be a minimal host |
| `promotion` | formation | `automatic` requires a witness; the operator and agents never initiate promotion without explicit confirmation regardless of this value |
| `replication` | formation | Declared, not inferred; sync formations must document the write-latency cost in the service description |

Allowed modes per kind:

| Kind | single | rebuildable | formation | clustered |
| --- | --- | --- | --- | --- |
| postgresql | yes | no | yes (M14) | no |
| redis | yes | yes | future | no |
| mysql (future) | yes | no | yes | no |
| elasticsearch (future) | yes | no | no | yes |
| rabbitmq (future) | yes | no | no | yes |

Validation rejects any combination outside this table, so a declaration
that a kind cannot honor fails at `cloudfall config validate`, not at
convergence.

## Expansion-ready from one node

The core of this design: **`mode: single` is not the absence of
availability planning; it is a formation or cluster of one.** Every setting
whose later change would force a restart, a reload of data, or a rebuild is
applied at first install, so growing to N nodes is purely additive.

For every kind that supports `formation` or `clustered`, the engine role
must satisfy these at n=1:

1. **Fixed identity.** Cluster names, formation names, and node names are
   set at first install from declared config and never derived from
   hostnames or defaults that a second node would contradict. Renames force
   rebuilds; identities are chosen once
2. **Replication prerequisites installed dormant.** Settings that require a
   restart to change are set for the expanded topology from day one:
   PostgreSQL installs with `wal_level = replica`, replication slots
   enabled, `max_wal_senders` sized, and a replication role provisioned;
   Elasticsearch installs with cluster discovery configured for explicit
   seed lists. A dormant prerequisite costs nothing; a missing one costs a
   restart window later
3. **Bind and TLS ready to widen.** Single-node services keep the
   loopback-only bind, but the unit and config are templated so that
   switching to a private-network interface with TLS is a config change and
   a reload — the certificate material and the config surface exist from
   the start, per the same mTLS discipline the logging gateway already uses
4. **Storage placement per the hybrid design.** Replica placement follows
   the [hybrid storage rules](hybrid-storage-design.md): tail-resident data
   is declared before it exists, and two replicas of one dataset never
   share a server
5. **Backup independent of topology.** The backup policy and restore drill
   attach to the service, not to a node count. A formation changes where
   backups are taken (the standby), never whether they exist

With those in place, the expansion contract is:

| Transition | Declared by | Engine performs |
| --- | --- | --- |
| single → formation | Add standby to `servers`, add `witness`, set `mode` | Base backup from the running primary, streaming replication, monitor setup; no data reload, no primary reinstall |
| single → clustered | Add nodes to `servers`, set `mode` | New nodes join the existing one-node cluster; shard/queue rebalancing follows the service's own mechanics |
| single → rebuildable | Set `mode`, list eligible servers | Nothing until a reassignment is needed |
| formation/cluster grows | Append to `servers` | Additional standby or node joins; placement validated first |
| formation/cluster shrinks or reverts to single | Remove entries, change `mode` | Refused by the engine without explicit confirmation; this path can destroy redundancy and is never converged silently |

## Validation rules

1. `mode` must be allowed for the kind (table above)
2. `formation` requires at least two entries in `servers` plus a `witness`
   distinct from all of them
3. `clustered` requires at least one server; quorum-sensitive settings are
   validated against the declared count (an even-node cluster without a
   tiebreaker is rejected for kinds that need quorum)
4. All placement entries must reference declared, active servers; duplicate
   servers are rejected
5. `rebuildable` is only accepted when the kind's data is derivable — the
   service must not also declare a backup policy marked as the primary copy
   of record
6. `promotion: automatic` requires `mode: formation` and a witness
7. A `formation` or `clustered` declaration requires a non-loopback bind
   policy with TLS between the declared servers; validation rejects a
   formation that would replicate in plaintext
8. Alert rules are part of the contract: `mode` other than `single`
   requires declared alert rules for replication lag or cluster health and
   for member loss — a formation nobody is alerted about is not an
   availability design

## Evidence and audit

Availability claims follow the same invariant as everything else: no status
without evidence.

- Observations record each member's actual role (primary, standby, single,
  cluster member) and replication or cluster health; audit compares the
  observed topology against the declared `mode` and `servers` and reports
  drift like any other check
- Promotion, join, and removal events write receipts, whether initiated by
  the formation itself or by a confirmed operator action
- The M10 restore drill extends to formations: a restorable backup is
  proven from the node backups are actually taken on

## Operator and autonomy interplay

Formation-internal automatic promotion is the service's own declared
behavior and is recorded as evidence when it happens. The operator's rules
are unchanged: database promotion remains on the confirm-forever list, so
no autonomy level ever licenses the operator or an agent to initiate
promotion; graduated autonomy applies to the surrounding remediation
(restarting a failed standby, re-adding a member) under the usual receipt
history and `OperatorPolicy` bounds.

## Rollout

- The schema block lands with M13 for PostgreSQL `single`, together with
  the expansion-ready prerequisites; `formation` is implemented by the
  demand-gated M14
- Existing configs need no migration: an absent block means `mode: single`,
  and the expansion prerequisites become part of the PostgreSQL and Redis
  role baselines when their roles next converge
- Redis gains `rebuildable`; Elasticsearch inaugurates `clustered` when it
  joins the catalog
- The audit gains topology-role evidence in the same milestone as the first
  formation, since an unaudited formation violates the design's own rule 8

## Non-goals

- No generic failover for arbitrary software: each kind's modes exist only
  when its engine role implements them
- No automatic sharding or data rebalancing beyond what a clustered service
  does natively
- No cross-region or cross-provider topology; the fleet model is
  single-site until the architecture says otherwise
- No availability block on `Component` or `Domain`: stateless failover
  stays a routing concern
