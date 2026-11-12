"""router.store against a real PostgreSQL: migrations, spend cap, async log, audit, feedback."""

import asyncio

import asyncpg

from router.store import MIGRATIONS, Store, migrate


def row(rid, **over):
    base = {
        "request_id": rid,
        "task_type": "label",
        "mode": "balanced",
        "data_class": "non_sensitive",
        "prompt_hash": "h" * 64,
        "prompt_masked": "card did not arrive",
        "n_options": 77,
        "j1_answer": "card_arrival",
        "j2_answer": "card_arrival",
        "agreed": True,
        "escalated": False,
        "status": "answered",
        "answer": "card_arrival",
        "verified": True,
        "final_model": "gemma3:12b",
        "reason": "juniors_agreed",
        "policy_version": "run-test",
        "prompt_version": "junior-prompts-v2",
        "junior1_model": "gemma3:12b",
        "junior2_model": "phi4:14b",
        "senior_model": "claude-opus-5-5",
        "cost_usd": 0.0,
        "latency_ms": 900,
        "policy_p": 0.8,
    }
    return base | over


async def started(dsn, cap=8.0) -> Store:
    s = Store(dsn, cap)
    await s.start()
    return s


async def fetch(dsn, sql, *args):
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


def test_migrations_run_once_and_are_recorded(dsn):
    async def go():
        s = await started(dsn)
        await s.stop()
        conn = await asyncpg.connect(dsn)
        try:
            again = await migrate(conn)
            versions = [
                r["version"]
                for r in await conn.fetch("SELECT version FROM schema_migrations ORDER BY version")
            ]
        finally:
            await conn.close()
        return again, versions

    again, versions = asyncio.run(go())
    assert again == []  # idempotent: nothing left to apply
    assert versions == sorted(v for v, _ in MIGRATIONS)


def test_changed_spend_cap_applies_on_restart(dsn):
    """Regression test for the bug found on 11 Nov: the cap used to stay at its first value."""

    async def go():
        s = await started(dsn, cap=30)
        await s.stop()
        s = await started(dsn, cap=8)
        await s.stop()
        return await fetch(dsn, "SELECT cap_usd FROM spend WHERE id = 1")

    assert float(asyncio.run(go())[0]["cap_usd"]) == 8.0


def test_concurrent_reservations_never_exceed_the_cap(dsn):
    async def go():
        s = await started(dsn, cap=1.0)
        results = await asyncio.gather(*(s.reserve_spend(0.1) for _ in range(50)))
        state = await fetch(dsn, "SELECT spent_usd, reserved_usd FROM spend WHERE id = 1")
        await s.stop()
        return results, state[0]

    results, state = asyncio.run(go())
    assert sum(results) == 10  # exactly cap / cost reservations succeed, under concurrency
    assert float(state["reserved_usd"]) == 1.0 and float(state["spent_usd"]) == 0.0


def test_settle_moves_the_reservation_to_actual_spend(dsn):
    async def go():
        s = await started(dsn, cap=1.0)
        assert await s.reserve_spend(0.05)
        await s.settle_spend(0.05, 0.0027)
        state = await fetch(dsn, "SELECT spent_usd, reserved_usd FROM spend WHERE id = 1")
        await s.stop()
        return state[0]

    state = asyncio.run(go())
    assert float(state["reserved_usd"]) == 0.0 and abs(float(state["spent_usd"]) - 0.0027) < 1e-9


def test_log_row_then_audit_result_are_written_in_order(dsn):
    async def go():
        s = await started(dsn)
        s.enqueue_log(row("req_000000000001"))
        s.enqueue_audit(
            {
                "request_id": "req_000000000001",
                "audit_answer": "lost_or_stolen_card",
                "audit_agrees": False,
                "audit_cost_usd": 0.0027,
            }
        )
        await s.queue.join()
        rows = await fetch(dsn, "SELECT audit_answer, audit_agrees, audited_at FROM request_log")
        queue = await fetch(dsn, "SELECT request_id FROM audit_review_queue")
        await s.stop()
        return rows, queue, s.dropped

    rows, queue, dropped = asyncio.run(go())
    assert rows[0]["audit_agrees"] is False and rows[0]["audit_answer"] == "lost_or_stolen_card"
    assert rows[0]["audited_at"] is not None and dropped == 0
    assert [r["request_id"] for r in queue] == ["req_000000000001"]  # waits for human review


def test_a_bad_row_is_dropped_and_counted_without_stopping_the_writer(dsn):
    async def go():
        s = await started(dsn)
        s.enqueue_log(row("req_000000000002", task_type=None))  # violates NOT NULL
        s.enqueue_log(row("req_000000000003"))
        await s.queue.join()
        ids = [r["request_id"] for r in await fetch(dsn, "SELECT request_id FROM request_log")]
        await s.stop()
        return ids, s.dropped

    ids, dropped = asyncio.run(go())
    assert ids == ["req_000000000003"] and dropped == 1


def test_feedback_upserts_per_caller_and_rejects_unknown_requests(dsn):
    async def go():
        s = await started(dsn)
        s.enqueue_log(row("req_000000000004"))
        await s.queue.join()
        first = await s.upsert_feedback("req_000000000004", "key000000001", "wrong", "note")
        second = await s.upsert_feedback("req_000000000004", "key000000001", "correct", None)
        unknown = await s.upsert_feedback("req_ffffffffffff", "key000000001", "wrong", None)
        rows = await fetch(dsn, "SELECT flag, note_masked FROM feedback")
        await s.stop()
        return first, second, unknown, rows

    first, second, unknown, rows = asyncio.run(go())
    assert (first, second, unknown) == (True, True, False)
    assert [(r["flag"], r["note_masked"]) for r in rows] == [("correct", None)]
