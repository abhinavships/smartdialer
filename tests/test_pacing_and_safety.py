import time
import pytest

from smartdialer.pacing import PredictivePacingEngine, ProgressivePacingEngine, SystemSnapshot
from smartdialer.safety import SafetyController

pytestmark = pytest.mark.asyncio


async def test_progressive_pacing():
    engine = ProgressivePacingEngine()
    snapshot = SystemSnapshot(
        campaign_id=1,
        available_agents=25,
        wrap_up_agents=5,
        ringing_calls=10,
        connected_calls=15,
        historical_answer_rate=0.5,
        historical_avg_talk_time=120.0,
        historical_avg_setup_time=2.0,
    )
    req = engine.calculate_dials(snapshot)
    assert req.requested_dials == 25
    assert req.mode == "PROGRESSIVE"


async def test_predictive_pacing_headroom():
    engine = PredictivePacingEngine(alpha=0.2)
    snapshot = SystemSnapshot(
        campaign_id=1,
        available_agents=10,
        wrap_up_agents=0,
        ringing_calls=0,
        connected_calls=0,
        historical_answer_rate=0.5,
        historical_avg_talk_time=120.0,
        historical_avg_setup_time=2.0,
    )
    req = engine.calculate_dials(snapshot)
    # headroom = 10 - 0 = 10, ceil(10 / 0.5) = 20
    assert req.requested_dials == 20


async def test_safety_controller_absolute_cap():
    controller = SafetyController(max_concurrent_calls=50)
    engine = ProgressivePacingEngine()
    snapshot = SystemSnapshot(
        campaign_id=1,
        available_agents=60,
        wrap_up_agents=0,
        ringing_calls=50,
        connected_calls=0,
        historical_answer_rate=0.5,
        historical_avg_talk_time=120.0,
        historical_avg_setup_time=2.0,
    )
    req = engine.calculate_dials(snapshot)
    approved, reason = controller.evaluate(req)
    assert approved == 0
    assert "Absolute concurrent call cap" in reason


async def test_safety_controller_ratio_cap():
    controller = SafetyController(max_concurrent_calls=100, max_ratio=2.0)
    engine = ProgressivePacingEngine()
    # 5 available agents -> max allowed inflight = 10
    snapshot = SystemSnapshot(
        campaign_id=1,
        available_agents=5,
        wrap_up_agents=0,
        ringing_calls=10,
        connected_calls=0,
        historical_answer_rate=0.5,
        historical_avg_talk_time=120.0,
        historical_avg_setup_time=2.0,
    )
    req = engine.calculate_dials(snapshot)
    approved, reason = controller.evaluate(req)
    assert approved == 0
    assert "Ratio cap reached" in reason


async def test_circuit_breaker():
    controller = SafetyController()
    # Record 6 consecutive failures to trip circuit breaker (>50%)
    for _ in range(6):
        controller.circuit_breaker.record_attempt(success=False)

    assert controller.circuit_breaker.check_status() == "OPEN"

    engine = ProgressivePacingEngine()
    snapshot = SystemSnapshot(
        campaign_id=1,
        available_agents=10,
        wrap_up_agents=0,
        ringing_calls=0,
        connected_calls=0,
        historical_answer_rate=0.5,
        historical_avg_talk_time=120.0,
        historical_avg_setup_time=2.0,
    )
    req = engine.calculate_dials(snapshot)
    approved, reason = controller.evaluate(req)
    assert approved == 0
    assert "circuit breaker OPEN" in reason
