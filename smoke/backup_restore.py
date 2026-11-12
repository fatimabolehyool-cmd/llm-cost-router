"""Timed backup and restore drill (architecture section E, feedback A5).

1. Count the rows of every router table.
2. pg_dump the router database (custom format) to backups/ and time it.
3. Restore the dump into a scratch database, time it, count the rows again.
4. Pass if every count matches; drop the scratch database; write results/backup_restore_<ts>.md.

The stack must be running (any profile with postgres). Dumps are git-ignored: they hold
request data (masked text, routing records), so they stay on the machine.
"""

from __future__ import annotations

import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PSQL = ["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "postgres", "-Atc"]
TABLES = ("request_log", "feedback", "spend", "schema_migrations")
SCRATCH = "router_restore_check"


def counts(db: str) -> dict[str, int]:
    query = ", ".join(f"(SELECT count(*) FROM {t})" for t in TABLES)
    out = subprocess.run(
        [*PSQL[:-1], "-d", db, "-Atc", f"SELECT {query}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return dict(zip(TABLES, map(int, out.split("|")), strict=True))


def main() -> int:
    ts = datetime.now().strftime("%Y%m%d-%H%M")
    (ROOT / "backups").mkdir(exist_ok=True)
    dump = ROOT / "backups" / f"router-{ts}.dump"
    before = counts("router")

    t0 = time.perf_counter()
    with dump.open("wb") as f:
        subprocess.run(
            [
                "docker",
                "compose",
                "exec",
                "-T",
                "postgres",
                "pg_dump",
                "-U",
                "postgres",
                "-d",
                "router",
                "-Fc",
            ],
            stdout=f,
            check=True,
        )
    dump_s = time.perf_counter() - t0

    subprocess.run([*PSQL, f"DROP DATABASE IF EXISTS {SCRATCH}"], check=True, capture_output=True)
    subprocess.run([*PSQL, f"CREATE DATABASE {SCRATCH}"], check=True, capture_output=True)
    t1 = time.perf_counter()
    with dump.open("rb") as f:
        subprocess.run(
            [
                "docker",
                "compose",
                "exec",
                "-T",
                "postgres",
                "pg_restore",
                "-U",
                "postgres",
                "-d",
                SCRATCH,
                "--no-owner",
                "--exit-on-error",
            ],
            stdin=f,
            check=True,
        )
    restore_s = time.perf_counter() - t1
    after = counts(SCRATCH)
    subprocess.run([*PSQL, f"DROP DATABASE {SCRATCH}"], check=True, capture_output=True)

    ok = before == after
    lines = [
        f"# Backup and restore drill ({datetime.now(UTC).isoformat(timespec='seconds')})",
        "",
        f"**Result: {'PASS' if ok else 'FAIL'}**: pg_dump of the router database "
        f"({dump.stat().st_size / 1024:.0f} KiB, custom format) in **{dump_s:.2f} s**; restore into a "
        f"scratch database in **{restore_s:.2f} s**. Measured on this Mac.",
        "",
        "| Table | Rows before | Rows after restore |",
        "|---|---|---|",
        *[f"| {t} | {before[t]} | {after[t]} |" for t in TABLES],
        "",
        "Recovery procedure: `docker compose exec -T postgres pg_restore -U postgres -d router "
        "--clean --no-owner < backups/<file>.dump` with the router stopped. The dump file "
        f"`{dump.name}` stays in backups/ (git-ignored, contains request data).",
    ]
    out = ROOT / "results" / f"backup_restore_{ts}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
