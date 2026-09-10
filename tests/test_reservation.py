import asyncio

import pytest
from sqlalchemy import event

from smartdialer.reservation import reserve_agent

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
