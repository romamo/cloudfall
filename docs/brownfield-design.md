# Brownfield design: Ansible is the hand, Cloudfall is the brain

Status: proposed concept. Nothing in this document is implemented. It
re-roots Cloudfall on an existing Ansible inventory instead of Cloudfall's
own resource documents. It builds on the decision in
[no Ansible replacement](decisions/2026-09-14-no-ansible-replacement.md)
and changes the [project vocabulary](../ARCHITECTURE.md): the fleet is no
longer declared by Cloudfall, it is read from Ansible.

## Problem

Most teams that would benefit from Cloudfall already run Ansible. They have
an inventory, roles and playbooks that work. What they lack is not
automation; it is an operator who watches the fleet, decides what to run,
reads the output and asks before doing harm. Today that operator is a
human, and the job is manual.

Two obvious paths are wrong:

- **Import and leave.** Reading their inventory once into Cloudfall
  resources creates a second source of truth that drifts on the first edit
- **Two inventories side by side.** Cloudfall's rendered inventory next to
  theirs works technically (Ansible merges sources) but doubles the config
  and splits ownership of every host

The rule for this design: one config, which is theirs. Ansible executes,
Cloudfall decides.

## Positioning

Cloudfall is not "Ansible for agents". Ansible is already callable by an
agent. Cloudfall is the operator layer an agent is missing:

| Agent needs | Ansible gives | Cloudfall adds |
| --- | --- | --- |
| Know the fleet | Inventory, facts | One structured snapshot per host |
| Know what it may do | Any playbook, any host | A catalog of declared operations with risk levels |
| See the result | Verbose text, JSON callback | Per-host outcome, diff, evidence |
| Be stopped before harm | `--check` if remembered | A gate it cannot bypass |
| Change config safely | Nothing; raw file writes | One edit primitive: round-trip, schema-checked, diffed, reversible |
| Explain itself | Nothing | Audit log with the evidence behind each decision |

The defensible part is the contract: an agent acts only through declared
operations and safe edits, on a fleet it can observe, behind a gate it does
not control.

## The seven parts

### 1. Fleet reader

Cloudfall declares no servers. It runs `ansible-inventory --list` against
the team's inventory (static INI or YAML, or a dynamic plugin) and reads
hosts, groups and connection variables. The typed model in
`sdk/src/cloudfall/inventory.py` is populated from that output instead of
from resource documents.

Everything Ansible does not model lives in a `cloudfall` variable block in
the team's own `group_vars` and `host_vars`:

```yaml
# host_vars/web-3.yml
cloudfall:
  environment: production
  server_type: web
  owns: [firewall, nginx]
  components: [ledgrai-backend]
```

This is ordinary Ansible. Their playbooks can read the same variables.
There is nothing to sync. Cloudfall's own state is confined to this key;
removing Cloudfall means deleting the `cloudfall` blocks. Any other change
to their files goes through the safe edit primitive in part 3, never
through a whole-file rewrite.

The `owns` list says which subsystems Cloudfall's own roles may touch on
that host. Anything not listed belongs to the team's roles. This is the
answer to the real conflict, which is not inventory but two role sets
managing the same file.

### 2. Operations catalog

Every playbook or role the team already runs becomes an operation:

```yaml
# operations/deploy.yml
id: deploy
playbook: playbooks/deploy.yml
risk: mutating
targets: component
inputs:
  version: string
preconditions:
  - health: ok
verify:
  playbook: playbooks/health.yml
```

An operation has a typed input schema, a risk level (`read`, `mutating`,
`destructive`), a target scope, preconditions and a verify step. The
catalog is the agent's tool list, generated from what the team runs today.
Cloudfall's own roles (`cloudfall_bootstrap`, `cloudfall_firewall`,
`cloudfall_postgresql`, ...) ship as a starter pack of operations for teams
that have no baseline yet. They are optional.

### 3. Safe edits

Ansible ships nothing that edits its own files: `ansible-inventory` exports,
`ansible-config init` generates, ansible-lint validates. An agent editing
Ansible today rewrites raw YAML with no schema and no gate. Cloudfall
provides one edit primitive for every YAML file Ansible reads, and the
concept commands are wrappers over it:

```
cloudfall edit host_vars/web-3.yml set cloudfall.owns '[firewall, nginx]' --check
cloudfall edit hosts.yml set all.children.production.hosts.web-4 '{ansible_host: 10.0.0.14}'
cloudfall edit playbooks/deploy.yml set '[0].tasks[3].retries' 5
cloudfall edit playbooks/deploy.yml unset '[0].tasks[3].ignore_errors'
cloudfall add server web-4 --address 10.0.0.14 --group production
```

Every call runs the same pipeline:

1. **Parse round-trip** with ruamel so comments, anchors, ordering and
   quoting survive. Only the addressed path changes
2. **Validate by file kind** before writing. Inventory and vars files
   against the ansible-lint inventory and vars schemas; playbooks against
   the ansible-lint playbook schema and `ansible-playbook --syntax-check`;
   task module arguments against `ansible-doc --json` argument specs; the
   `cloudfall` key against Cloudfall's own schema. Malformed input fails
   with a structured error and nothing is written
3. **Diff and gate.** Without `--yes`, print the unified diff and stop.
   Edits are `mutating`: a playbook edit changes what future runs do, an
   inventory edit changes where they run
4. **Write, then re-validate** on disk with the same checks, plus
   `ansible-inventory --list` for inventory files. Any failure restores the
   pre-write copy, which is kept until the post-write check passes
5. **Record** the path, the diff and the approver in the audit log

Wrappers such as `add server`, `remove server`, `set` and `unset` add
semantic checks on top: `add server` refuses a host that already exists in
the merged inventory or a group that does not, and names the file it will
write to.

Limits, stated to the user rather than worked around:

- **INI inventories and dynamic sources** are not editable. The command
  fails and names the file or plugin to change by hand
- **Semantic correctness** of a playbook is not checked. Syntax check and
  argument specs catch a wrong parameter, not a task that stops the wrong
  service. Check mode on the next run is the gate for that
- **Jinja templates** are not YAML and are out of scope
- **Cloudfall never rewrites a whole file** or restructures groups, roles or
  plays. Larger changes are drafted on a branch and merged by a human

### 4. Observation

Facts, service state, disk, recent logs and the last run result are
gathered into one structured snapshot per host. The agent reads the
snapshot, never raw SSH output. Snapshots are cached with a timestamp so
the agent can tell how stale its view is and refresh only what it needs.

### 5. Approval and audit

| Risk | Behaviour |
| --- | --- |
| `read` | Runs freely |
| `mutating` | Check mode first, diff shown, then waits for approval unless policy allows |
| `destructive` | Always waits for a human |

Every decision records the operation, the targets, the snapshot it was
based on, the check-mode diff, who approved and the outcome. The audit
log is the evidence the agent shows when asked why.

### 6. Agent surface

One CLI and one MCP server, same commands for humans and agents, JSON
output everywhere. The loop is fixed:

1. Observe: read snapshots
2. Decide: pick an operation and targets, state why
3. Preview: check mode and diff
4. Approve: gate by risk
5. Run: execute through Ansible
6. Verify: run the operation's verify step, update snapshots

### 7. Starter roles

The existing engine roles stay, packaged as optional operations. A
greenfield team gets a plain Ansible inventory generated by `cloudfall init`
so that greenfield and brownfield share one code path.

## What changes in the codebase

| Keep | Drop | Re-root |
| --- | --- | --- |
| Typed domain and JSON schemas | Resource documents as the fleet source | Schemas validate the `cloudfall` var block instead of standalone files |
| Operator, approval and audit logic | Outward inventory renderer | Renderer becomes a `group_vars` / `host_vars` writer |
| MCP server and CLI | `cloudfall add *` as resource writers | `cloudfall add *` become wrappers over the safe edit primitive |
| | | `cloudfall init` emits a plain Ansible inventory |
| Read-only dashboard | | Dashboard reads snapshots |
| Engine roles and playbooks | | Registered as starter operations |

## Open questions

- **Hostname mapping.** The team's inventory name may differ from the
  machine hostname and from any Cloudfall id. The inventory name is the
  key; everything else is a variable
- **Schema strictness of the var block.** Reuse the existing JSON schemas,
  but decide whether unknown keys under `cloudfall` fail or warn
- **Dynamic inventories.** Where do `host_vars` go when hosts come from a
  cloud plugin? Likely `group_vars` keyed by tag, plus a Cloudfall-side
  overlay file only for hosts that need one
- **Policy for auto-approval.** Which mutating operations may run without a
  human, and under what conditions (environment, time window, blast radius)

## Not in scope

- Replacing Ansible or owning the team's inventory and playbook files;
  Cloudfall edits them only through the safe edit primitive, path by path
- A write UI; approvals stay in the CLI (see the UI scope decision)
- Multi-tool execution (Terraform, Kubernetes); Ansible is the only hand
  for now
