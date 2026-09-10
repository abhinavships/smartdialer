"""Mock Telephony Provider abstractions meeting ProviderProtocol specification."""
from __future__ import annotations

import asyncio
import random
import time
from typing import AsyncGenerator, Protocol


class ProviderProtocol(Protocol):
    async def place_call(
        self, call_id: int, borrower_phone: str
    ) -> AsyncGenerator[tuple[str, str], None]:
        """Place an outbound call. Yields (event_type, provider_event_id) tuples."""
        ...


class FastProvider:
    """Fast, predictable provider: 100-200ms setup, 2% failure rate, in-order events."""

    def __init__(self, failure_rate: float = 0.02) -> None:
        self.failure_rate = failure_rate

    async def place_call(
        self, call_id: int, borrower_phone: str
    ) -> AsyncGenerator[tuple[str, str], None]:
        # Setup latency 100-200ms
        await asyncio.sleep(random.uniform(0.1, 0.2))

        # Check failure
        if random.random() < self.failure_rate:
            yield ("FAILED", f"evt_fast_fail_{call_id}_{int(time.time()*1000)}")
            return

        # In-order happy path
        yield ("INITIATED", f"evt_fast_init_{call_id}")
        await asyncio.sleep(0.1)
        yield ("RINGING", f"evt_fast_ring_{call_id}")
        await asyncio.sleep(random.uniform(0.2, 0.5))
        yield ("ANSWERED", f"evt_fast_ans_{call_id}")
        yield ("CONNECTED", f"evt_fast_conn_{call_id}")
        await asyncio.sleep(random.uniform(1.0, 3.0))
        yield ("COMPLETED", f"evt_fast_comp_{call_id}")


class FlakyProvider:
    """Flaky provider: 500-2000ms setup, 15% timeouts, ~30% duplicates, out-of-order events."""

    def __init__(self, timeout_rate: float = 0.15, duplicate_rate: float = 0.30) -> None:
        self.timeout_rate = timeout_rate
        self.duplicate_rate = duplicate_rate

    async def place_call(
        self, call_id: int, borrower_phone: str
    ) -> AsyncGenerator[tuple[str, str], None]:
        # High setup latency 500-2000ms
        await asyncio.sleep(random.uniform(0.5, 2.0))

        # Timeout / Failure check
        if random.random() < self.timeout_rate:
            yield ("FAILED", f"evt_flaky_timeout_{call_id}_{int(time.time()*1000)}")
            return

        # Flaky out-of-order or duplicate behavior
        events = [
            ("INITIATED", f"evt_flaky_init_{call_id}"),
            ("RINGING", f"evt_flaky_ring_{call_id}"),
            ("ANSWERED", f"evt_flaky_ans_{call_id}"),
            ("CONNECTED", f"evt_flaky_conn_{call_id}"),
            ("COMPLETED", f"evt_flaky_comp_{call_id}"),
        ]

        # Occasionally shuffle non-terminal events to simulate out-of-order delivery
        if random.random() < 0.4:
            # Swap ANSWERED and RINGING or COMPLETED and ANSWERED
            events[1], events[2] = events[2], events[1]

        for etype, eid in events:
            yield (etype, eid)
            # Duplicate injection for ANSWERED events
            if etype == "ANSWERED" and random.random() < self.duplicate_rate:
                # Same event ID duplicate or distinct event ID duplicate
                dup_eid = eid if random.random() < 0.5 else f"{eid}_dup"
                yield ("ANSWERED", dup_eid)

            await asyncio.sleep(random.uniform(0.1, 0.4))
