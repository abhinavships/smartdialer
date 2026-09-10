"""SmartDialer Simulation & Load Test Harness.

Executes scenarios A, B, C, D from Tech Assignment PDF page 6:
  Scenario A: 20% answer rate, 120s talk time
  Scenario B: 50% answer rate, 90s talk time
  Scenario C: 70% answer rate, 180s talk time
  Scenario D: Dynamic changing answer rates & provider failures
"""
from __future__ import annotations

import asyncio
import logging
import time

from smartdialer.db import init_schema, make_engine
from smartdialer.pacing import PredictivePacingEngine, SystemSnapshot
from smartdialer.providers import FastProvider, FlakyProvider
from smartdialer.safety import SafetyController

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("sim.simulator")


async def run_scenario(name: str, answer_rate: float, talk_time: float, setup_time: float) -> None:
    log.info("==========================================")
    log.info("RUNNING SIMULATION SCENARIO %s", name)
    log.info("Answer Rate: %.0f%%, Avg Talk Time: %.0fs, Avg Setup Time: %.1fs", answer_rate * 100, talk_time, setup_time)
    log.info("==========================================")

    pacing_engine = PredictivePacingEngine(alpha=0.2)
    safety_controller = SafetyController(max_concurrent_calls=100, max_ratio=2.0)

    # Test under different agent counts (10, 50, 100)
    for agents in [10, 50, 100]:
        for ringing in [0, int(agents * 0.3), int(agents * 0.8)]:
            snapshot = SystemSnapshot(
                campaign_id=1,
                available_agents=agents,
                wrap_up_agents=int(agents * 0.1),
                ringing_calls=ringing,
                connected_calls=agents - ringing,
                historical_answer_rate=answer_rate,
                historical_avg_talk_time=talk_time,
                historical_avg_setup_time=setup_time,
            )

            pacing_req = pacing_engine.calculate_dials(snapshot)
            approved, reason = safety_controller.evaluate(pacing_req)

            log.info(
                "Agents=%d, Ringing=%d -> Pacing Requested=%d | Approved=%d (%s)",
                agents,
                ringing,
                pacing_req.requested_dials,
                approved,
                reason,
            )
            await asyncio.sleep(0.05)


async def main() -> None:
    log.info("Starting SmartDialer Simulator Harness...")

    # Scenario A
    await run_scenario("A", answer_rate=0.20, talk_time=120.0, setup_time=2.0)

    # Scenario B
    await run_scenario("B", answer_rate=0.50, talk_time=90.0, setup_time=1.5)

    # Scenario C
    await run_scenario("C", answer_rate=0.70, talk_time=180.0, setup_time=2.5)

    # Scenario D: Dynamic & Flaky Provider
    log.info("==========================================")
    log.info("RUNNING SCENARIO D: DYNAMIC FLAKY PROVIDER")
    log.info("==========================================")
    flaky = FlakyProvider(timeout_rate=0.20, duplicate_rate=0.30)
    for i in range(5):
        log.info("Simulating Flaky Call #%d dispatch...", i + 1)
        async for etype, eid in flaky.place_call(i + 1, f"+1555999{i}"):
            log.info("  Received event: %s (id=%s)", etype, eid)

    log.info("Simulation completed successfully.")


if __name__ == "__main__":
    asyncio.run(main())
