from __future__ import annotations

import asyncpg
from loguru import logger

_pool: asyncpg.Pool | None = None


# A query that waits on a row lock must eventually *fail*, not hang. With asyncpg's default
# `command_timeout=None` it waits forever: no exception, no log line, no `tree.on_error` — the
# member just watches the spinner turn until the interaction token expires. Every command
# defers before touching the database, so one stuck `FOR UPDATE` (ours, or the `api` service's,
# which shares this database) is indistinguishable from a dead bot. This is the bound that
# turns that silence into a logged error and an ephemeral reply.
COMMAND_TIMEOUT = 15.0

# Nothing we do holds a transaction open across a Discord round-trip, so a session sitting
# idle inside one is a bug by definition. Cap it rather than let it pin a lock indefinitely.
IDLE_IN_TRANSACTION_TIMEOUT_MS = "30000"

# Migrations are one big blob run once at startup and legitimately outlast COMMAND_TIMEOUT on
# a database with real data in it. They get their own bound so hardening the pool can't turn
# a slow boot into a crash loop.
MIGRATION_TIMEOUT = 300.0


async def create_pool(database_url: str) -> asyncpg.Pool:
    global _pool
    logger.info("Connecting to PostgreSQL...")
    _pool = await asyncpg.create_pool(
        database_url,
        min_size=2,
        max_size=10,
        command_timeout=COMMAND_TIMEOUT,
        max_inactive_connection_lifetime=300.0,
        server_settings={"idle_in_transaction_session_timeout": IDLE_IN_TRANSACTION_TIMEOUT_MS},
    )
    logger.info("PostgreSQL connection pool created.")
    return _pool


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool has not been initialised. Call create_pool() first.")
    return _pool


async def run_migrations(pool: asyncpg.Pool, sql: str) -> None:
    async with pool.acquire() as conn:
        # Overrides the pool's COMMAND_TIMEOUT — see MIGRATION_TIMEOUT.
        await conn.execute(sql, timeout=MIGRATION_TIMEOUT)
    logger.info("Migrations applied.")
