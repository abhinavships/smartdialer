"""Call Allocator & Dispatcher."""
from __future__ import annotations

import asyncio
import logging
from typing import List

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from smartdialer.calls import ingest_event
from smartdialer.providers import ProviderProtocol
from smartdialer.reservation import reserve_agent_and_create_call

log = logging.getLogger("smartdialer.allocator")


class CallAllocator:
    def __init__(self, engine: AsyncEngine, provider: ProviderProtocol) -> None:
        self.engine = engine
        self.provider = provider

    async def get_queued_borrowers(self, campaign_id: int, limit: int) -> List[int]:
        """Fetch available QUEUED borrowers for campaign."""
        if limit <= 0:
            return []
        async with self.engine.begin() as conn:
            result = await conn.execute(
                text(
                    """
                    SELECT id FROM borrowers
                    WHERE campaign_id = :campaign_id
                      AND state = 'QUEUED'
                      AND (locked_until IS NULL OR locked_until < now())
                    ORDER BY id
                    LIMIT :limit
                    """
                ),
                {"campaign_id": campaign_id, "limit": limit},
            )
            return [row[0] for row in result.fetchall()]

    async def allocate_and_dispatch(
        self, campaign_id: int, approved_dials: int, provider_name: str = "mock_provider"
    ) -> int:
        """Reserve agents/borrowers and dispatch calls. Returns count of calls dispatched."""
        borrower_ids = await self.get_queued_borrowers(campaign_id, approved_dials)
        dispatched = 0

        for b_id in borrower_ids:
            res = await reserve_agent_and_create_call(
                self.engine, campaign_id, b_id, provider_name
            )
            if res is None:
                continue  # No agent available or reservation conflict

            agent_id, call_id = res
            dispatched += 1

            # Dispatch background task to handle provider event stream
            asyncio.create_task(
                self._dispatch_call_task(call_id, f"+1555000{b_id:04d}")
            )

        return dispatched

    async def _dispatch_call_task(self, call_id: int, phone: str) -> None:
        try:
            async for event_type, event_id in self.provider.place_call(call_id, phone):
                result = await ingest_event(self.engine, event_id, call_id, event_type)
                log.info(
                    "Call %s event %s (%s) -> ingest result: %s",
                    call_id,
                    event_type,
                    event_id,
                    result,
                )
        except Exception as e:
            log.error("Call %s provider dispatch failed: %s", call_id, e)
            # Ingest FAILED event if exception occurred
            await ingest_event(
                self.engine, f"evt_err_{call_id}_{int(asyncio.get_event_loop().time())}", call_id, "FAILED"
            )
