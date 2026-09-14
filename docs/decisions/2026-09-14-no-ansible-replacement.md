# Decision: Ansible stays the engine, no replacement

Date: 2026-09-14. Status: decided, revisit on the triggers listed below.

## Question

Is there a better execution engine than Ansible for Cloudfall, and is it
worth switching now?

## Decision

No. Cloudfall keeps Ansible as its only execution engine, hidden behind the
command-line and playbook contracts described in
[`ARCHITECTURE.md`](../../ARCHITECTURE.md). The one alternative that fits
Cloudfall's model, pyinfra, is recorded as a revisit trigger rather than a
plan.

## What Ansible is to the product

The engine is a ~940-line Python wrapper that shells out to
`ansible-playbook`. The roles, templates, and playbooks it drives (~5,200
lines across 107 files) are the part that touches servers, and every proving
run from M0 to M10 was executed through them. Users never write playbooks
unless they add a fleet-specific role, so Ansible's ergonomics cost the
maintainer, not the user.

## Why Ansible stays

- **The roles are the proven asset.** Any replacement discards the M0 to M10
  proving runs, the same objection that decided
  [`2026-09-14-no-rust-rewrite.md`](2026-09-14-no-rust-rewrite.md) and
  [`2026-09-14-no-nixos-base.md`](2026-09-14-no-nixos-base.md)
- **Module maturity is the whole job.** apt, systemd, the PostgreSQL
  collection, community.general, and the certbot webroot flow exist and are
  idempotent. Every alternative makes Cloudfall write those by hand
- **It passes the exit test.** A Debian admin can read a playbook and a
  Jinja template. Takeover-ability is a principle in
  [`MANIFESTO.md`](../../MANIFESTO.md), so readability of the engine's
  artefacts matters more here than in a typical shop
- **It is already hidden.** The engine contract means a switch is an
  engine-internal change, so there is no urgency to make it before the
  contract has been exercised by more than one workload
- **Agents write it well.** The AI-native pitch depends on agents adding
  fleet-specific roles; Ansible is the configuration language agents know
  best

## Where Ansible hurts this project

These are real costs, recorded so the triggers below are measurable:

- **Speed.** Fork-per-task execution and serial SSH round trips make
  convergence slow. The operator will feel this once it converges on drift
  repeatedly rather than once per deploy
- **Check mode is weak evidence.** The config-versus-observed audit is the
  product. Ansible's check mode is unreliable for `shell` and `command`
  tasks, template side effects, and handlers, so the audit cannot lean on it
- **Structured output is awkward.** Receipts need a JSON callback plugin or
  stdout parsing rather than typed return values from the engine
- **Interpreter coupling.** The `ansible-core` pin in `pyproject.toml` ties
  Cloudfall's Python upgrade cadence to Ansible's release train

## Alternatives considered

| Tool | Verdict | Why |
|---|---|---|
| pyinfra | Revisit trigger | Same agentless SSH, mutable-host model; operations are Python functions callable in-process with typed results, real dry-run diffs, and parallel SSH. Small module ecosystem, so nginx, PostgreSQL, and certbot logic would be Cloudfall's to write |
| Salt (salt-ssh) | Rejected | Heavier surface, declining mindshare, no advantage at one-to-two servers |
| Puppet, Chef | Rejected | Agent-based, Ruby ecosystem, wrong audience |
| Fabric, raw SSH scripts | Rejected | No idempotence, no module ecosystem; every role becomes bespoke shell |
| In-house SSH executor | Rejected for now | The trigger named in the Rust decision; a product decision that discards the roles |
| NixOS, Kamal, Compose, Kubernetes | Rejected | Already classified in [`landscape.md`](../landscape.md) as alternatives Cloudfall does not take |

pyinfra is the only entry that keeps the Debian base, the systemd unit
model, and the exit test while removing the four costs above. It is, in
effect, the in-house SSH executor from the Rust decision maintained by
someone else.

## Triggers to revisit

- **Convergence is measurably slow.** Operator drift convergence on the
  proving-run hosts or the LedgrAI workload takes long enough to change how
  often the operator can run
- **The `ansible-core` pin blocks a Python upgrade** Cloudfall wants, with
  no compatible Ansible release available
- **Receipts need typed in-process results.** Parsing playbook output stops
  being adequate evidence for the audit or the operator's autonomy rules
- **The engine contract gains a second implementation** for any other
  reason, which lowers the cost of a third

## How a switch would happen, if a trigger fires

Migrate one role behind the existing engine contract as a pilot, run the
relevant proving run against it, and compare receipts. Do not swap the
engine wholesale. The contract exists so that piecewise replacement is the
default path.

## Consequences

- Ansible remains the only engine; the landscape document keeps it
  classified as built on
- The four costs above are tracked as operational observations in proving
  runs, not fixed pre-emptively
- The engine stays reachable only through its command-line and playbook
  contracts, so a pyinfra or in-house engine can replace a role at a time
- No pyinfra code, dependencies, or documentation paths are added
