# How Ansible works

Context note for anyone working on Cloudfall. Ansible is the hand: it
executes on the servers. The agent is the brain: it decides what to run.
Cloudfall is the conscience: it declares what the agent may run, gates it,
verifies the result and keeps the record. Today Ansible is Cloudfall's
embedded engine; under the [brownfield design](brownfield-design.md) it is
the team's own repository, read in place

## Architecture

- Ansible is agentless: nothing persistent is installed or runs on managed hosts
- Everything runs from a single control node
- The control node connects to Linux hosts over SSH and to Windows hosts over WinRM
- Targets need SSH and a Python interpreter (Linux) or PowerShell (Windows); both are standard on servers

## Playbooks, tasks, modules

- Playbooks are YAML instruction manuals written by the operator
- A playbook is a list of plays; each play maps a group of hosts to a list of tasks
- Each task calls one module with arguments
- Modules are pre-written Python (or PowerShell) programs shipped with Ansible or a collection; they do the actual work
- Roles and collections bundle tasks, modules, templates and defaults for reuse

## Execution flow

1. The control node parses the playbook and builds the inventory of target hosts
2. For each task, it packs the module and its helper library into a single Python payload (AnsiballZ)
3. It opens an SSH connection and copies the payload to a temporary directory on the target
4. It runs the payload with the remote Python interpreter
5. The module returns a JSON result (`changed`, `failed`, facts, output)
6. The temporary files are removed immediately
7. Results are collected per host and the next task runs

Exceptions: the `raw` module sends a bare command without uploading Python, and Windows hosts run PowerShell modules over WinRM

## Idempotency

- Idempotency is a property of modules, not a global guarantee
- Most built-in modules (`apt`, `file`, `copy`, `template`, `service`, `user`) compare desired state with current state and act only when they differ, reporting `changed` accordingly
- `shell` and `command` run every time; guard them with `creates`, `removes` or `changed_when`
- Check mode (`--check`) reports what would change without changing anything; `--diff` shows the file-level diff

## Core value

- Safe, repeatable automation: rerunning a playbook is cheap and converges the host toward the declared state
- No agents to install, patch or keep alive on servers
- Plain YAML plus SSH means the whole toolchain is inspectable and version-controlled

## Inventory and variables

The parts the brownfield design depends on:

- The inventory names hosts and groups; static INI or YAML files, or a dynamic plugin that queries a cloud API
- `all` contains every host; a host may belong to many groups; groups may nest through `children`
- Variables attach to hosts and groups: inline in the inventory, or in `host_vars/<host>.yml` and `group_vars/<group>.yml` next to it, or in a directory of the same name holding several files
- Precedence is fixed and long (over twenty levels): roughly `all` group vars, then parent groups, then child groups, then host vars, then play vars, then `--extra-vars`, which always wins
- `ansible-inventory --list` prints the merged result; in process, `InventoryManager` and `VariableManager` give the same with the precedence applied and vault decrypted
- Nothing in Ansible edits these files; `ansible-inventory` exports, `ansible-config init` generates, ansible-lint validates
- `ansible.cfg` and environment variables are read into module-level state at import, so one process serves one project

A `cloudfall` key in `group_vars` or `host_vars` is therefore an ordinary variable: Ansible merges it by the same rules, the team's own playbooks can read it, and deleting it removes Cloudfall's state.

## Results and check mode

- Every task returns JSON per host: `changed`, `failed`, `skipped`, `msg`, facts, and module-specific output
- Callback plugins and ansible-runner turn the run into structured events; today Cloudfall runs `ansible-playbook` as a subprocess through its engine contract and reads receipts the playbooks write, and the brownfield design names ansible-runner as the way to get per-host outcomes instead of console text
- `--check` with `--diff` is a preview: what would change and the file-level diff, without changing anything. It is the "preview" step of the agent loop
- Check mode is not an audit: it compares the playbook against the host, not a declared model of the fleet against the host, and modules that do not support it skip silently
- `--syntax-check` parses a playbook without running it; `ansible-doc --json` returns a module's argument spec

## What Ansible does not do

The gaps Cloudfall fills, and the reason it exists next to Ansible rather than instead of it:

- **No declared tool list.** Any playbook runs on any host; nothing says which ones an agent may call, with what inputs, at what risk
- **No verify step.** A playbook that ran is not a service that is healthy; Ansible reports `changed`, not "true on every host"
- **No record.** Ansible's output says what a run did; nothing records what the fleet looked like when it was decided, what was proposed, who approved and what verify reported
- **No gate.** `--check` is voluntary and the agent chooses whether to remember it
- **No file editing.** An agent that changes an inventory or a playbook rewrites raw YAML with no schema, no diff and no rollback

Ansible stays the hand for all of these. Cloudfall adds the catalog, the verify step, the audit entry, the gate and the safe edit on top, and none of them require a playbook to change.
