# Backup and restore drill (2026-11-12T14:34:15+00:00)

**Result: PASS**: pg_dump of the router database (34 KiB, custom format) in **0.12 s**; restore into a scratch database in **0.12 s**. Measured on this Mac.

| Table | Rows before | Rows after restore |
|---|---|---|
| request_log | 465 | 465 |
| feedback | 0 | 0 |
| spend | 1 | 1 |
| schema_migrations | 4 | 4 |

Recovery procedure: `docker compose exec -T postgres pg_restore -U postgres -d router --clean --no-owner < backups/<file>.dump` with the router stopped. The dump file `router-20261112-1834.dump` stays in backups/ (git-ignored, contains request data).
