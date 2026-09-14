# Where Cloudfall sits: the tool landscape

Status: reference positioning, written 2026-09-14. This document places
every neighboring tool at once. Each tool is classified by the
layer it occupies and by its relationship to Cloudfall: **built on**
(Cloudfall uses it), **replaces** (Cloudfall does the same job a different
way), **complements** (runs next to Cloudfall, no overlap), **alternative**
(a different approach to the same problem that Cloudfall rejects), or
**migrates from** (the place a workload leaves).

## The stack, top to bottom

```text
  who runs it        Cloudfall operator, PagerDuty, Devopness, AI-SRE tools
  how you deploy     Cloudfall deploy, Coolify, Dokku, Kamal, Compose, k8s
  what it runs on    systemd + nginx + PostgreSQL   (Cloudfall's choice)
  how the OS is      Ansible, Puppet, Salt   |   NixOS (OS is the config)
  configured
  which OS           Debian (Cloudfall requires)  |  NixOS, Ubuntu, Alpine
  how the server     Terraform / OpenTofu, Pulumi, hcloud CLI, cloud-init
  is created
  where              Hetzner, OVH, any VPS   |   Render, Heroku, Fly (PaaS)
```

Cloudfall occupies three of these rows itself: how you deploy, how the OS is
configured (through its embedded Ansible engine), and who runs it (the
operator). It requires a specific answer for one row (Debian), has no
opinion on the row below (server creation), and treats the PaaS column as
the place workloads come from.

## Tool by tool

| Tool | Layer | Relationship | In one sentence |
|---|---|---|---|
| Ansible | OS configuration | Built on | Cloudfall's engine is Ansible behind a playbook contract; you never write playbooks unless you add a fleet-specific role |
| Terraform / OpenTofu, Pulumi | Server creation | Complements | Creates the Hetzner server, DNS zone, and network; hands a Debian host to Cloudfall and stops |
| hcloud CLI, cloud-init | Server creation | Complements | The manual or scripted way to get the same Debian host; the proving runs use hcloud directly |
| Debian | Operating system | Required | Stock Debian is a principle (any Linux admin can take over), not a placeholder |
| NixOS | Operating system and its configuration | Alternative | Makes the machine a build output; Cloudfall makes the operation provable on a mutable host |
| Puppet, Chef, Salt | OS configuration | Alternative | Same layer as Ansible; Cloudfall picked Ansible and hides it |
| Coolify, Dokploy, CapRover | Self-hosted PaaS | Replaces | Same job (apps behind a proxy with TLS on your server); Docker containers driven from a dashboard versus native systemd driven from typed config with receipts |
| Dokku | Self-hosted PaaS | Replaces | Heroku-style git push to containers on one host; no typed state, no evidence, no operator |
| Kamal | Deployment | Replaces | Imperative container deploys over SSH with health checks; closest in spirit on deploys, but containers, no config-versus-observed audit, no databases, no operator |
| Docker Compose | Deployment | Alternative | Cloudfall runs applications as native units, not containers; container services land in the import gap report today |
| Kubernetes, k3s, Nomad, Swarm | Orchestration | Alternative | The thing Cloudfall exists to avoid at the 1 to 100 application scale; movability is a config change plus convergence, not a scheduler |
| ArgoCD, Flux | GitOps | Alternative | Kubernetes-only reconcilers; Cloudfall's config repository plus audit is the same idea without a cluster |
| Render, Heroku, Fly.io, Railway | Managed PaaS | Migrates from | The bill Cloudfall replaces; the Render importer, guided data migration, and DNS-paused cutover exist for this |
| Neon, Supabase, RDS | Managed database | Replaces or complements | Loopback PostgreSQL with receipted backups and restore drills replaces the compute bill; a team may keep a managed database and move only the backend |
| Grafana, Loki, Prometheus, Alloy | Observability | Built on | Bundled as one guarded logging stack over mTLS with typed alert rules |
| Datadog, New Relic, Better Stack | Observability SaaS | Replaces | Per-host pricing at this scale is another margin; the bundled stack covers logs, metrics, and alerts |
| sops, age | Secrets | Built on | Secrets encrypted in the repository and rendered to environment files outside the config |
| Vault, Doppler, Infisical | Secrets | Alternative | Central secret servers; Cloudfall keeps the sops file model for one to two servers |
| certbot, Let's Encrypt | TLS | Built on | Issued through the nginx role's webroot flow with timer compliance |
| pgBackRest, Barman, restic, Borg | Backups | Alternative today | Cloudfall runs its own receipted dumps and restore drills; point-in-time recovery on the roadmap may adopt one of these |
| PagerDuty, Opsgenie | Operations | Replaces at this scale | The operator remediates what its receipts prove reversible and pages you only for what it cannot undo |
| Devopness | Operations SaaS | Competes | The closest operational twin: closed SaaS, imperative dashboard; Cloudfall is AGPL with typed state the user owns |
| AI SRE agents (generic) | Operations | Alternative | Agents bolted onto imperative tools; Cloudfall grants autonomy per operation class from verified receipt history |
| Claude Code, Codex, any MCP client | Agent surface | Complements | Drive Cloudfall through `cloudfall-mcp`; read-only tools free, mutating tools behind a confirmation handshake |

## What Cloudfall adds and what it replaces

Cloudfall **adds** something no tool on the list has: a layer of evidence
between "the command exited zero" and "the platform is healthy". Receipts
for every mutation, read-only observations, an audit that reports drift
with exit codes, restore drills that prove a backup, and an operator whose
autonomy is derived from that evidence. Every other tool here treats
success as the absence of an error.

Cloudfall **replaces** the self-hosted PaaS category (Coolify, Dokku,
Kamal), the managed PaaS bill (Render, Heroku), hand-rolled Ansible, and,
at one-to-two-server scale, observability and paging SaaS.

Cloudfall **does not replace** server provisioning (Terraform, hcloud),
the operating system (Debian), or the agent that talks to it (any MCP
client). It uses Ansible, Grafana/Loki, sops/age, and certbot underneath
and does not pretend otherwise.

Cloudfall **rejects** orchestration (Kubernetes and friends), containers
as the application unit, and NixOS as the base, each as a deliberate
trade: the exit test is that any Linux admin can take over the server with
nothing but Debian knowledge.

## The two axes that separate Cloudfall from its closest rivals

The self-hosted PaaS tools are the real category rivals, and two axes
split them from Cloudfall:

1. **Where the truth lives.** Coolify, Dokploy, and CapRover hold state in
   their own database behind a dashboard; Kamal holds it in a YAML file plus
   whatever the containers are doing. Cloudfall holds it in schema-validated
   YAML the user owns, and audits the servers against it
2. **What counts as success.** All of them report a deploy as successful
   when the container starts or the health check passes once. Cloudfall
   writes a receipt, keeps observing, and the operator acts on drift and
   alerts afterwards

The migration axis is unclaimed by any of them: every self-hosted PaaS
starts from an empty server, and the managed PaaS providers are
structurally disincentivized to help you leave.

## A typical complete stack

For the wedge use case, the full toolchain is:

- Terraform or the hcloud CLI creates a Debian 13 server on Hetzner
- Cloudfall baselines it, installs PostgreSQL, Redis, nginx, and the
  logging stack, deploys the application from a git ref with a health gate,
  and pauses for DNS verification
- Cloudflare or your registrar holds DNS; Cloudfall verifies the flip, it
  does not perform it
- An MCP client such as Claude Code drives the whole migration through
  `cloudfall-mcp`, confirming each server-changing step
- The Cloudfall operator runs afterwards on a small always-on management
  host, watching alerts and audits

Nothing on that list is NixOS, Kubernetes, Docker, or a PaaS.
