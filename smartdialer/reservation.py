"""Agent reservation. Invariant 1: exactly one worker reserves an agent.

The single atomic UPDATE below is the whole enforcement mechanism:
  - the inner SELECT ... FOR UPDATE SKIP LOCKED grabs one AVAILABLE row and
    row-locks it, skipping rows other transactions already hold;
  - concurrent callers therefore each lock a *different* row (or none), so two
    workers can never both claim the same agent.
No application lock, no cache. Postgres row locks are the coordinator.
"""
from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

log = logging.getLogger("smartdialer.reservation")

LEASE_SECONDS = 30

_RESERVE = text(
    """
    UPDATE agents SET
        state = 'RESERVED',
        reserved_until = now() + make_interval(secs => :lease),
        version = version + 1
    WHERE id = (
        SELECT id FROM agents
        WHERE campaign_id = :campaign_id AND state = 'AVAILABLE'
        ORDER BY id
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    )
    RETURNING id
    """
)


async def reserve_agent(engine: AsyncEngine, campaign_id: int) -> int | None:
    """Reserve one AVAILABLE agent for the campaign. Returns its id or None."""
    async with engine.begin() as conn:
        row = (
            await conn.execute(
                _RESERVE, {"campaign_id": campaign_id, "lease": LEASE_SECONDS}
            )
        ).first()
        return row[0] if row else None


_CREATE_CALL = text(
    """
    INSERT INTO calls (agent_id, borrower_id, provider, state, state_rank)
    VALUES (:agent_id, :borrower_id, :provider, 'RESERVED', 1)
    RETURNING id
    """
)


async def reserve_agent_and_create_call(
    engine: AsyncEngine, campaign_id: int, borrower_id: int, provider: str
) -> tuple[int, int] | None:
    """Reserve an agent AND create its RESERVED call in ONE transaction.

    Atomic on purpose: the agent's RESERVED state and its call row commit
    together or not at all. If the one_active_call_per_agent guard fires (the
    agent somehow already has a non-terminal call), the whole transaction rolls
    back -> agent stays AVAILABLE, no orphan call row -> return None.

    Returns (agent_id, call_id) on success, None if no agent free or the
    invariant guard rejected the insert.
    """
    try:
        async with engine.begin() as conn:
            aid = (
                await conn.execute(
                    _RESERVE, {"campaign_id": campaign_id, "lease": LEASE_SECONDS}
                )
            ).scalar()
            if aid is None:
                return None  # no AVAILABLE agent
            call_id = (
                await conn.execute(
                    _CREATE_CALL,
                    {"agent_id": aid, "borrower_id": borrower_id, "provider": provider},
                )
            ).scalar_one()
            return aid, call_id
    except IntegrityError as e:
        if "one_active_call_per_agent" in str(e.orig):
            # Invariant guard hit, not a bug. Transaction already rolled back.
            log.warning(
                "one_active_call_per_agent guard rejected reservation "
                "(campaign=%s borrower=%s): agent already has an active call",
                campaign_id,
                borrower_id,
            )
            return None
        raise
