# Security policy

Atlas manages servers over SSH and renders infrastructure configuration, so
security reports are taken seriously.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting on this repository ("Report a
vulnerability" under the Security tab). Do not open a public issue for
security-sensitive findings.

Please include the affected component (`state/`, `sdk/`, `engine/`), a
reproduction, and the impact you believe it has.

## Scope notes

- State directories must never contain secret values; schemas and validation
  are designed to reject them. A way to smuggle credentials into rendered
  inventory or observations is a vulnerability
- The inspection pipeline is read-only by design. Any code path through which
  `atlas_inspect` or the audit toolchain modifies a target host is a
  vulnerability
- Rendered configuration that weakens documented trust boundaries (proxy
  header trust, mTLS requirements, loopback-only listeners) is a vulnerability
