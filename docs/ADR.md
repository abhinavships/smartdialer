# Architectural Decision Record (ADR): Postgres-Native Concurrency & Storage

## Context & Rationale: PostgreSQL as the Sole Primitive

SmartDialer intentionally eliminates Redis, Kafka, and external microservice boundaries in favor of a single PostgreSQL database instance.

### Why Postgres Alone?
1. **Single Source of Truth & ACID Atomicity**: State changes across agent availability, call state, borrower retry queues, and event logging execute within atomic database transactions. Dual-write inconsistencies between a state store (e.g. Postgres) and a message broker (e.g. Kafka) or cache (e.g. Redis) are completely eliminated.
2. **Simplified Operational Surface**: Operates with zero external coordination services, reducing deployment overhead, cluster split-brain risks, and operational complexity for prototype and growth-stage workloads.
3. **Native Row Locking as a Distributed Lock**: PostgreSQL `SELECT ... FOR UPDATE SKIP LOCKED` acts as an ultra-low-latency distributed lock engine without needing Redis/Redlock protocols.

---

## Concurrency Primitive: `SELECT ... FOR UPDATE SKIP LOCKED`

### What `SKIP LOCKED` Buys
- **Lock-Free Parallelism**: Worker processes scan and acquire available agents without blocking each other or spinning in retry loops.
- **Zero Double-Reservations**: Concurrently executing workers acquire distinct agent rows automatically. Combined with a partial unique index (`uq_agent_active_reservation ON agents(id) WHERE state IN ('RESERVED','DIALING','CONNECTED')`), double-booking an agent is structurally impossible.

### What `SKIP LOCKED` Costs
- **Heap and Index Scanning Overhead**: Under heavy load, workers scan past locked rows in the index/heap to find unreserved `AVAILABLE` rows. As active agent count increases, scan costs grow proportionally.
- **Connection and Transaction Pressure**: Every lock transaction holds a active DB connection, coupling system concurrency directly to PostgreSQL connection pool limits (`max_connections`).

---

## Architectural Trade-offs & Limitations

1. **Single-Writer DB Contention**: High-frequency updates to hot tables (`agents`, `calls`, `borrowers`) create lock contention and write amplification on single-primary Postgres.
2. **No Native Cross-Region Failover**: Single-primary database topology prevents multi-datacenter active-active deployments without complex regional database sharding.
3. **Reaper Latency Floor**: Crash recovery relies on a 5-second polling reaper loop. If a worker dies mid-execution, orphaned agent/borrower leases remain locked for up to `reserved_until` (30s) + reaper tick (5s).

---

## Bottlenecks & Scaling Roadmap (At 10,000+ Agents)

Scaling to 10k+ concurrent agents must address system bottlenecks in strict sequential order. **Crucially, adding more worker servers before sharding makes lock contention worse, not better, as more processes scan the exact same hot row indices.**

```
[Bottleneck: Hot Row Lock Contention] ---> Fix 1: Shard Agent Pool
                                                   |
                                                   v
[Bottleneck: Metric Query Latency]  ---> Fix 2: Trigger-Maintained Counters
                                                   |
                                                   v
[Bottleneck: Process Co-ordination] ---> Fix 3: Split Campaign-Owning Workers
```

### 1. Primary Bottleneck: Lock Contention on Hot Agent Rows
At 10,000 agents, the initial bottleneck is not CPU or network bandwidth, but index/heap scan contention on the `agents` table. When dozens of workers simultaneously issue `SELECT ... FOR UPDATE SKIP LOCKED` on the same `AVAILABLE` index, scan times lengthen exponentially.
* **Fix 1: Shard the Agent Pool**. Partition agents into explicit shards (by campaign, agent ID range, or hash). Workers are assigned specific shards to claim agents from. Contention drops linearly with the number of shards.

### 2. Secondary Bottleneck: Metric Aggregation Query Overhead
Once agent reservation is sharded, the next ceiling is the pacing loop executing full-table metric aggregation queries (`COUNT(*) FILTER WHERE state = ...`) on every tick.
* **Fix 2: Replace Synchronous Metrics Queries with Trigger-Maintained Counter Table**. Use database triggers on `agents` and `calls` status changes to update a lightweight `campaign_counters` summary table. Pacing reads pre-aggregated counters in $O(1)$ time.

### 3. Tertiary Bottleneck: Process Ownership & Dispatch Scaling
With database contention resolved via sharding and trigger counters, CPU scheduling within single worker nodes becomes the final constraint.
* **Fix 3: Split into Campaign-Owning Worker Processes**. Partition campaigns across independent worker processes, where each process exclusively manages pacing and dispatch for its assigned campaigns without cross-process locking overhead.
