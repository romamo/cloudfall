# cloudfall — Evaluation Index

**Generated:** 2026-09-14
**CLI version:** 0.1.0
**Scope:** critical
**Failure modes evaluated:** 22 of 74 _(scope: critical)_

## Score Summary

| Severity | Pass (3/3) | Partial (1–2) | Fail (0) | Total |
|---|---|---|---|---|
| Critical | 0 | 20 | 2 | 22 |
| High | 0 | 0 | 0 | 0 |
| Medium | 0 | 0 | 0 | 0 |
| **All** | **0** | **20** | **2** | **22** |

**Average score:** 1.23 / 3

## Readiness Score

| Dimension | Score |
|---|---|
| Documentation Quality | 1/3 |
| Self-Description | 1/3 |
| Pre-built Integrations | 2/3 |
| Setup Reproducibility | 1/3 |
| Workflow Coverage | 3/3 |
| **Total** | **8/15 [C]** |

## Reports

| Report | Audience | File |
|---|---|---|
| Issues & Problems | AI agents and their builders | [report-issues.md](report-issues.md) |
| Runtime Brief | AI agents at invocation time | [report-runtime.md](report-runtime.md) |
| Integration Guide | Agent developers | [report-agent-dev.md](report-agent-dev.md) |
| Fix List | CLI authors | [report-dev.md](report-dev.md) |

## Top Issues

- `--api-key`/`--source-url` are prefix-matched to the `*-file` flags and the secret is echoed in the error (§24)
- `operator run --interval` output is block-buffered under a pipe and lost on SIGTERM (§60)
- Invalid ids crash `health`, `rollback`, `import render` with tracebacks on exit 1, the same code as "unhealthy" (§1)
- No output size bound: `inventory show` returns 181 KB in one line for 600 servers (§43)
- No credential scope declaration; operator and collectors share one mTLS trust domain (§74)

## Observed Bugs

19 bugs recorded during evaluation — see [report-issues.md](report-issues.md) for details.
