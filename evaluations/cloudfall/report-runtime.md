# cloudfall — Runtime Brief

Generated: 2026-09-14 | CLI version: 0.1.0 | Findings: 22 failure modes | Scope: critical

## Invoke As

`uv run cloudfall` (from the Cloudfall checkout or a project pinned to it; resolved `.venv/bin/cloudfall`)

## Always Include

| Flag / Env var | Reason | §N |
|---|---|---|
| `PYTHONUNBUFFERED=1` | stdout is block-buffered under a pipe; `operator run --interval` emits nothing until exit | §60 |
| `--project <dir>` or `CLOUDFALL_PROJECT` | relative `--output`/`--receipts` paths resolve against the project, not confined to it | §34 |
| stdin `/dev/null` | no command reads stdin; piped payloads can deadlock against large stdout | §61 |
| unset `ANSIBLE_*` | inherited Ansible env changes engine output embedded in JSON `detail` | §42 |

## Never Do

| Action | Risk | §N |
|---|---|---|
| Abbreviate or guess secret flags (`--api-key`, `--source-url`) | argparse prefix-matches the `*-file` flag and echoes the secret in the error | §24, §42 |
| Pass secrets on argv | only file flags exist; any argv value can end up in stderr | §24 |
| Run `restart`/`deploy`/`rollback`/`data migrate` to "check" something | they act on the first call; no `--dry-run` | §23 |
| Run `migrate --yes` before reviewing plan output | executes baseline, services, deploy, DNS cutover steps | §23 |
| Pass `--output`, `--dry-run`, `--timeout`, `--idempotency-key`, `--max-output`, `--version` | not supported; exit 2 usage error | §2, §11, §12, §43, §71 |
| Retry after exit 1 from `health` without reading `detail` | exit 1 covers unhealthy, unreachable, and tracebacks | §1, §11 |
| Feed `inventory show` of a large fleet straight into context | unbounded single-line JSON (181 KB at 600 servers) | §43 |
| Follow `init`'s `next` hint `config validate .` verbatim | exit 2; use `config validate` with no positional | §62 |

## Watch in Output

| Pattern | Meaning | Action |
|---|---|---|
| stderr starts with `Traceback (most recent call last)` | uncaught error (invalid id, missing cert file) | fix input; do not treat as outage |
| stderr starts with `usage: cloudfall` | argparse usage error, exit 2, prose | fix flags; do not retry |
| `{"status": "error", "error": {"code": ...}}` on stderr | structured failure, exit 2 (input/not-found) or 1 (`lifecycle_execution_failed`) | branch on `error.code` |
| `{"code": "operator_...", "message": ...}` on stderr | operator error without envelope | branch on top-level `code` |
| `*_unreachable` with `401`, `certificate expired`, `unknown ca` in message | credential problem, not network | rotate/fix credential; do not retry |
| `"status": "unhealthy"` with `UNREACHABLE!` in `detail` | SSH failed, component state unknown | check connectivity before restart |
| `migrate` stdout `"status": "error"` + `"step"` | step failed, progress saved in plan file | fix cause, re-run same command to resume |
| `migrate` exit 3 / `"status": "paused"` | DNS pause at cutover | complete DNS step, re-run |
| `resource_exists` on `add` | may be your own earlier success | read the YAML before treating as conflict |
| `audit` exit 1 / 3 | drift / compliance unknown | not an execution error |

## Score Summary

| §N | Title | Severity | Score |
|---|---|---|---|
| §1 | Exit Codes & Status Signaling | Critical | 1/3 |
| §2 | Output Format & Parseability | Critical | 1/3 |
| §10 | Interactivity & TTY Requirements | Critical | 2/3 |
| §11 | Timeouts & Hanging Processes | Critical | 1/3 |
| §12 | Idempotency & Safe Retries | Critical | 1/3 |
| §13 | Partial Failure & Atomicity | Critical | 2/3 |
| §23 | Side Effects & Destructive Operations | Critical | 1/3 |
| §24 | Authentication & Secret Handling | Critical | 1/3 |
| §25 | Prompt Injection via Output | Critical | 1/3 |
| §34 | Shell Injection via Agent-Constructed Commands | Critical | 1/3 |
| §37 | REPL / Interactive Mode Accidental Triggering | Critical | 2/3 |
| §42 | Debug / Trace Mode Secret Leakage | Critical | 1/3 |
| §43 | Tool Output Result Size Unboundedness | Critical | 0/3 |
| §45 | Headless Authentication / OAuth Browser Flow Blocking | Critical | 1/3 |
| §50 | Stdin Consumption Deadlock | Critical | 2/3 |
| §53 | Credential Expiry Mid-Session | Critical | 1/3 |
| §60 | OS Output Buffer Deadlock | Critical | 1/3 |
| §61 | Bidirectional Pipe Payload Deadlock | Critical | 1/3 |
| §62 | $EDITOR and $VISUAL Trap | Critical | 2/3 |
| §64 | Headless Display and GUI Launch Blocking | Critical | 2/3 |
| §71 | Non-Interactive Installation Absence | Critical | 2/3 |
| §74 | Credential Scope Declaration Absence | Critical | 0/3 |

**Worst gaps (score 0):** §43, §74
**Partial (score 1–2):** §1, §2, §10, §11, §12, §13, §23, §24, §25, §34, §37, §42, §45, §50, §53, §60, §61, §62, §64, §71
