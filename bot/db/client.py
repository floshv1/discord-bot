from __future__ import annotations

import asyncpg
from loguru import logger

_pool: asyncpg.Pool | None = None


async def create_pool(database_url: str) -> asyncpg.Pool:
    global _pool
    logger.info("Connecting to PostgreSQL...")
    _pool = await asyncpg.create_pool(database_url)
    logger.info("PostgreSQL connection pool created.")
    return _pool


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool has not been initialised. Call create_pool() first.")
    return _pool


async def run_migrations(pool: asyncpg.Pool, sql: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(sql)
    logger.info("Migrations applied.")
