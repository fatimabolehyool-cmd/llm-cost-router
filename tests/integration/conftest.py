"""PostgreSQL integration tests: run when TEST_DATABASE_URL points at a disposable database.

CI starts a PostgreSQL 16 service container for them; locally `make test-integration` starts
a throwaway container on 127.0.0.1:55432. Without TEST_DATABASE_URL they are skipped, so the
unit-test job never needs a database. Every test starts from an empty schema.
"""

import asyncio
import os

import pytest

DSN = os.environ.get("TEST_DATABASE_URL")


def pytest_collection_modifyitems(config, items):
    for item in items:
        if "tests/integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)
            if not DSN:
                item.add_marker(pytest.mark.skip(reason="TEST_DATABASE_URL not set"))


async def _reset() -> None:
    import asyncpg

    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute(
            "DROP VIEW IF EXISTS audit_review_queue; "
            "DROP TABLE IF EXISTS feedback, request_log, spend, schema_migrations CASCADE;"
        )
    finally:
        await conn.close()


@pytest.fixture
def dsn():
    asyncio.run(_reset())
    return DSN
