# SmartDialer 📞⚡

A high-throughput, fault-tolerant **Predictive Dialer Prototype** built with **Python 3.11, FastAPI, and PostgreSQL**. 

SmartDialer leverages PostgreSQL as its single concurrency primitive and coordination layer—eliminating dependencies on Redis, Kafka, or complex microservice infrastructure.

---

## 🌟 Core Architectural Highlights

- **Postgres-Native Concurrency**: Agent reservations utilize `SELECT ... FOR UPDATE SKIP LOCKED` inside transactions, paired with a partial unique index (`uq_agent_active_reservation ON agents(id) WHERE state IN ('RESERVED','DIALING','CONNECTED')`). Postgres row locks are the sole concurrency coordinator.
- **Monotonic State Machine**: Call transitions enforce `UPDATE calls SET state=$new, state_rank=$rank WHERE id=$id AND state_rank < $rank`. Older or duplicate provider events are dropped naturally without distributed locks. Terminal call states (`COMPLETED`, `FAILED`, `CANCELLED` at rank 6) are strictly absorbing.
- **Idempotent Event Suppression**: Provider webhooks insert into `provider_events` keyed on `provider_event_id` with `INSERT ... ON CONFLICT DO NOTHING`. Replayed events are dropped at the storage tier.
- **Lease-Based Self-Healing**: Every agent and borrower reservation has a 30-second `reserved_until` lease. A background reaper coroutine running every 5 seconds reclaims expired leases, marks stuck calls as `FAILED`, and requeues borrowers (`attempt_count + 1`).
- **Unidirectional Execution Pipeline**: `Campaign > Pacing Engine > Safety Controller > Call Allocator > Telephony Provider`.
  - **Pacing Engine**: Decoupled module returning an immutable `PacingRequest`. Has **no import path** to provider drivers.
  - **Safety Controller**: Non-bypassable safety envelope enforcing hard concurrency caps, ratio limits ($2.0\times$ available agents), abandon-rate guards, provider circuit breakers ($>50\%$ failures in 30s), and stale-metrics guards ($>5\text{s}$).
  - **Explainable Decisions**: Every pacing evaluation persists the complete state snapshot to `pacing_decisions` for full post-hoc auditability.

---

## 📐 Documentation Sitemap

Comprehensive documentation is available in the [`docs/`](./docs/) directory:

- 🏗️ **[docs/architecture.md](./docs/architecture.md)**: System pipeline diagram, Safety Controller ordering rules, complete pacing mathematics, Agent State Machine, and Monotonic Call State Machine.
- 📜 **[docs/ADR.md](./docs/ADR.md)**: Architecture Decision Record analyzing the Postgres-only design, `SKIP LOCKED` trade-offs, system limits, and the step-by-step 10k agent scaling roadmap.
- 🛡️ **[docs/failure-scenarios.md](./docs/failure-scenarios.md)**: State transition traces for 5 failure cases (worker crash, provider outage, mass agent disconnect, duplicate events, out-of-order webhooks).

---

## 🚀 Quick Start & Development

### Prerequisites
- Python 3.11+
- PostgreSQL 16
- Docker & Docker Compose (optional for local deployment)

### Local Environment Setup

1. **Clone & Install Dependencies**:
   ```bash
   git clone https://github.com/abhinavships/smartdialer.git
   cd smartdialer
   pip install -r requirements.txt
   ```

2. **Run Tests**:
   Ensure local PostgreSQL is running and `DATABASE_URL` / `TEST_DATABASE_URL` is set:
   ```bash
   export DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/smartdialer"
   pytest
   ```

---

## 🐳 Docker Deployment

Run the complete stack (PostgreSQL 16, API, and Worker) locally using Docker Compose:

```bash
docker-compose -f deploy/docker-compose.yml up --build
```

Components launched:
- **`postgres`**: PostgreSQL 16 database with health check and persistent volume (`postgres_data`).
- **`api`**: FastAPI HTTP server handling API endpoints & webhooks.
- **`worker`**: Background worker process executing pacing, reservation, and reaper tasks.

---

## ☁️ Cloud Deployment

For production deployment instructions, view **[deploy/RAILWAY.md](./deploy/RAILWAY.md)**.

> ⚠️ **Note on Vercel**: Vercel serverless functions are request-scoped and terminate after HTTP responses. SmartDialer requires continuous process execution for its 1-second pacing loop and 5-second lease reaper. Use Railway, AWS ECS, or Render for hosting.

---

## 📄 License

MIT License. See [LICENSE](LICENSE) for details.
