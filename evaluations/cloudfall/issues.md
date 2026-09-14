# cloudfall — Issues

### §34 candidate — output paths escape the project directory
`cloudfall dashboard build --output ../../escape-test` (run with `--project tmp/eval/proj`) exit 0 and wrote `index.html` and `operations.json` to `tmp/escape-test`, two levels above the project. README says relative paths resolve against the project; nothing confines them to it. Same pattern applies to every `--output`, `--receipts`, `--inventory-file` flag.
Discovered during §34 evaluation on 2026-09-14.

### §34/§1 candidate — `import render` crashes on an invalid id
`cloudfall import render x.yaml --application acme%2Fx --server h1` raises an uncaught `ValueError: invalid resource id` traceback (exit 1). `_run_import_render` catches `RenderImportError`/`ConfigValidationError` but not the `ValueError` from `ResourceId.from_boundary`, unlike `add`, which returns `invalid_argument` JSON.
Discovered during §34 evaluation on 2026-09-14.

### §34 candidate — literal `null` accepted as a resource id
`cloudfall add server null --address 203.0.113.10` exit 0 and wrote `servers/null.yaml`. The id pattern accepts null-like literals an agent emits when a value is missing.
Discovered during §34 evaluation on 2026-09-14.

### §42 candidate — inherited `ANSIBLE_*` env changes engine output
Lifecycle steps run with `{**os.environ, ...}`, so `ANSIBLE_VERBOSITY=4` in the agent's environment turns on `-vvvv` for the engine; the raw SSH command lines (user, control path, options) are then embedded in the JSON `detail` field of `cloudfall health`. Nothing strips or pins `ANSIBLE_*` variables, so a debug env var set elsewhere silently widens what lands in agent context.
Discovered during §42 evaluation on 2026-09-14.

### §42 candidate — argparse echoes unrecognized flag values
`cloudfall health crm-backend --token sk-live-SECRET-abc123 --debug` exits 2 with `unrecognized arguments: --token sk-live-SECRET-abc123 --debug` on stderr. Harmless for real inputs (no secret flags exist) but a hallucinated secret flag is copied into the error an agent logs.
Discovered during §42 evaluation on 2026-09-14.

### §45/§1 candidate — missing gateway cert file crashes `operator run`
`cloudfall operator run --gateway-cert tmp/eval/tls/absent.crt --gateway-key tmp/eval/tls/absent.key ...` raises an uncaught `FileNotFoundError` from `ssl.SSLContext.load_cert_chain` (traceback, exit 1). `GatewayAlertFeed.fetch` builds the SSL context outside its `try`, and the CLI only catches `OperatorError`. Exit 1 also collides with the 'proposal not verified' result code.
Discovered during §45 evaluation on 2026-09-14.

### §45/§24 candidate — authentication failures reported as `unreachable`
Render API HTTP 401 → `render_api_unreachable` ('HTTP Error 401: Unauthorized'); gateway rejecting the client cert (`TLSV1_ALERT_UNKNOWN_CA`) → `operator_feed_unreachable`. `urllib.error.HTTPError` and `ssl.SSLError` are both `OSError`, caught by one `except OSError`. An agent cannot tell 'fix the credential' from 'retry later' without regex on the message.
Discovered during §45 evaluation on 2026-09-14.

### §53 candidate — no pre-flight expiry check on gateway credentials
`operator run --interval N` loads the client cert on every pass but never reads its `notAfter`; the first signal is a failed pass after expiry, reported as `operator_feed_unreachable`. For a long-running watcher, a warning in the pass JSON N days before expiry would let an agent rotate the cert before alerts go dark.
Discovered during §53 evaluation on 2026-09-14.

### §60 candidate — `operator run --interval` output is block-buffered and lost on termination
Under a pipe (systemd journal capture, agent subprocess), `cloudfall operator run --interval 2` emitted nothing for 9 seconds across 5 passes, and SIGTERM discarded the buffer: 0 bytes ever reached the reader. `_write_json` in `sdk/src/cloudfall/cli.py` writes to `sys.stdout` without `flush()`; only `dashboard serve` flushes. Fix: flush in `_write_json` (or `sys.stdout.reconfigure(line_buffering=True)` in `run()`). Workaround: `PYTHONUNBUFFERED=1`.
Discovered during §60 evaluation on 2026-09-14.
Impact on the documented deployment: the systemd unit in `docs/operator-guide.md` runs `operator run --interval 30` under journald without `PYTHONUNBUFFERED=1`. At ~155 bytes per quiet pass, the 8 KiB buffer fills only after ~50 passes (~25 minutes), so `journalctl -u` lags by that much, and a restart drops the unflushed passes.

### §73 candidate — `init` suggests a command that no longer parses
`cloudfall init <dir>` returns `next: [..., "uv run cloudfall config validate ."]`. Run verbatim inside the new project, it exits 2 with `unrecognized arguments: .` because the positional project argument was replaced by `--project`/`CLOUDFALL_PROJECT`/cwd. Agents copy `next` hints literally; the hint should be `uv run cloudfall config validate`. Check the scaffolded project README for the same drift.
Discovered during §62 evaluation on 2026-09-14.

### §71/§20 candidate — no `--version` flag
`cloudfall --version` exits 2 with an argparse usage error; `cloudfall-engine` and `cloudfall-mcp` behave the same. Agents and install scripts cannot verify which Cloudfall build (package version or pinned git commit) is active. `init` already resolves the installed revision (`resolve_installed_revision`), so `--version` could print `0.1.0 (b9677a6)`.
Discovered during §71 evaluation on 2026-09-14.

### §1 candidate — invalid ids crash lifecycle commands with tracebacks
`cloudfall rollback crm-backend --release r1` → uncaught `ValueError: invalid release id: 'r1'`; `cloudfall health 'Bad ID!'` → `ValueError: invalid resource id`. Both exit 1 with a Python traceback on stderr. `deploy`, `rollback`, `restart`, `health`, `backup`, `data migrate`, `secrets render`, `operator show|approve` call `ResourceId`/`ReleaseId.from_boundary` outside any `except ValueError`, while `add` and `init` wrap it as `invalid_argument` exit 2. Exit 1 is also what `health` returns for 'unhealthy', so an agent reads a typo as an outage.
Discovered during §10 evaluation on 2026-09-14.

### §11/§1 candidate — unreachable hosts reported as an unhealthy component
`cloudfall health crm-backend` against hosts that time out on SSH (203.0.113.10/11) and against unresolvable hostnames returns exit 1 with `"healthy": false, "status": "unhealthy"`, the same result as a component failing its HTTP check. The Ansible `UNREACHABLE!` signal is only inside the `detail` log tail. `operator` autonomy and agents that restart on 'unhealthy' would act on a network partition as if the app were down.
Discovered during §11 evaluation on 2026-09-14.

### §12 candidate — identical `add` retry is an error, not a noop
`cloudfall add server h8 --address 203.0.113.8` run twice: first exit 0 `added`, second exit 2 `resource_exists`, even though the requested state already holds. An agent retrying after a timeout cannot tell 'already done' from 'conflicting resource with the same id' without reading the YAML. Comparing the existing document with the requested one and returning `status: ok` with `unchanged` would make the retry safe.
Discovered during §12 evaluation on 2026-09-14.

### §13/§3 candidate — failed migrate step keeps `status: pending` and lands on stdout
After `cloudfall migrate --yes` fails at `baseline`, the step list still shows `{"id": "baseline", "status": "pending"}`; the failure is only in top-level `step`/`error`. The failure payload is written to stdout with exit 1, while `MigrateError` (e.g. `migrate_component_unbuilt`) goes to stderr with exit 2, so an agent must read both streams to find the error object.
Discovered during §13 evaluation on 2026-09-14.

### §23 candidate — CLI lacks the confirmation gate the MCP server enforces
`cloudfall-mcp` requires a preview call then `confirm=true` for every server-changing tool, but the same operations through the CLI (`cloudfall restart crm-backend`, `deploy`, `rollback`, `data migrate`) run on first invocation with no `--dry-run` or `--yes`. An agent given shell access instead of MCP bypasses the handshake. `migrate`'s plan/`--yes` split shows the pattern already exists in the CLI.
Discovered during §23 evaluation on 2026-09-14.

### §24 candidate — `--api-key <secret>` is silently accepted and echoed
argparse `allow_abbrev` is on (default) for every parser. `cloudfall import render-api --api-key rnd_x ...` is parsed as `--api-key-file rnd_x`, and the resulting error prints the value: `{"code": "render_api_key_missing", "message": "Render API key file does not exist: rnd_x"}`. An agent that guesses the natural flag name leaks the real key into stderr, logs, and its own context. The same prefix matching applies to `--source-url` → `--source-url-file` on `data migrate`. Fix: `ArgumentParser(allow_abbrev=False)` on every parser and omit file contents-looking values from the not-found message.
Discovered during §24 evaluation on 2026-09-14.

### §74 candidate — operator and collectors share one mTLS trust domain
`docs/operator-guide.md`: the operator's client certificate is 'signed by the same CA that validates collector certificates (`collectors.clientTls`); the operator is just another mTLS client of the gateway'. A collector cert on any application host is therefore accepted by the read-only alerts route the operator uses, and nothing in the config distinguishes an operator identity from a log shipper. Confirmed in `engine/ansible/roles/cloudfall_logging_backend/templates/nginx-loki-gateway.conf.j2`: `ssl_verify_client on` with a single `ssl_client_certificate` CA and no per-location client check, so the operator cert can also push to `/loki/api/v1/push` and `/api/v1/write`, and collector certs can read `/api/v1/alerts`. Not exploited live.
Discovered during §74 evaluation on 2026-09-14.

### §2 candidate — three different error shapes
`{"status": "error", "error": {"code", "message"}}` (validation, lifecycle, secrets, render import, authoring) vs `{"code", "message"}` with no envelope (`OperatorError.as_dict()`, e.g. `operator show ghost`) vs a failure object on stdout (`migrate --yes` step failure), plus prose for argparse errors and uncaught exceptions. An agent needs four parsers to read one failure. `_operator_exit` should wrap errors in the shared envelope.
Discovered during §2 evaluation on 2026-09-14.
