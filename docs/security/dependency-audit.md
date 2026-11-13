# Dependency audit

CI runs `pip-audit` on every lock file (job `security` in `.github/workflows/ci.yml`). This file
records what the audit found when it was introduced and every finding that is accepted rather
than fixed.

## Findings when the audit was introduced (10 Nov 2026)

| Lock file | Package | Pinned | Fixed in | Action |
|---|---|---|---|---|
| `infra/requirements-router.lock` | none | | | |
| `infra/requirements-mlops.lock` | pyasn1 (4 CVEs) | 0.6.4 | 0.6.5 | Upgraded |
| `exp2_policy_serving/requirements-serving.lock` | pyasn1 (4 CVEs) | 0.6.4 | 0.6.5 | Upgraded |
| `exp2_policy_serving/requirements-serving.lock` | mlflow | 3.17.1 | 3.17.2 | Upgraded (now matches the MLflow server) |
| `exp2_policy_serving/requirements-serving.lock` | brotli | 1.2.0 | 1.2.1 | Upgraded |
| `exp2_policy_serving/requirements-serving.lock` | starlette (6 advisories) | 0.46.2 | 0.47.2 – 1.3.1 | **Accepted**, see below |

The upgraded images must be rebuilt and re-checked (S1–S3, E2) before release.

## Later findings

| Date | Lock file | Package | Pinned | Fixed in | Action |
|---|---|---|---|---|---|
| 12 Nov 2026 | `requirements-dev.txt` (test runner, not deployed) | pytest (PYSEC-2026-1845) | 8.4.2 | 9.0.3 | Upgraded; dev tools added to the CI audit, which previously checked only deployed lock files |
| 13 Nov 2026 | router, pipelines and policy-server locks | opentelemetry-api (CVE-2026-126360, GHSA-mf5v-w3w3-f6cg: ReDoS in `traceparent` extraction) | 1.45.1 | 1.45.2 | Upgraded with the matching OpenTelemetry packages (1.45.2 / 0.66b2); inter-package pins checked; images rebuilt and the stack re-run |

## Accepted: starlette 0.46.2 in the policy server image

**Advisories ignored in CI:** PYSEC-2026-161, PYSEC-2026-248, PYSEC-2026-249, PYSEC-2026-1941,
PYSEC-2026-1942, PYSEC-2026-2280, PYSEC-2026-2281. They are ignored for
`requirements-serving.lock` only; the same advisories fail the build in any other lock file.

**Why it cannot be upgraded.** MLServer 1.7.1, the latest release, requires
`fastapi>=0.88.0,!=0.89.0,<0.116.0`, and FastAPI 0.115.x requires `starlette<0.47.0`. Every fix
needs starlette 0.47.2 or later. Forcing a newer starlette breaks MLServer's declared constraints.

**Why the risk is acceptable for this deployment.**
- The policy server has no caller other than the router's PolicyClient (interface I5). In the
  main Compose stack it is reachable only on the internal Docker network.
- The only input it accepts is a fixed-shape vector of 9 floats, built and validated by the
  router; no user text reaches it.
- If it misbehaves or is stopped, the router falls back to rule-v0 in-process (ADR-2), so
  removing it entirely is a one-line configuration change.

**Review trigger.** Re-run the audit and remove this exception when MLServer publishes a release
that allows FastAPI ≥ 0.116, or before any deployment beyond the single development Mac.
