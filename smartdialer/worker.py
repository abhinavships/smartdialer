"""SmartDialer background worker daemon process."""
from __future__ import annotations

import asyncio
import logging
import json
import time

from sqlalchemy import text

from smartdialer.allocator import CallAllocator
from smartdialer.db import init_schema, make_engine
from smartdialer.pacing import PredictivePacingEngine, SystemSnapshot
from smartdialer.providers import FastProvider
from smartdialer.reaper import run_reaper_loop
from smartdialer.safety import SafetyController

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("smartdialer.worker")

CAMPAIGN_ID = 1
TICK_INTERVAL_SECONDS = 1.0


async def fetch_system_snapshot(engine, campaign_id: int) -> SystemSnapshot:
    async with engine.begin() as conn:
        agents_res = await conn.execute(
            text(
                "SELECT state, COUNT(*) FROM agents WHERE campaign_id = :cid GROUP BY state"
            ),
            {"cid": campaign_id},
        )
        agent_counts = {row[0]: row[1] for row in agents_res.fetchall()}

        calls_res = await conn.execute(
            text("SELECT state, COUNT(*) FROM calls GROUP BY state")
        )
        call_counts = {row[0]: row[1] for row in calls_res.fetchall()}

    return SystemSnapshot(
        campaign_id=campaign_id,
        available_agents=agent_counts.get("AVAILABLE", 0),
        wrap_up_agents=agent_counts.get("WRAP_UP", 0),
        ringing_calls=call_counts.get("RINGING", 0) + call_counts.get("INITIATED", 0),
        connected_calls=call_counts.get("CONNECTED", 0),
        historical_answer_rate=0.65,
        historical_avg_talk_time=120.0,
        historical_avg_setup_time=2.0,
        timestamp=time.time(),
    )


async def persist_pacing_decision(
    engine, campaign_id: int, requested: int, approved: int, reason: str, snapshot_json: dict
) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO pacing_decisions (campaign_id, requested, approved, reason, snapshot_json)
                VALUES (:cid, :req, :app, :reason, :json)
                """
            ),
            {
                "cid": campaign_id,
                "req": requested,
                "app": approved,
                "reason": reason,
                "json": json.dumps(snapshot_json),
            },
        )


async def run_pacing_loop(engine) -> None:
    pacing_engine = PredictivePacingEngine(alpha=0.2)
    safety_controller = SafetyController()
    provider = FastProvider()
    allocator = CallAllocator(engine, provider)

    log.info("Starting Pacing Loop for campaign %s...", CAMPAIGN_ID)

    while True:
        try:
            snapshot = await fetch_system_snapshot(engine, CAMPAIGN_ID)
            pacing_req = pacing_engine.calculate_dials(snapshot)
            approved_dials, safety_reason = safety_controller.evaluate(pacing_req)

            await persist_pacing_decision(
                engine,
                CAMPAIGN_ID,
                pacing_req.requested_dials,
                approved_dials,
                safety_reason,
                pacing_req.to_json(),
            )

            if approved_dials > 0:
                dispatched = await allocator.allocate_and_dispatch(
                    CAMPAIGN_ID, approved_dials, "FastProvider"
                )
                log.info("Pacing tick: approved=%d, dispatched=%d calls", approved_dials, dispatched)

        except Exception as e:
            log.error("Error in pacing loop: %s", e)

        await asyncio.sleep(TICK_INTERVAL_SECONDS)


async def main() -> None:
    engine = make_engine()
    await init_schema(engine)
    log.info("SmartDialer Worker Daemon started.")

    # Run Pacing Loop and Reaper Loop concurrently
    await asyncio.gather(
        run_pacing_loop(engine),
        run_reaper_loop(engine),
    )


if __name__ == "__main__":
    asyncio.run(main())
