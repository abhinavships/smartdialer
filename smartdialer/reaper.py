"""Background Reaper Coroutine for crash recovery & lease reclamation."""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

log = logging.getLogger("smartdialer.reaper")

REAPER_INTERVAL_SECONDS = 5.0


async def sweep_expired_leases(engine: AsyncEngine) -> dict[str, int]:
    """Reclaim expired agent/call leases and requeue stuck borrowers."""
    results = {"reclaimed_agents": 0, "failed_calls": 0, "requeued_borrowers": 0}

    async with engine.begin() as conn:
        # 1. Fail stuck non-terminal calls with expired reserved_until / updated_at
        fail_res = await conn.execute(
            text(
                """
                UPDATE calls
                SET state = 'FAILED', state_rank = 6, updated_at = now()
                WHERE state NOT IN ('COMPLETED', 'FAILED', 'CANCELLED')
                  AND updated_at < now() - INTERVAL '30 seconds'
                """
            )
        )
        results["failed_calls"] = fail_res.rowcount

        # 2. Reset expired agents back to AVAILABLE
        agent_res = await conn.execute(
            text(
                """
                UPDATE agents
                SET state = 'AVAILABLE', reserved_until = NULL, version = version + 1
                WHERE state IN ('RESERVED', 'DIALING')
                  AND reserved_until < now()
                """
            )
        )
        results["reclaimed_agents"] = agent_res.rowcount

        # 3. Reset locked borrowers, increment attempt_count, set state back to QUEUED
        borr_res = await conn.execute(
            text(
                """
                UPDATE borrowers
                SET state = 'QUEUED',
                    attempt_count = attempt_count + 1,
                    locked_until = NULL
                WHERE locked_until < now()
                """
            )
        )
        results["requeued_borrowers"] = borr_res.rowcount

    if any(results.values()):
        log.info("Reaper sweep executed: %s", results)

    return results


async def run_reaper_loop(engine: AsyncEngine, interval: float = REAPER_INTERVAL_SECONDS) -> None:
    """Continuous background reaper loop."""
    log.info("Starting background Reaper loop (interval=%.1fs)...", interval)
    while True:
        try:
            await sweep_expired_leases(engine)
        except Exception as e:
            log.error("Error during reaper sweep: %s", e)
        await asyncio.sleep(interval)
