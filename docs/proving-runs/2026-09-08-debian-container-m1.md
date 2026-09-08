# Proving run: M1 baseline on a disposable Debian 13 host

| | |
| --- | --- |
| **Date** | 2026-09-08 |
| **Scope** | M1 exit criteria: idempotent baseline, compliant audit |
| **Host** | Privileged Debian 13 (trixie) systemd container, SSH on 2222 |
| **Verdict** | Both criteria met; one engine bug found and fixed |

## Method

A disposable host was provisioned as a privileged `debian:13` container
running systemd as PID 1, with `openssh-server` (port 2222), `chrony`
(Debian's `systemd-timesyncd` refuses to start in containers), `dbus`, and
`python3` preinstalled, reachable only from loopback via a throwaway ed25519
key. A dedicated state directory declared the host honestly: an `overlay`
root filesystem, no software RAID, firewall allowing only `tcp/2222`, and the
standard package, service, timer, and sshd-configuration requirements.

The run used only public entry points, exactly as documented: `cloudfall
state validate`, `cloudfall-engine inventory render`, the `baseline.yml` and
`inspect.yml` playbooks with the rendered inventory, and `cloudfall audit`.

## Results

- **First baseline run**: failed at the firewall step (finding 1 below);
  after the one-line template fix it converged (`failed=0`), applying
  packages, project scaffolding, UTC time, key-only SSH policy, unattended
  upgrades, and the default-deny nftables ruleset. The active SSH control
  connection survived the firewall cutover as designed
- **Second baseline run**: `changed=0` — the baseline is idempotent
- **Inspect + audit**: `cloudfall audit` exited `0` with 18/18 checks
  compliant, including the managed nftables table, `drop` input policy,
  declared inbound allowances, `apt-daily.timer` running and enabled, and the
  sshd configuration hash evidence

## Findings

1. **nftables template rendered invalid syntax for described rules.** Under
   Ansible's `trim_blocks` semantics, a rule carrying a `description`
   swallowed the newline before the input chain's closing brace, so
   `nft --check` rejected the file and the baseline halted. Fixed by
   restructuring the rule loop in `nftables.conf.j2`
2. **The template test suite could not catch finding 1.** The unit tests
   rendered the template with Jinja defaults (`trim_blocks=False`) and
   asserted substrings, so they passed against output the real engine never
   produces. The test environment now mirrors Ansible's trim semantics and a
   regression test asserts line structure

Finding 1 validates the proving-run premise directly: the failure was
invisible to lint, type checks, unit tests, and `--syntax-check`, and
surfaced only when a real `nft` binary validated a really rendered file.

## Caveats

A privileged container is a weaker proxy than the wedge target:

- No RAID, real disks, boot loader, or kernel of its own; the storage and
  RAID portions of host profiles were not exercised
- Time was synchronized by chrony against the container host's clock;
  `systemd-timesyncd` behavior on real hardware remains unexercised
- Inbound traffic reaches the container through Docker's proxy, so the
  firewall was proven against the container's own network namespace only
- The M1 observability slice (Loki/Grafana/Alloy host metrics) and the
  M2–M5 layers (services, deploy, import, migrate) were not in scope

The README's "not yet validated on live hosts" disclaimer therefore stands
until this run is repeated on a real bare-metal or VPS Debian host and the
remaining layers join the proving batch.

## Reproduce

```console
uv run cloudfall state validate <state-dir> --schemas state/schemas/v1
uv run cloudfall-engine inventory render <state-dir> --schemas state/schemas/v1 --output tmp/inventory.json
ANSIBLE_CONFIG=engine/ansible/ansible.cfg uv run ansible-playbook --inventory tmp/inventory.json engine/ansible/playbooks/baseline.yml
ANSIBLE_CONFIG=engine/ansible/ansible.cfg uv run ansible-playbook --inventory tmp/inventory.json engine/ansible/playbooks/baseline.yml  # expect changed=0
ANSIBLE_CONFIG=engine/ansible/ansible.cfg uv run ansible-playbook --inventory tmp/inventory.json engine/ansible/playbooks/inspect.yml --extra-vars cloudfall_inspect_output_directory=$PWD/tmp/observed
uv run cloudfall audit <state-dir> --schemas state/schemas/v1 --observed tmp/observed  # expect exit 0
```
