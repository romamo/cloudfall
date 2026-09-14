# X Premium Post — cloudfall

<!-- Copy everything between the lines below into X as one Premium long-form post -->

---
I audited cloudfall against 22 Critical CLI Agent Spec failure modes.

Result: 1.2/3 average across scored Critical checks. Readiness: 8/15 [C].

The surprising part: install is not the problem.

The weak points are runtime semantics for agents:

1. no output bound: inventory show returned 181 KB in one JSON line for 600 servers
2. no credential scope declaration; operator and collectors share one mTLS CA
3. --source-url is prefix-matched to --source-url-file and the error echoes the URL with its password
4. no schema or --version; exit 1 means unhealthy, unreachable, or a Python traceback
5. restart, deploy, rollback, data migrate act on the first CLI call, while the MCP server demands a confirm step

Practical guidance for agent builders:
- set PYTHONUNBUFFERED=1; operator run --interval sends 0 bytes through a pipe otherwise
- always spell secret flags in full and pass secrets only as files
- read detail for UNREACHABLE before treating "unhealthy" as an app failure
- validate JSON strictly before parsing: errors come in three shapes
- prefer cloudfall-mcp for mutations to keep the confirm handshake

Fastest CLI-author fixes:
- structured non-interactive error envelopes
- invariant JSON for success and failure paths
- machine-readable schema/manifest for commands, flags, exit codes, scopes, safe defaults, and interactivity
- dry-run/effect/idempotency contracts for setup and config commands

Full report: [PASTE LINK HERE]
---

<!-- FORMAT RULES (apply before writing content):
     - Write a single X Premium long-form post, not a numbered thread.
     - Keep the first 280 characters self-contained: tool name, audit result, and why it matters.
     - Target 900-1800 characters unless the findings need more detail; do not exceed X Premium's long-post limit.
     - No emojis unless they appear in source findings; this should read like an engineering field note.
     - Use plain text bullets and numbered lists; no markdown tables.
     - Put the link near the end, not in the opening line.
     - Never guess maintainer @handles. If an @handle is provided by the user, place it on its own line before the link.
     - Ground every claim in findings/readiness/issues; do not invent counts, scores, or bugs.
     - First person singular is allowed ("I audited") when the report was produced by one evaluator. -->
