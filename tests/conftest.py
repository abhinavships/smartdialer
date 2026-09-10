import os

import pytest
import pytest_asyncio
from sqlalchemy import text

from smartdialer.db import init_schema, make_engine

# Local run: uses ambient Postgres. CI/docker sets DATABASE_URL.
TEST_URL = os.environ.get(
    "TEST_DATABASE_URL",
    os.environ.get("DATABASE_URL", "postgresql+asyncpg://localhost:5432/postgres"),
)


@pytest_asyncio.fixture
async def engine():
    eng = make_engine(TEST_URL)
    await init_schema(eng)
    # Clean slate between tests (order matters for FKs).
    async with eng.begin() as conn:
        for tbl in ("provider_events", "calls", "pacing_decisions", "agents", "borrowers"):
            await conn.execute(text(f"TRUNCATE {tbl} RESTART IDENTITY CASCADE"))
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def one_agent(engine):
    async with engine.begin() as conn:
        agent_id = (
            await conn.execute(
                text(
                    "INSERT INTO agents (state, campaign_id) "
                    "VALUES ('AVAILABLE', 1) RETURNING id"
                )
            )
        ).scalar_one()
    return engine, agent_id
