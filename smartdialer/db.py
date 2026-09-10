"""Engine + schema bootstrap. Postgres is the only coordination layer."""
from __future__ import annotations

import os
import pathlib

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://localhost:5432/smartdialer",
)

_SCHEMA = (pathlib.Path(__file__).parent / "schema.sql").read_text()


def make_engine(url: str | None = None, pool_size: int = 20) -> AsyncEngine:
    # Real pool with >=10 connections: the concurrency test needs each task on
    # its own connection/transaction, otherwise asyncio serializes them and
    # SKIP LOCKED is never exercised. NullPool would defeat that.
    return create_async_engine(
        url or DATABASE_URL, pool_pre_ping=True, pool_size=pool_size, max_overflow=0
    )


async def init_schema(engine: AsyncEngine) -> None:
    # Strip -- line comments first (they may contain ';'), then split.
    sql = "\n".join(l for l in _SCHEMA.splitlines() if not l.lstrip().startswith("--"))
    async with engine.begin() as conn:
        # asyncpg runs one statement per exec; split the DDL on ';'.
        for stmt in filter(str.strip, sql.split(";")):
            await conn.execute(text(stmt))
