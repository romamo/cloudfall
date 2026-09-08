# Cloudfall — Lean Canvas

> One-pager business model. The **AI-native control plane** for moving a SaaS off
> cloud PaaS onto one self-hosted Debian server — hardened, monitored, and
> deployed with rollback, in under an hour. No Kubernetes.

| | |
| --- | --- |
| **Stage** | Pre-1.0 · Open source |
| **License** | AGPL-3.0 (commercial exception reserved) |
| **Wedge** | Render → Hetzner |
| **As of** | 2026-09-08 |

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

**Leave PaaS in an hour — and let an AI agent operate the server it can't fake success on.**

Evidence over inference: every deploy produces receipts, status is derived from validated observations, and Cloudfall never reports success it cannot prove.

*High-level concept:* Terraform-grade rigor for *getting off the cloud* — driven by an agent instead of a platform team.

## 4 · Solution

- **Declarative typed state.** Servers, services, and domains as YAML validated against JSON Schemas.
- **One resumable migrate.** Baseline → services → deploy → TLS → DNS cutover, persisted and restartable.
- **systemd, not containers.** Artifact releases with symlink rollback on long-lived Debian hosts.
- **Render importer.** Blueprint → state with an explicit gap report; nothing guessed silently.

## 5 · Channels

- Open-source repo & docs (GitHub)
- Migration playbooks: **Render → Hetzner** guides
- Developer communities — HN, r/selfhosted
- Agent / MCP ecosystem listings

## 6 · Revenue Streams

- **Commercial license exception** for teams who can't ship AGPL
- **Managed / hosted fleet control plane** (subscription)
- **Migration & support engagements** for the cutover itself

## 7 · Cost Structure

- **Core engineering** — schemas, SDK, engine roles, service catalog
- **CI + disposable test hosts** for live proving runs
- **Docs, migration guides, community support**

## 8 · Key Metrics

- Completed migrations (Render → self-host)
- Time-to-cutover — target **< 1 hour**
- Audit-clean hosts (zero drift)
- Share of ops run through the SDK vs. manual SSH

## 9 · Unfair Advantage

- **Evidence-based audit model.** Receipts and desired-vs-observed drift detection — hard to bolt on after the fact.
- **Agent-native by design.** Stable SDK + MCP server; every mutation gated behind an explicit confirm handshake.
- **Dual license.** AGPL-3.0 for the community, commercial exception reserved to the holder.
