"""pipelines.retention against a real PostgreSQL (the two retention rules, feedback cascade)."""

import asyncio

import asyncpg
import pytest

from tests.integration.test_store_pg import fetch, row, started

psycopg = pytest.importorskip("psycopg")
from pipelines.retention import apply  # noqa: E402


def test_retention_clears_old_text_and_deletes_old_rows(dsn):
    async def setup():
        s = await started(dsn)
        for rid in ("req_00000000000a", "req_00000000000b", "req_00000000000c"):
            s.enqueue_log(row(rid))
        await s.queue.join()
        await s.upsert_feedback("req_00000000000c", "key000000001", "wrong", "masked note")
        await s.stop()
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(
                "UPDATE request_log SET created_at = now() - interval '45 days' WHERE request_id = 'req_00000000000b'"
            )
            await conn.execute(
                "UPDATE request_log SET created_at = now() - interval '200 days' WHERE request_id = 'req_00000000000c'"
            )
        finally:
            await conn.close()

    asyncio.run(setup())
    preview = apply(dsn, 30, 180, dry_run=True)
    after_preview = asyncio.run(fetch(dsn, "SELECT count(*) AS n FROM request_log"))[0]["n"]
    counts = apply(dsn, 30, 180)
    rows = {
        r["request_id"]: r["prompt_masked"]
        for r in asyncio.run(fetch(dsn, "SELECT request_id, prompt_masked FROM request_log"))
    }
    feedback = asyncio.run(fetch(dsn, "SELECT count(*) AS n FROM feedback"))[0]["n"]

    assert preview["rows_deleted"] == 1 and after_preview == 3  # dry run changed nothing
    assert counts["request_text_cleared"] == 2 and counts["rows_deleted"] == 1
    assert rows == {"req_00000000000a": "card did not arrive", "req_00000000000b": None}
    assert feedback == 0  # deleted with its routing record (ON DELETE CASCADE)
