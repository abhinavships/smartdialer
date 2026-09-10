"""Call state machine. Monotonic by rank (invariant 4).

Two independent mechanisms keep event ingestion correct under a flaky provider:

  1. provider_events PK + ON CONFLICT DO NOTHING -> a replayed event id is
     dropped. That is the idempotency guarantee.
  2. UPDATE ... WHERE state_rank < :rank -> a lower-ranked event never
     overwrites a higher-ranked state. This one clause absorbs BOTH duplicates
     (same rank, not <) and out-of-order/late events (lower rank, not <),
     including a duplicate ANSWERED that arrives under a *different* event id.

Terminal states (rank 6) are absorbing: a late ANSWERED after COMPLETED is
still recorded in provider_events, then the UPDATE no-ops.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

RANK = {
    "QUEUED": 0,
    "RESERVED": 1,
    "INITIATED": 2,
    "RINGING": 3,
    "ANSWERED": 4,
    "CONNECTED": 5,
    "COMPLETED": 6,
    "FAILED": 6,
    "CANCELLED": 6,
}

_INSERT_EVENT = text(
    """
    INSERT INTO provider_events (provider_event_id, call_id, event_type)
    VALUES (:eid, :cid, :etype)
    ON CONFLICT (provider_event_id) DO NOTHING
    """
)

_APPLY = text(
    """
    UPDATE calls
    SET state = :state, state_rank = :rank, updated_at = now()
    WHERE id = :cid AND state_rank < :rank
    """
)


async def ingest_event(
    engine: AsyncEngine, provider_event_id: str, call_id: int, event_type: str
) -> str:
    """Ingest one provider event. Returns: 'duplicate', 'applied', or 'absorbed'.

    'absorbed' = recorded but did not change state (out-of-order or terminal).
    """
    if event_type not in RANK:
        raise ValueError(f"unknown event_type {event_type!r}")
    rank = RANK[event_type]
    async with engine.begin() as conn:
        ins = await conn.execute(
            _INSERT_EVENT, {"eid": provider_event_id, "cid": call_id, "etype": event_type}
        )
        if ins.rowcount == 0:
            return "duplicate"  # replayed event id -> dropped
        upd = await conn.execute(
            _APPLY, {"state": event_type, "rank": rank, "cid": call_id}
        )
        return "applied" if upd.rowcount == 1 else "absorbed"
