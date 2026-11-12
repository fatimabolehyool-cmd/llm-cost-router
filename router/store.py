"""PostgreSQL access: schema, atomic spend reservation, and the asynchronous request logger.

Transaction boundaries (Section E):
  * spend reservation is ONE atomic UPDATE before any paid call;
  * the request-log insert is its own write AFTER the response, off the request path;
  * a logging failure never fails a request: rows are dropped and counted instead.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import asyncpg

log = logging.getLogger("router.store")

SCHEMA = """
CREATE TABLE IF NOT EXISTS request_log (
    request_id      TEXT PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    task_type       TEXT NOT NULL,
    mode            TEXT NOT NULL,
    data_class      TEXT NOT NULL,
    prompt_hash     TEXT NOT NULL,          -- HMAC-SHA256, always stored
    prompt_masked   TEXT,                   -- NULL for sensitive requests
    n_options       INT  NOT NULL,
    j1_answer       TEXT,                   -- normalized option only, never raw model text
    j2_answer       TEXT,
    agreed          BOOLEAN,
    escalated       BOOLEAN NOT NULL,
    status          TEXT NOT NULL,          -- answered | abstained | error
    answer          TEXT,
    verified        BOOLEAN NOT NULL,
    final_model     TEXT,
    reason          TEXT NOT NULL,
    policy_version  TEXT NOT NULL,
    prompt_version  TEXT NOT NULL,
    junior1_model   TEXT NOT NULL,
    junior2_model   TEXT NOT NULL,
    senior_model    TEXT NOT NULL,
    cost_usd        NUMERIC(12, 6) NOT NULL DEFAULT 0,
    latency_ms      INT NOT NULL
);
CREATE INDEX IF NOT EXISTS request_log_created_at ON request_log (created_at);

CREATE TABLE IF NOT EXISTS spend (
    id            INT PRIMARY KEY CHECK (id = 1),
    cap_usd       NUMERIC(12, 6) NOT NULL,
    spent_usd     NUMERIC(12, 6) NOT NULL DEFAULT 0,
    reserved_usd  NUMERIC(12, 6) NOT NULL DEFAULT 0
);
"""

LOG_COLUMNS = [
    "request_id",
    "task_type",
    "mode",
    "data_class",
    "prompt_hash",
    "prompt_masked",
    "n_options",
    "j1_answer",
    "j2_answer",
    "agreed",
    "escalated",
    "status",
    "answer",
    "verified",
    "final_model",
    "reason",
    "policy_version",
    "prompt_version",
    "junior1_model",
    "junior2_model",
    "senior_model",
    "cost_usd",
    "latency_ms",
    "policy_p",
]

# Numbered, idempotent schema migrations (architecture section E). Each runs once, inside a
# transaction, and is recorded in schema_migrations; an advisory lock keeps two router
# processes from migrating at the same time. Never edit a migration that has shipped: add one.
MIGRATIONS: list[tuple[str, str]] = [
    ("001_initial_schema", SCHEMA),
    (
        "002_policy_probability",
        "ALTER TABLE request_log ADD COLUMN IF NOT EXISTS policy_p REAL;  -- P(correct), if used",
    ),
    (
        "003_audit_sampling",
        """
        ALTER TABLE request_log ADD COLUMN IF NOT EXISTS audit_answer TEXT;
        ALTER TABLE request_log ADD COLUMN IF NOT EXISTS audit_agrees BOOLEAN;
        ALTER TABLE request_log ADD COLUMN IF NOT EXISTS audit_cost_usd NUMERIC(12, 6);
        ALTER TABLE request_log ADD COLUMN IF NOT EXISTS audited_at TIMESTAMPTZ;
        -- Human review queue (ADR-26): accepted answers the senior disagreed with.
        CREATE OR REPLACE VIEW audit_review_queue AS
            SELECT request_id, created_at, task_type, answer, audit_answer, audited_at
            FROM request_log WHERE audit_agrees = false;
        """,
    ),
    (
        "004_feedback",
        """
        CREATE TABLE IF NOT EXISTS feedback (
            request_id   TEXT NOT NULL REFERENCES request_log (request_id) ON DELETE CASCADE,
            key_id       TEXT NOT NULL,          -- first 12 hex of the caller's key hash
            flag         TEXT NOT NULL CHECK (flag IN ('wrong', 'correct', 'unsure')),
            note_masked  TEXT,                   -- personal data masked before storage
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (request_id, key_id)
        );
        """,
    ),
]
AUDIT_SQL = (
    "UPDATE request_log SET audit_answer = $2, audit_agrees = $3, audit_cost_usd = $4, "
    "audited_at = now() WHERE request_id = $1"
)
MIGRATION_LOCK = 727_001  # arbitrary constant for pg_advisory_lock


async def migrate(conn: asyncpg.Connection) -> list[str]:
    """Apply pending migrations; return the versions applied now."""
    await conn.execute("SELECT pg_advisory_lock($1)", MIGRATION_LOCK)
    try:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        done = {r["version"] for r in await conn.fetch("SELECT version FROM schema_migrations")}
        applied = []
        for version, sql in MIGRATIONS:
            if version in done:
                continue
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute("INSERT INTO schema_migrations (version) VALUES ($1)", version)
            applied.append(version)
        return applied
    finally:
        await conn.execute("SELECT pg_advisory_unlock($1)", MIGRATION_LOCK)


class Store:
    def __init__(self, dsn: str, spend_cap_usd: float, queue_size: int = 1000) -> None:
        self.dsn, self.spend_cap_usd = dsn, spend_cap_usd
        self.pool: asyncpg.Pool | None = None
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=queue_size)
        self.dropped = 0
        self._writer: asyncio.Task | None = None

    async def start(self) -> None:
        self.pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=5, command_timeout=2)
        async with self.pool.acquire() as c:
            applied = await migrate(c)
            if applied:
                log.info("schema migrations applied: %s", ",".join(applied))
            # The configured cap always wins: a changed SPEND_CAP_USD must take effect on restart
            # (it used to be ignored once the row existed). Spent and reserved are untouched.
            await c.execute(
                "INSERT INTO spend (id, cap_usd) VALUES (1, $1) "
                "ON CONFLICT (id) DO UPDATE SET cap_usd = EXCLUDED.cap_usd",
                self.spend_cap_usd,
            )
        self._writer = asyncio.create_task(self._write_loop())

    async def stop(self) -> None:
        if self._writer:
            await asyncio.wait_for(self.queue.join(), timeout=5)
            self._writer.cancel()
        if self.pool:
            await self.pool.close()

    async def ping(self) -> bool:
        try:
            async with self.pool.acquire(timeout=1) as c:
                return await c.fetchval("SELECT 1", timeout=1) == 1
        except Exception:  # noqa: BLE001
            return False

    # ---- spend cap: one atomic statement, so two concurrent requests cannot both pass ----
    # Amounts are rounded to the column type, NUMERIC(12, 6), inside the statement. asyncpg sends
    # a Python float as its exact binary value (0.1 -> 0.1000000000000000055...), so without the
    # cast the check compared a slightly larger number than the one stored, and a request that
    # fitted exactly under the cap was refused. Found by the PostgreSQL integration test.
    async def reserve_spend(self, usd: float) -> bool:
        async with self.pool.acquire(timeout=1) as c:
            row = await c.fetchrow(
                "UPDATE spend SET reserved_usd = reserved_usd + $1::numeric(12, 6) "
                "WHERE id = 1 AND spent_usd + reserved_usd + $1::numeric(12, 6) <= cap_usd "
                "RETURNING id",
                usd,
            )
        return row is not None

    async def settle_spend(self, reserved: float, actual: float) -> None:
        async with self.pool.acquire(timeout=1) as c:
            await c.execute(
                "UPDATE spend SET reserved_usd = GREATEST(reserved_usd - $1::numeric(12, 6), 0), "
                "spent_usd = spent_usd + $2::numeric(12, 6) WHERE id = 1",
                reserved,
                actual,
            )

    # ---- caller feedback (interface I2) ----
    async def upsert_feedback(
        self, request_id: str, key_id: str, flag: str, note_masked: str | None
    ) -> bool:
        """Record or replace this caller's flag on a request. False if the request is unknown."""
        try:
            async with self.pool.acquire(timeout=1) as c:
                await c.execute(
                    "INSERT INTO feedback (request_id, key_id, flag, note_masked) "
                    "VALUES ($1, $2, $3, $4) ON CONFLICT (request_id, key_id) DO UPDATE SET "
                    "flag = EXCLUDED.flag, note_masked = EXCLUDED.note_masked, updated_at = now()",
                    request_id,
                    key_id,
                    flag,
                    note_masked,
                )
        except asyncpg.ForeignKeyViolationError:
            return False
        return True

    # ---- asynchronous request log ----
    def enqueue_log(self, row: dict[str, Any]) -> None:
        try:
            self.queue.put_nowait(row)
        except asyncio.QueueFull:
            self.dropped += 1

    def enqueue_audit(self, result: dict[str, Any]) -> None:
        """Audit results travel through the same FIFO queue, so they are written after the
        request's own row (which was queued when the response was built)."""
        self.enqueue_log({"_op": "audit", **result})

    async def _write_loop(self) -> None:
        cols = ", ".join(LOG_COLUMNS)
        params = ", ".join(f"${i}" for i in range(1, len(LOG_COLUMNS) + 1))
        sql = f"INSERT INTO request_log ({cols}) VALUES ({params}) ON CONFLICT (request_id) DO NOTHING"
        while True:
            row = await self.queue.get()
            try:
                async with self.pool.acquire(timeout=2) as c:
                    if row.get("_op") == "audit":
                        await c.execute(
                            AUDIT_SQL,
                            row["request_id"],
                            row["audit_answer"],
                            row["audit_agrees"],
                            row["audit_cost_usd"],
                        )
                    else:
                        await c.execute(sql, *[row.get(k) for k in LOG_COLUMNS])
            except Exception as e:  # noqa: BLE001 - never crash the writer; count and move on
                self.dropped += 1
                log.warning("request_log write dropped: %s", type(e).__name__)
            finally:
                self.queue.task_done()
