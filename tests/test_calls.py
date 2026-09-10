import pytest
from sqlalchemy import text

from smartdialer.calls import ingest_event

pytestmark = pytest.mark.asyncio


async def _make_call(engine) -> int:
    async with engine.begin() as conn:
        aid = (
            await conn.execute(
                text("INSERT INTO agents (state,campaign_id) VALUES ('RESERVED',1) RETURNING id")
            )
        ).scalar_one()
        bid = (
            await conn.execute(
                text("INSERT INTO borrowers (phone,campaign_id) VALUES ('+100',1) RETURNING id")
            )
        ).scalar_one()
        return (
            await conn.execute(
                text(
                    "INSERT INTO calls (agent_id,borrower_id,state,state_rank) "
                    "VALUES (:a,:b,'RESERVED',1) RETURNING id"
                ),
                {"a": aid, "b": bid},
            )
        ).scalar_one()


async def _state(engine, call_id):
    async with engine.connect() as conn:
        return (
            await conn.execute(
                text("SELECT state, state_rank FROM calls WHERE id=:i"), {"i": call_id}
            )
        ).first()


async def test_forward_progress(engine):
    cid = await _make_call(engine)
    assert await ingest_event(engine, "e1", cid, "INITIATED") == "applied"
    assert await ingest_event(engine, "e2", cid, "RINGING") == "applied"
    assert await ingest_event(engine, "e3", cid, "ANSWERED") == "applied"
    assert await _state(engine, cid) == ("ANSWERED", 4)


async def test_duplicate_event_same_id_dropped(engine):
    """Replayed event id -> dropped by ON CONFLICT (idempotency guarantee)."""
    cid = await _make_call(engine)
    assert await ingest_event(engine, "dup", cid, "ANSWERED") == "applied"
    assert await ingest_event(engine, "dup", cid, "ANSWERED") == "duplicate"
    assert await _state(engine, cid) == ("ANSWERED", 4)


async def test_duplicate_answered_different_id_absorbed(engine):
    """A duplicate ANSWERED under a NEW id can't be caught by the PK; the rank
    guard absorbs it (same rank, not <)."""
    cid = await _make_call(engine)
    assert await ingest_event(engine, "a1", cid, "ANSWERED") == "applied"
    assert await ingest_event(engine, "a2", cid, "ANSWERED") == "absorbed"
    assert await _state(engine, cid) == ("ANSWERED", 4)


async def test_out_of_order_lower_rank_absorbed(engine):
    """RINGING arriving after CONNECTED does not roll the state backwards."""
    cid = await _make_call(engine)
    assert await ingest_event(engine, "c", cid, "CONNECTED") == "applied"
    assert await ingest_event(engine, "r", cid, "RINGING") == "absorbed"
    assert await _state(engine, cid) == ("CONNECTED", 5)


async def test_terminal_is_absorbing(engine):
    """Late ANSWERED after COMPLETED is recorded in provider_events but the
    state stays terminal."""
    cid = await _make_call(engine)
    assert await ingest_event(engine, "done", cid, "COMPLETED") == "applied"
    assert await ingest_event(engine, "late", cid, "ANSWERED") == "absorbed"
    assert await _state(engine, cid) == ("COMPLETED", 6)
    # The discarded event is still on record.
    async with engine.connect() as conn:
        n = (
            await conn.execute(
                text("SELECT count(*) FROM provider_events WHERE call_id=:i"), {"i": cid}
            )
        ).scalar_one()
    assert n == 2
