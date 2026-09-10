"""Predictive & Progressive Pacing Engine. Decoupled from providers and database."""
from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict


@dataclass(frozen=True)
class SystemSnapshot:
    campaign_id: int
    available_agents: int
    wrap_up_agents: int
    ringing_calls: int
    connected_calls: int
    historical_answer_rate: float
    historical_avg_talk_time: float
    historical_avg_setup_time: float
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class PacingRequest:
    campaign_id: int
    requested_dials: int
    mode: str
    reason: str
    snapshot: SystemSnapshot

    def to_json(self) -> Dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "requested_dials": self.requested_dials,
            "mode": self.mode,
            "reason": self.reason,
            "snapshot": asdict(self.snapshot),
        }


class PacingEngine:
    def calculate_dials(self, snapshot: SystemSnapshot) -> PacingRequest:
        raise NotImplementedError


class ProgressivePacingEngine(PacingEngine):
    """Progressive Pacing: 1 available agent = 1 target dial."""

    def calculate_dials(self, snapshot: SystemSnapshot) -> PacingRequest:
        requested = max(0, snapshot.available_agents)
        return PacingRequest(
            campaign_id=snapshot.campaign_id,
            requested_dials=requested,
            mode="PROGRESSIVE",
            reason="Progressive mode: 1-to-1 available agent mapping",
            snapshot=snapshot,
        )


class PredictivePacingEngine(PacingEngine):
    """Predictive Pacing Engine: EWMA answer rate + free agent headroom prediction."""

    def __init__(self, alpha: float = 0.2) -> None:
        self.alpha = alpha
        self.current_ewma_answer_rate: float | None = None

    def update_answer_rate(self, latest_sample: float) -> float:
        if self.current_ewma_answer_rate is None:
            self.current_ewma_answer_rate = latest_sample
        else:
            self.current_ewma_answer_rate = (
                self.alpha * latest_sample + (1 - self.alpha) * self.current_ewma_answer_rate
            )
        # Clamp to [0.05, 0.95]
        return min(0.95, max(0.05, self.current_ewma_answer_rate))

    def calculate_dials(self, snapshot: SystemSnapshot) -> PacingRequest:
        p = self.update_answer_rate(snapshot.historical_answer_rate)

        # Expected answers from inflight ringing calls
        expected_answers_inflight = snapshot.ringing_calls * p

        # Free agents soon: available + wrap_up probability estimate
        # P(wrap up ends within setup time) approx setup_time / avg_talk_time
        wrap_prob = min(
            1.0,
            snapshot.historical_avg_setup_time / max(1.0, snapshot.historical_avg_talk_time),
        )
        agents_free_soon = snapshot.available_agents + (snapshot.wrap_up_agents * wrap_prob)

        # Headroom calculation
        headroom = agents_free_soon - expected_answers_inflight

        if headroom > 0:
            desired = math.ceil(headroom / p)
            reason = f"Predictive headroom {headroom:.2f} > 0, answer_rate EWMA={p:.2f}"
        else:
            desired = 0
            reason = f"Predictive headroom {headroom:.2f} <= 0 (expected answers {expected_answers_inflight:.2f})"

        return PacingRequest(
            campaign_id=snapshot.campaign_id,
            requested_dials=desired,
            mode="PREDICTIVE",
            reason=reason,
            snapshot=snapshot,
        )
