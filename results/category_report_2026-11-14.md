# FR-17 per-category quality report

Generated 2026-11-14 by `pipelines/category_report.py` from `data/replay/pool_v1.jsonl` (junior timeout applied). Categories chosen on the release-evaluation split (1057 questions); effect measured on the test split (689 questions) with Opus's recorded answers (`results/eval_senior_opus.jsonl`).

A help-desk category is the request type both local models agreed on. 72 categories had agreed answers; 15 had at least 8 and could be judged; **5 fail too often** (observed false agreement above the NFR-10 bar of 25%).

## Always-escalate candidates (release split)

| Category | Agreed | Wrong | False agreement | Upper 95% |
|---|---|---|---|---|
| transfer_not_received_by_recipient | 8 | 5 | 62% | 86% |
| exchange_rate | 13 | 7 | 54% | 77% |
| transfer_timing | 17 | 9 | 53% | 74% |
| verify_my_identity | 8 | 4 | 50% | 78% |
| transfer_fee_charged | 9 | 3 | 33% | 65% |

## Effect on the test split

| Strategy | Accuracy (95% CI) | Escalated | Paid cost / 1,000 |
|---|---|---|---|
| Rule-v0 (agreement) | 86.4% (83.6%–88.7%) | 27% | $0.90 |
| Rule-v0 + always-escalate list | 87.5% (84.8%–89.8%) | 33% | $1.14 |

## Exam subjects (information only, not visible to the router)

| Subject | Agreed | Wrong | False agreement |
|---|---|---|---|
| college_chemistry | 4 | 3 | 75% |
| high_school_mathematics | 9 | 6 | 67% |
| formal_logic | 2 | 1 | 50% |
| high_school_computer_science | 2 | 1 | 50% |
| high_school_european_history | 2 | 1 | 50% |
| college_physics | 3 | 1 | 33% |
| international_law | 3 | 1 | 33% |
| public_relations | 3 | 1 | 33% |
