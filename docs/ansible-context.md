# How Ansible works

Context note for anyone working on the Cloudfall engine, which drives Ansible under the hood

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
