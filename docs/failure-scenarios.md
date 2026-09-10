# SmartDialer Failure Scenarios & Self-Healing Mechanisms

This document outlines system recovery behavior across 5 critical failure scenarios, detailed step-by-step as numbered state transition traces, highlighting the exact architectural mechanism protecting system integrity.

---

## 1. Worker Crash After Call Initiated

### Context
A worker process reserves an agent, locks a borrower, creates a call record, and dispatches an outbound call request to the provider. Immediately after dispatching, the worker process crashes (e.g., OOM killer, node eviction, power failure).

### Numbered State Transition Trace
1. **$t=0$**: Worker executes `reserve_agent()` via `SELECT ... FOR UPDATE SKIP LOCKED`.
   - Agent state: `AVAILABLE` $\rightarrow$ `RESERVED` (`reserved_until = now() + 30s`).
   - Borrower state: `QUEUED` $\rightarrow$ `LOCKED` (`locked_until = now() + 30s`).
2. **$t=1$**: Worker inserts `calls` record with `state = 'INITIATED'`, `state_rank = 2`.
3. **$t=2$**: Worker process crashes. The in-flight call continues ringing at provider, but the local worker process holding reference state is dead.
4. **$t=2\dots30$**: Call state remains `INITIATED` (`state_rank = 2`). Agent state remains `RESERVED`. Lease countdown ticks down.
5. **$t=30$**: Lease expires (`now() > reserved_until`).
6. **$t=33$**: Reaper coroutine runs on its 5-second polling interval and detects expired agent and call leases:
   - Executes query: `SELECT * FROM calls WHERE state NOT IN ('COMPLETED','FAILED','CANCELLED') AND updated_at < now() - INTERVAL '30s'`
   - Updates call state: `INITIATED` (rank 2) $\rightarrow$ `FAILED` (rank 6) via `WHERE state_rank < 6`.
   - Updates agent state: `RESERVED` $\rightarrow$ `AVAILABLE` (`reserved_until = NULL`).
   - Updates borrower state: `locked_until` cleared, `attempt_count` incremented $1 \rightarrow 2$, state set back to `QUEUED`.

### Saving Mechanism
**Lease-Based Reaper Coroutine (`reserved_until` + 5s Reaper Sweep)**. No long-lived worker state exists without a time-bound lease. Expired leases are cleaned deterministically by background reaper tasks.

---

## 2. Telephony Provider Outage

### Context
An upstream provider experiences an outage, resulting in API call timeouts, 5xx errors, or network drops for outbound call dispatches.

### Numbered State Transition Trace & Component Handling

#### A. Existing In-Flight Calls
1. Calls currently in `RINGING` (rank 3) or `INITIATED` (rank 2) receive no provider webhooks.
2. After 30 seconds, their `reserved_until` leases expire.
3. The Reaper coroutine transitions call states to `FAILED` (rank 6) and returns agents to `AVAILABLE`.

#### B. New Call Requests & Circuit Breaker
1. Call Allocator attempts new call dispatches to provider.
2. Provider responds with 500 errors or network timeouts ($>50\%$ failures recorded in Safety Controller's 30-second sliding window).
3. **Circuit Breaker Rule Trips**: Safety Controller transitions provider circuit breaker from `CLOSED` $\rightarrow$ `OPEN`.
4. Subsequent call allocations are immediately rejected at Safety Controller without hitting the provider API.
5. After 15 seconds, circuit breaker transitions to `HALF_OPEN`, allowing a trial request to check provider health.

#### C. Retries
1. Borrowers whose calls failed during the outage have `attempt_count` incremented by 1.
2. Borrower state resets to `QUEUED` with exponential backoff delay (`locked_until = now() + backoff`).
3. Retries are held in `QUEUED` state while the circuit breaker is `OPEN`.

#### D. Pacing Behavior
1. EWMA answer rate drops due to failed calls.
2. Safety Controller Abandon-Rate Guard and Circuit Breaker force `desired_dials = 0`.
3. Pacing loop writes decision `approved = 0`, `reason = 'circuit_breaker_open'` to `pacing_decisions` table.

### Saving Mechanism
**Safety Controller Provider Circuit Breaker (>50% failures in 30s window $\rightarrow$ OPEN, HALF_OPEN after 15s) + Abandon-Rate Guard + Exponential Borrower Backoff**.

---

## 3. Mass Agent Disconnection (40 of 100 Agents Disappear in Seconds)

### Context
A network partition or client-side crash suddenly disconnects 40 out of 100 active agents while 50 calls are currently inflight.

### Numbered State Transition Trace
1. **$t=0$**: System has 100 active agents (60 `CONNECTED`, 40 `AVAILABLE`), 50 inflight calls (`RINGING`/`INITIATED`).
2. **$t=1$**: 40 agents abruptly disconnect or log out. Available agent count drops from 40 $\rightarrow$ 0.
3. **$t=2$**: Next pacing engine tick runs (1-second loop interval).
4. **$t=3$**: Safety Controller evaluates Ratio Cap rule:
   $$\text{max\_allowed\_inflight} = \text{available\_agents} \times 2.0 = 0 \times 2.0 = 0$$
5. **$t=4$**: Pacing math calculates desired dials:
   $$\text{headroom} = \text{agents\_free\_soon} - \text{expected\_answers\_inflight} < 0 \implies \text{desired\_dials} = 0$$
6. Safety Controller caps approved dials to `0`. No new calls are dispatched despite ongoing campaign targets.
7. Disconnected agents in `CONNECTED` state trigger agent state updates $\rightarrow$ `OFFLINE` when webhooks report disconnect or when agent WebSocket drops.

### Saving Mechanism
**Safety Controller Ratio Cap (`inflight <= available_agents * 2.0`) + Headroom Clamp (`desired = 0` when headroom $\le 0$)**. Prevents over-dialing when available agent pool shrinks rapidly.

---

## 4. Triplicate Provider Event Delivery

### Context
A telephony provider sends the exact same status webhook (`provider_event_id = "evt_abc123"`, `call_id = 99`, `event_type = "ANSWERED"`) three separate times due to network retries.

### Numbered State Transition Trace
1. **First Arrival ($t=0ms$)**:
   - Application issues:
     ```sql
     INSERT INTO provider_events (provider_event_id, call_id, event_type)
     VALUES ('evt_abc123', 99, 'ANSWERED')
     ON CONFLICT (provider_event_id) DO NOTHING;
     ```
   - Transaction inserts 1 row. Event processing continues.
   - Call state updates: `RINGING` (rank 3) $\rightarrow$ `ANSWERED` (rank 4).
2. **Second Arrival ($t=50ms$)**:
   - Application issues identical `INSERT INTO provider_events ... ON CONFLICT DO NOTHING`.
   - Postgres detects primary key conflict on `provider_event_id = 'evt_abc123'`.
   - Query returns `0 rows inserted`.
   - Application detects duplicate event delivery and aborts further processing immediately.
3. **Third Arrival ($t=200ms$)**:
   - Database conflict drops insert (`0 rows inserted`). Aborted immediately.

### Saving Mechanism
**Database Deduplication via `provider_events` Primary Key constraint + `INSERT ... ON CONFLICT DO NOTHING`**. Guaranteed idempotency at the storage tier.

---

## 5. Out-of-Order Provider Event Delivery (`COMPLETED` $\rightarrow$ `ANSWERED` $\rightarrow$ `RINGING`)

### Context
Network delays cause provider events for Call #42 to arrive in reverse chronological order: `COMPLETED` (rank 6) arrives first, followed by `ANSWERED` (rank 4), and finally `RINGING` (rank 3).

### Numbered State Transition Trace
1. **Initial State**: Call #42 state is `INITIATED` (`state_rank = 2`).
2. **Step 1: `COMPLETED` event arrives (rank 6)**:
   - Executes:
     ```sql
     UPDATE calls
     SET state = 'COMPLETED', state_rank = 6, updated_at = now()
     WHERE id = 42 AND state_rank < 6;
     ```
   - Evaluation: `2 < 6` is `TRUE`. Row updated.
   - Call #42 state becomes `COMPLETED` (`state_rank = 6`). Terminal state reached.
3. **Step 2: `ANSWERED` event arrives (rank 4)**:
   - Executes:
     ```sql
     UPDATE calls
     SET state = 'ANSWERED', state_rank = 4, updated_at = now()
     WHERE id = 42 AND state_rank < 4;
     ```
   - Evaluation: `WHERE state_rank < 4` evaluates to `6 < 4` which is `FALSE`.
   - DB result: `0 rows updated`. Event ignored.
4. **Step 3: `RINGING` event arrives (rank 3)**:
   - Executes:
     ```sql
     UPDATE calls
     SET state = 'RINGING', state_rank = 3, updated_at = now()
     WHERE id = 42 AND state_rank < 3;
     ```
   - Evaluation: `WHERE state_rank < 3` evaluates to `6 < 3` which is `FALSE`.
   - DB result: `0 rows updated`. Event ignored.

### Saving Mechanism
**Monotonic State Rank Guard (`WHERE state_rank < $new_rank`) with Absorbing Terminal Ranks**. Out-of-order events with lower ranks can never overwrite a higher rank or mutate terminal call states.
