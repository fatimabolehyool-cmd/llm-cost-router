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
]


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
            await c.execute(SCHEMA)
            await c.execute(
                "INSERT INTO spend (id, cap_usd) VALUES (1, $1) ON CONFLICT (id) DO NOTHING",
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
    async def reserve_spend(self, usd: float) -> bool:
        async with self.pool.acquire(timeout=1) as c:
            row = await c.fetchrow(
                "UPDATE spend SET reserved_usd = reserved_usd + $1 "
                "WHERE id = 1 AND spent_usd + reserved_usd + $1 <= cap_usd RETURNING id",
                usd,
            )
        return row is not None

    async def settle_spend(self, reserved: float, actual: float) -> None:
        async with self.pool.acquire(timeout=1) as c:
            await c.execute(
                "UPDATE spend SET reserved_usd = GREATEST(reserved_usd - $1, 0), "
                "spent_usd = spent_usd + $2 WHERE id = 1",
                reserved,
                actual,
            )

    # ---- asynchronous request log ----
    def enqueue_log(self, row: dict[str, Any]) -> None:
        try:
            self.queue.put_nowait(row)
        except asyncio.QueueFull:
            self.dropped += 1

    async def _write_loop(self) -> None:
        cols = ", ".join(LOG_COLUMNS)
        params = ", ".join(f"${i}" for i in range(1, len(LOG_COLUMNS) + 1))
        sql = f"INSERT INTO request_log ({cols}) VALUES ({params}) ON CONFLICT (request_id) DO NOTHING"
        while True:
            row = await self.queue.get()
            try:
                async with self.pool.acquire(timeout=2) as c:
                    await c.execute(sql, *[row.get(k) for k in LOG_COLUMNS])
            except Exception as e:  # noqa: BLE001 - never crash the writer; count and move on
                self.dropped += 1
                log.warning("request_log write dropped: %s", type(e).__name__)
            finally:
                self.queue.task_done()
