# S3 canary test (2026-11-19T18:49:18+00:00)

**Result: PASS** — 252 sensitive requests (7 junior behaviours x 3 senior behaviours x 3 modes x 2 task types x 2 ways of being sensitive), run time 294 s. Measured.

| Check | Observed | Required |
|---|---|---|
| Sensitive tokens found at the external (senior) provider | 0 | 0 |
| Sensitive tokens found in request_log (any column) | 0 | 0 |
| Sensitive tokens found in router logs | 0 | 0 |
| Sensitive requests misclassified or escalated | 0 | 0 |
| Positive controls (before and after the matrix) reached the senior | yes | yes |
| Audit positive control (accepted, non-sensitive) reached the senior via the audit path | yes | yes |
| Sensitive requests that reached the local juniors (sanity) | 252/252 | > 0 |

## Outcomes of the sensitive requests

| Status | Reason | Count |
|---|---|---|
| 503 | models_unavailable | 36 |
| abstained | sensitive_disagreement | 180 |
| answered | juniors_agreed | 36 |

Limitations: mock models, not real ones; the PII detector covers emails, UAE phone numbers, Emirates IDs and card numbers (Luhn) only, so other personal data in an unflagged request is a stated residual risk.
