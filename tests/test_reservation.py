import asyncio

import pytest
from sqlalchemy import event, text

from smartdialer.reservation import reserve_agent, reserve_agent_and_create_call

pytestmark = pytest.mark.asyncio


async def test_concurrent_double_reservation(one_agent):
    """50 tasks race for 1 agent: exactly one wins, the rest get None.

    Also proves the tasks actually ran in parallel across separate pooled
    connections/transactions — otherwise asyncio serializes them and the pass
    is meaningless (SKIP LOCKED never fires).
    """
    engine, agent_id = one_agent

    # Pool must be big enough that tasks don't queue behind one connection.
    pool_size = engine.pool.size()
    print(f"\npool size = {pool_size}")
    assert pool_size >= 10, "pool too small to exercise real concurrency"

    # Track peak simultaneously-checked-out connections during the race.
    live = {"cur": 0, "peak": 0}

    @event.listens_for(engine.sync_engine, "checkout")
    def _out(*_):
        live["cur"] += 1
        live["peak"] = max(live["peak"], live["cur"])

    @event.listens_for(engine.sync_engine, "checkin")
    def _in(*_):
        live["cur"] -= 1

    results = await asyncio.gather(*(reserve_agent(engine, 1) for _ in range(50)))

    print(f"peak concurrent connections = {live['peak']}")
    assert live["peak"] >= 10, "tasks were serialized, not truly concurrent"

    winners = [r for r in results if r is not None]
    assert winners == [agent_id], f"expected exactly one winner, got {winners}"
    assert results.count(None) == 49


async def test_reserve_none_when_no_available(engine):
    """No AVAILABLE agent -> None, never an exception."""
    assert await reserve_agent(engine, 999) is None


async def test_active_call_guard_rolls_back_reservation(engine):
    """Agent already has a non-terminal call. Reserving + dialing it again must
    hit one_active_call_per_agent, roll back the whole txn (no 500), leave the
    agent AVAILABLE, and create no orphan call row."""
    async with engine.begin() as conn:
        aid = (
            await conn.execute(
                text("INSERT INTO agents (state,campaign_id) VALUES ('AVAILABLE',1) RETURNING id")
            )
        ).scalar_one()
        bid = (
            await conn.execute(
                text("INSERT INTO borrowers (phone,campaign_id) VALUES ('+1',1) RETURNING id")
            )
        ).scalar_one()
        # Pre-existing active call for this agent.
        await conn.execute(
            text(
                "INSERT INTO calls (agent_id,borrower_id,state,state_rank) "
                "VALUES (:a,:b,'RINGING',3)"
            ),
            {"a": aid, "b": bid},
        )

    result = await reserve_agent_and_create_call(engine, 1, bid, "FastProvider")
    assert result is None  # guard hit, not an exception

    async with engine.connect() as conn:
        state = (
            await conn.execute(text("SELECT state FROM agents WHERE id=:i"), {"i": aid})
        ).scalar_one()
        n_calls = (
            await conn.execute(text("SELECT count(*) FROM calls WHERE agent_id=:i"), {"i": aid})
        ).scalar_one()
    assert state == "AVAILABLE"  # reservation rolled back
    assert n_calls == 1  # only the pre-existing call; no orphan


async def test_reserve_and_create_call_happy_path(engine):
    async with engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO agents (state,campaign_id) VALUES ('AVAILABLE',1)")
        )
        bid = (
            await conn.execute(
                text("INSERT INTO borrowers (phone,campaign_id) VALUES ('+2',1) RETURNING id")
            )
        ).scalar_one()
    res = await reserve_agent_and_create_call(engine, 1, bid, "FastProvider")
    assert res is not None
    aid, call_id = res
    async with engine.connect() as conn:
        arow = (await conn.execute(text("SELECT state FROM agents WHERE id=:i"), {"i": aid})).scalar_one()
        crow = (await conn.execute(text("SELECT state, agent_id FROM calls WHERE id=:i"), {"i": call_id})).first()
    assert arow == "RESERVED"
    assert crow == ("RESERVED", aid)
