"""Data retention (architecture section E, feedback A5).

Two rules, applied together in one transaction:
  * after TEXT_DAYS (30): the stored request text (already masked) and feedback notes are set to
    NULL; the routing record itself (outcome, versions, cost, timings) stays for auditing;
  * after ROW_DAYS (180): the whole routing record is deleted; its feedback goes with it
    (ON DELETE CASCADE).

Scheduled daily by the Airflow DAG `data_retention`; runs in the pipelines virtual environment.
--dry-run executes the same statements and rolls back, so it reports exactly what would change.

Usage: python -m pipelines.retention [--text-days 30] [--row-days 180] [--dry-run]
       (database from ROUTER_DATABASE_URL)
"""

from __future__ import annotations

import argparse
import json
import os
import sys

STATEMENTS: tuple[tuple[str, str], ...] = (
    (
        "request_text_cleared",
        "UPDATE request_log SET prompt_masked = NULL "
        "WHERE prompt_masked IS NOT NULL AND created_at < now() - make_interval(days => %(text_days)s)",
    ),
    (
        "feedback_notes_cleared",
        "UPDATE feedback SET note_masked = NULL "
        "WHERE note_masked IS NOT NULL AND created_at < now() - make_interval(days => %(text_days)s)",
    ),
    (
        "rows_deleted",
        "DELETE FROM request_log WHERE created_at < now() - make_interval(days => %(row_days)s)",
    ),
)


def validate(text_days: int, row_days: int) -> None:
    if not 1 <= text_days < row_days:
        raise ValueError("need 1 <= text_days < row_days")


def apply(dsn: str, text_days: int = 30, row_days: int = 180, dry_run: bool = False) -> dict:
    """Run the retention statements; return the number of rows each one changed."""
    import psycopg

    validate(text_days, row_days)
    params = {"text_days": text_days, "row_days": row_days}
    counts: dict[str, int] = {}
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            for name, sql in STATEMENTS:
                cur.execute(sql, params)
                counts[name] = cur.rowcount
        if dry_run:
            conn.rollback()
        else:
            conn.commit()
    return counts | {"dry_run": dry_run, "text_days": text_days, "row_days": row_days}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--text-days", type=int, default=30)
    ap.add_argument("--row-days", type=int, default=180)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    dsn = os.environ.get("ROUTER_DATABASE_URL")
    if not dsn:
        print("ROUTER_DATABASE_URL is not set", file=sys.stderr)
        return 2
    print(json.dumps(apply(dsn, a.text_days, a.row_days, a.dry_run)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
