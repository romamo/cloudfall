# Decision: Debian stays the base, no NixOS

Date: 2026-09-14. Status: decided, revisit on the triggers listed below.

## Question

Is it worth moving Cloudfall's required host operating system from Debian
to NixOS?

## Decision

No. Cloudfall keeps stock Debian as the only supported base and keeps its
engine a mutable-host engine (Ansible roles writing files, installing
packages, and managing systemd units). The engine contract described in
[`ARCHITECTURE.md`](../../ARCHITECTURE.md) stays strict so that a NixOS
backend could be added later as a second engine consuming the same typed
configuration, without touching the SDK.

## What Debian is to the product

Debian is a principle in [`MANIFESTO.md`](../../MANIFESTO.md), not a
placeholder. The exit test is that any Linux admin can take over the server
with nothing but Debian knowledge. Every proving run from M0 to M10 was
executed against stock Debian 13 on Hetzner, and the roles, templates, and
playbooks (~5,200 lines across 107 files) encode that host model.

## Why NixOS does not pay

- **It fails the exit test.** NixOS is the one Linux base where an ordinary
  admin cannot take over with general Linux knowledge. The generated `/etc`,
  the absence of a package manager in the usual sense, and the Nix language
  itself are the most-cited reasons teams decline it
- **The engine would be replaced, not ported.** The roles install packages,
  render templates into `/etc`, drop systemd units, and run the certbot
  webroot flow. None of that is the correct action on NixOS. A NixOS base
  means a generator that emits `configuration.nix` from the typed YAML,
  which is a second engine and discards every proving run
- **Provisioning friction.** Hetzner ships no first-class NixOS image, so
  every server would begin with nixos-anywhere or nixos-infect. A stock
  Debian 13 cx23 is the zero-objection starting point today
- **Wrong audience.** The wedge is Python-heavy SaaS teams leaving a Render
  bill. They accept "it is Debian, systemd, nginx, and PostgreSQL". They will
  not learn Nix to read their own server
- **Stage of the product.** Version 0.1.0, one maintainer, one real workload
  in migration. A base swap freezes the roadmap for longer than the Rust
  rewrite rejected in
  [`2026-09-14-no-rust-rewrite.md`](2026-09-14-no-rust-rewrite.md) would have

## What NixOS does better, and what Cloudfall takes from it

- **Drift is impossible by construction on NixOS.** Cloudfall detects drift
  after the fact through the audit. This is the one honest gap. The answer
  inside Cloudfall's model is to keep the config-versus-observed audit strict
  and to converge on drift, not to change the operating system
- **A rebuild is reproducible on NixOS.** Cloudfall proves the equivalent
  empirically: a receipted baseline, a receipted restore drill, and an audit
  that exits non-zero on divergence. Evidence of the operation, rather than
  purity of the host, is the product

## Triggers to revisit

- **Paying users asking for it.** A fleet where reproducibility outranks
  takeover-ability is a real segment, but it is not the wedge
- **Drift becoming a recurring operational pain** in proving runs or in the
  LedgrAI workload, rather than a theoretical concern the audit already covers
- **A second engine appearing for any other reason.** If the engine contract
  ever gains a non-Ansible implementation, a NixOS generator becomes a
  cheaper addition than it is today

## Consequences

- Debian 13 remains the only supported base; the landscape document keeps
  NixOS classified as a rejected alternative
- No NixOS-specific code, roles, or documentation paths are added
- The engine stays reachable only through its command-line and playbook
  contracts, so a future NixOS backend can be a second engine rather than a
  fork of the SDK
- This decision applies to Cloudfall's base only. It says nothing about
  developer workstations or the maintainer's own hosts
