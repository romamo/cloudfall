# Cloudfall — Lean Canvas

> One-pager business model. The **evidence-based control plane** for moving a
> SaaS off cloud PaaS onto one self-hosted Debian server — hardened, monitored,
> and deployed with rollback. Under an hour as proven in the live M1–M5 runs;
> a cold first run pre-1.0 is closer to an afternoon. No Kubernetes. Once
> migrated, an always-on operator watches declared alerts, proposes
> evidence-backed remediations, and acts autonomously within declared policy
> where reversibility is proven — the full loop live-proven in the M7–M10 runs.

| | |
| --- | --- |
| **Stage** | Pre-1.0 · Open source · Operator proven live (M7–M10) |
| **License** | AGPL-3.0 (commercial exception reserved) |
| **Wedge** | Render → Hetzner |
| **As of** | 2026-09-11 |

The blocks below are numbered in canonical Lean Canvas fill order.

## 1 · Problem

- **PaaS margins bite at scale.** Render / Heroku / Fly bills run several times the underlying hardware cost once traffic is steady.
- **Leaving PaaS means pain.** The exits are Kubernetes complexity or fragile hand-rolled SSH scripts nobody trusts.
- **AI can't operate infra safely.** Agents invent shell commands over SSH — unauditable, no rollback, no proof it worked.

*Existing alternatives:* stay on PaaS · roll your own Ansible · adopt k8s · Kamal / Coolify / Dokku

## 2 · Customer Segments

- **Bootstrapped SaaS teams** running 1–20 apps and outgrowing PaaS pricing.
- **Solo founders & small teams** operating a Debian fleet without a platform hire.
- **AI-forward builders** who want an agent to run their infra safely.

*Early adopters:* cost-conscious Render / Heroku users on a Python + PostgreSQL stack, comfortable on Hetzner-class bare metal.

## 3 · Unique Value Proposition

**Leave PaaS in an afternoon and keep the PaaS feel — your own server, a tenth of the cost, no special skills, operated by an AI agent that can't fake success.**

No skills leak through: the typed state is the whole interface, the agent mediates it in plain language, and the founder never opens an SSH session. Evidence over inference: every deploy produces receipts, status is derived from validated observations, and Cloudfall never reports success it cannot prove — proof of outcome is the claim no one else makes. And because it is all plain systemd + nginx + Postgres on stock Debian, any Linux admin can take over without Cloudfall: no special skills required, no lock-in if you ever hire them.

*High-level concept:* Render-grade operational feel on hardware you own, with a flight recorder built in — it starts from your `render.yaml`, not an empty server.

## 4 · Solution

- **Declarative typed state.** Servers, services, and domains as YAML validated against JSON Schemas.
- **One resumable migrate.** Baseline → services → deploy → TLS → DNS cutover, persisted and restartable.
- **systemd, not containers.** Artifact releases with symlink rollback on long-lived Debian hosts.
- **Render importer.** Blueprint → state with an explicit gap report; nothing guessed silently.
- **Always-on operator.** Watches declared alerts and audited drift, writes every diagnosis as a receipted proposal, and executes autonomously only for operation classes licensed by declared policy and earned receipt history; DNS cutover, data deletion, and promotion confirm forever.
- **Proven promises.** Backups are restore-drilled on a timer with schema-valid receipts, and alert delivery to a real external destination is exercised evidence, not configuration.

## 5 · Channels

- Open-source repo & docs (GitHub)
- Migration playbooks: **Render → Hetzner** guides
- Developer communities — HN, r/selfhosted
- Agent / MCP ecosystem listings

## 6 · Revenue Streams

- **Autonomous operator subscription** (core stream, pricing hypothesis to
  validate): the always-on agent that manages the fleet 24/7 —
  evidence-backed, confirm-gated. The product side is no longer a hypothesis:
  the operator shipped and was proven live on 2026-09-10 (an induced failure
  remediated with one approval, then an identical failure remediated
  autonomously under declared policy in 2m06s). The AGPL tool and migration
  stay free: the importer is the acquisition motion, the operator is the
  monetization
- **Migration & support engagements** for teams that want the cutover done for
  them
- **Commercial license exception** for teams who can't ship AGPL

## 7 · Cost Structure

- **Core engineering** — schemas, SDK, engine roles, service catalog
- **CI + disposable test hosts** for live proving runs
- **Docs, migration guides, community support**

## 8 · Key Metrics

*Wedge (acquisition):*

- Completed migrations (Render → self-host)
- Time-to-cutover — target **< 1 hour**
- Audit-clean hosts (zero drift)
- Share of ops run through the SDK vs. manual SSH

*Operator (revenue):*

- Autonomous actions executed with receipts, per fleet per month
- Alert-to-resolution time for agent-handled incidents
- Inference cost per operated fleet vs. subscription price
- Operator subscription MRR and churn

## 9 · Unfair Advantage

- **Proof of outcome, not gated actions.** Rivals gate what an agent may do (RBAC, confirm flags, plan approval); only Cloudfall proves what happened: per-deploy receipts, status from validated observations, desired-vs-observed drift. It is the foundation of the status model — near-impossible to bolt on after the fact.
- **Autonomy downstream of evidence.** Autonomy is granted per operation class, licensed by declared policy and earned receipt history, never a global switch — competitors bolting agents onto imperative tools can offer autonomy only as recklessness. Proven live: the first failure required one approval (the receipt that earned the trust), the second identical failure was remediated with zero human involvement.
- **The unclaimed migration axis.** No competitor productizes PaaS exit; PaaS tools start from an empty server, and incumbents are structurally disincentivized (Render pays up to $10K in credits to pull workloads *in*).
- **State the user owns.** Typed, schema-validated YAML of the whole server: versionable, diffable, agent-readable. Category rivals are imperative dashboards.
- **Dual license.** AGPL-3.0 for the community, commercial exception reserved to the holder. Separates Cloudfall from its closest operational twin (Devopness, closed SaaS).
