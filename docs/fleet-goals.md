# Fleet goals: 10–100 applications on shared nodes

Status: adopted goal statement. This document defines what fleet operation
must deliver and is the reference the roadmap sequences against. The
[architecture](../ARCHITECTURE.md) describes the durable design;
[`availability-design.md`](availability-design.md) and
[`hybrid-storage-design.md`](hybrid-storage-design.md) specify two of the
mechanisms referenced here.

## Goal

Host 10–100 applications on the shared resources of a small fleet of bare
metal or VPS nodes, at a fixed and predictable cost per node, operated from
one management host by one operator (human or AI agent) without Kubernetes.

## Requirements

Economics:

1. **Fixed cost per atomic node.** The node (a Hetzner-class dedicated
   server or VPS) is the unit of purchase and capacity; adding capacity
   means adding nodes, never renegotiating a platform bill
2. **Fixed resources, enforced sharing.** A node's resources are shared by
   its applications under declared, enforced limits. Sharing without
   enforcement is a hope: one leaking application must not take down its
   neighbors (see requirement 8)
3. **Capacity grows by adding nodes.** Expansion is horizontal and
   incremental; no resource on the fleet requires replacing existing nodes
   to grow

Placement:

4. **Applications and components are movable.** Rebalancing an application
   between nodes is a config change followed by convergence — including its
   data (requirement 9) and its routing (requirement 10), or the property
   is only true for stateless components

Operations:

5. **Fast recovery and fast expansion.** A failed unit is detected,
   diagnosed, and remediated in minutes through the alert-and-operator
   loop; a new node reaches production readiness in one command; a lost
   node is rebuilt from config and backups, not from memory
6. **Failover on demand, automatic promotion where declared.** Failover is
   a per-service opt-in through the `availability` block, priced per
   service — a formation for the databases whose revenue justifies a
   standby, fast recovery plus point-in-time restore for everyone else.
   Fleet-wide formations would multiply the host count and destroy
   requirement 1
7. **One-command node setup from the management host.** An operator adds a
   server to the config and runs one command; baseline, hardening,
   firewall, observability, and service convergence follow without manual
   steps on the node

Added by the shared-density goal — absent any of these, 10–100 applications
on shared nodes fails in practice:

8. **Per-component resource declarations, enforced by systemd.** Every
   component declares its memory, CPU, task, and I/O envelope; the deploy
   role renders them into the unit (`MemoryMax`, `CPUQuota`, `TasksMax`,
   `IOWeight`) and the audit proves them. Declared reservations against
   node capacity are also the fleet's capacity accounting: what fits where
   is computed from config, not guessed
9. **Data movement as a first-class verb.** Moving an application moves its
   state: databases (the existing verified dump-and-restore path) and file
   storage, with receipts. Rebalancing granularity is the application with
   its data, not the process alone
10. **Routing follows placement.** Domain routes re-render and reconverge
    as a consequence of moving a component; the proxy layer is never edited
    to match placement by hand
11. **Node drain and decommission.** The reverse of requirement 7:
    evacuate every component, dataset, and route from a node by config
    change, prove the fleet healthy without it, then retire it. Planned
    drain of an aging node and recovery from a failed one are distinct,
    both routine

## Current status against the requirements

| # | Requirement | Mechanism | Status |
| --- | --- | --- | --- |
| 1 | Fixed node cost | Node as purchase unit, `ServerType` | In place |
| 2 | Enforced sharing | Requirement 8 | Missing |
| 3 | Horizontal growth | All-fit-all model, movable components | Designed, proven at one node |
| 4 | Movable applications | Multi-server assignment + convergence | Partial: components move; data and routing move manually |
| 5 | Fast recovery / expansion | M7 alerting, M8/M9 operator, `server:baseline` | Proven live for recovery; expansion proven for VPS, bare-metal storage unexercised |
| 6 | Failover on demand | `availability` block, formation mode | Designed, not implemented (demand-gated) |
| 7 | One-command node setup | Baseline over SSH from config | In place for installed Debian; provisioning (installimage, RAID) out of band |
| 8 | Resource limits | systemd resource control from declared envelopes | Missing |
| 9 | Data movement verb | `data migrate` + file-storage move + receipts | Partial: databases yes, file storage no |
| 10 | Routing follows placement | Domain re-render on convergence | Partial: rendering exists, not wired to placement change |
| 11 | Drain / decommission | Evacuation procedure over 9 + 10 | Missing |

## Sequencing implications

In value order for the roadmap, after M10 (proven restores and real
alerting — the floor everything above stands on):

1. Requirement 8, resource declarations and enforcement (roadmap M11) —
   the first thing that fails at density, and the cheapest of the missing
   items
2. Requirements 9 and 10, then 11 (M12) — data movement and routing make
   requirement 4 true, which is what "ready to scale" means here, and
   drain is needed the first time a node ages out; any real fleet's aging
   hardware guarantees that day comes
3. PostgreSQL point-in-time recovery, the availability schema, and the
   expansion-ready-at-one-node prerequisites (M13) — the schema and
   prerequisites land here because they are cheap and make the formation
   purchasable later
4. Requirement 6 implementation, the formation (M14) — demand-gated:
   built when a service's revenue justifies a standby, not before
5. Bare-metal provisioning closes the last gap in requirement 7

## Non-goals

- Kubernetes, container orchestration, or automatic scheduling: placement
  is declared, with capacity accounting to inform it, not decided by a
  scheduler
- Fleet-wide high availability by default: availability is bought per
  service where it pays for itself
- Multi-region or multi-provider topology: the fleet is single-site until
  the architecture says otherwise
- Autoscaling: capacity changes are deliberate config changes by the
  operator
