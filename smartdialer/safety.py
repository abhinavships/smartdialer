"""Non-Bypassable Safety Controller.

Enforces system safety constraints in strict priority order.
No flag anywhere in the codebase can disable or bypass the Safety Controller.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Tuple

from smartdialer.pacing import PacingRequest


@dataclass
class CircuitBreakerState:
    is_open: bool = False
    opened_at: float = 0.0
    failure_count: int = 0
    total_count: int = 0
    window_start: float = field(default_factory=time.time)

    def record_attempt(self, success: bool) -> None:
        now = time.time()
        # 30-second sliding window reset
        if now - self.window_start > 30.0:
            self.failure_count = 0
            self.total_count = 0
            self.window_start = now

        self.total_count += 1
        if not success:
            self.failure_count += 1

        # Check failure threshold (>50% failures with at least 5 calls)
        if self.total_count >= 5 and (self.failure_count / self.total_count) > 0.5:
            self.is_open = True
            self.opened_at = now

    def check_status(self) -> str:
        if not self.is_open:
            return "CLOSED"

        now = time.time()
        # Half-open state after 15 seconds
        if now - self.opened_at > 15.0:
            return "HALF_OPEN"

        return "OPEN"


class SafetyController:
    """Non-bypassable Safety Controller.

    Evaluates rules in strict order:
      1. Absolute concurrent-call cap
      2. Ratio cap (inflight <= available * 2.0)
      3. Abandon-rate guard (fallback to progressive)
      4. Provider circuit breaker (>50% failures in 30s)
      5. Stale-metrics guard (snapshot older than 5s)
    """

    def __init__(
        self,
        max_concurrent_calls: int = 100,
        max_ratio: float = 2.0,
        max_abandon_rate: float = 0.03,
        stale_threshold_seconds: float = 5.0,
    ) -> None:
        self.max_concurrent_calls = max_concurrent_calls
        self.max_ratio = max_ratio
        self.max_abandon_rate = max_abandon_rate
        self.stale_threshold_seconds = stale_threshold_seconds
        self.circuit_breaker = CircuitBreakerState()

    def evaluate(
        self, request: PacingRequest, current_abandon_rate: float = 0.01
    ) -> Tuple[int, str]:
        snapshot = request.snapshot
        requested = request.requested_dials
        now = time.time()

        # Rule 1: Absolute Concurrent Call Cap
        current_inflight = snapshot.ringing_calls + snapshot.connected_calls
        if current_inflight >= self.max_concurrent_calls:
            return 0, f"REJECTED: Absolute concurrent call cap ({self.max_concurrent_calls}) reached"

        available_cap = self.max_concurrent_calls - current_inflight
        approved = min(requested, available_cap)

        # Rule 2: Ratio Cap (inflight <= available * 2.0)
        max_allowed_inflight = int(snapshot.available_agents * self.max_ratio)
        if current_inflight >= max_allowed_inflight:
            return 0, f"REJECTED: Ratio cap reached ({current_inflight} inflight >= {max_allowed_inflight} max allowed)"

        ratio_headroom = max_allowed_inflight - current_inflight
        approved = min(approved, ratio_headroom)

        # Rule 3: Abandon-Rate Guard (> 3% -> fallback to progressive)
        if current_abandon_rate > self.max_abandon_rate:
            approved = min(approved, snapshot.available_agents)
            reason_suffix = " (CLAMPS to progressive due to high abandon rate)"
        else:
            reason_suffix = ""

        # Rule 4: Provider Circuit Breaker
        cb_status = self.circuit_breaker.check_status()
        if cb_status == "OPEN":
            return 0, "REJECTED: Provider circuit breaker OPEN (>50% failures in 30s)"
        elif cb_status == "HALF_OPEN":
            approved = min(approved, 1)  # allow 1 trial call in half-open state

        # Rule 5: Stale-Metrics Guard (snapshot age > 5s -> fallback to progressive)
        snapshot_age = now - snapshot.timestamp
        if snapshot_age > self.stale_threshold_seconds:
            approved = min(approved, snapshot.available_agents)
            reason_suffix += " (CLAMPS to progressive due to stale metrics snapshot)"

        approved = max(0, approved)
        return approved, f"APPROVED: {approved} dials approved{reason_suffix}"
