# The Cloudfall Manifesto

Cloudfall exists because leaving a PaaS should not require a platform team,
and letting an AI agent run a server should not require trusting its word.
These are the principles the project is built on. They are constraints we
accept, not aspirations we advertise.

## 1 · You should own your infrastructure

A steady-traffic SaaS pays a PaaS several times the price of the hardware it
runs on, forever, in exchange for not having to think about servers. By the
month the bill crosses $1,000 you are funding a platform team you never
hired. That trade made sense when the alternative was Kubernetes or
hand-rolled SSH scripts. It stops making sense the moment the operational
feel of a PaaS — health-gated deploys, monitoring, databases that provably
restore — can live on one or two servers you own. The bill should buy
computers, not margins.

## 2 · Evidence over inference

Status is derived from validated observations, never from the fact that a
command exited zero or a playbook exists. Deploys produce receipts. Audits
compare the declared state with what a read-only inspection actually found
on the host, and report drift with distinct exit codes. Cloudfall never
reports success it cannot prove; when it does not know, it says `unknown`
instead of guessing.

## 3 · An agent that can't fake success

AI agents will operate infrastructure. The question is on what terms. An
agent improvising shell commands over SSH is unauditable and unaccountable:
no rollback, no proof, no record. Cloudfall gives agents a stable CLI and
structured JSON results instead of a terminal, exposes read-only evidence
freely, and gates every server-changing action behind an explicit
confirmation handshake. The same evidence rules bind the agent that bind a
human: it cannot claim an outcome the receipts do not show.

## 4 · The state is the interface

The whole platform is typed YAML validated against JSON Schemas: servers,
services, applications, domains. It is versionable, diffable, and readable
by humans and agents alike. No execution logic hides in it, no engine
silently rewrites it, and no secret values ever live in it. If a fact about
your platform matters, it is either declared in the state or observed as
evidence; there is no third place.

## 5 · Boring on purpose

Applications run as native systemd services with artifact releases and
symlink rollback, behind nginx, next to loopback PostgreSQL, on stock
Debian. No Kubernetes, no container orchestrator, no bespoke daemon that
only we understand. The exit test: any Linux admin can take over the server
without Cloudfall installed. A tool that promises to free you from one
platform must not become the next one.

## 6 · Never guess silently

The Render importer emits a report of everything it did not import, every
assumption it made, and every step still required before cutover. The
migrate plan pauses at DNS verification rather than pretending a cutover is
safe to automate blindly. Validation fails fast on malformed input instead
of repairing it. Wherever Cloudfall must choose between convenient and
explicit, it chooses explicit.

## 7 · Proven live or labeled unproven

Every implemented layer is exercised on disposable servers against real
targets: real certificates, real DNS flips on owned domains, real rollbacks
of deliberately bad releases. What has not been proven live is listed as
such in the README and the roadmap. "Pre-1.0, honestly labeled" is not
marketing copy; it is the release policy.

## 8 · Open, business model included

Cloudfall is AGPL-3.0, and the business model is developed in the open next
to the code: the lean canvas, the pricing hypotheses, the revenue plan. The
free tool must be genuinely sufficient to leave a PaaS and run your own
server; sustainability comes from the autonomous operator, support, and
commercial licensing, never from crippling the open core. You should be
able to read exactly how this project intends to survive, and hold it to
that.

---

If Cloudfall ever reports a deploy it cannot prove, hides a gap it knows
about, or requires Cloudfall itself to keep your server running, that is a
bug against this document. File it.
