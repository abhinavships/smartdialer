-- SmartDialer schema. Single idempotent file applied at startup.
-- No Alembic: prototype has one schema version. Add Alembic when migrations
-- must run against data that can't be dropped.

CREATE TABLE IF NOT EXISTS agents (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    state         TEXT NOT NULL DEFAULT 'OFFLINE'
                  CHECK (state IN ('OFFLINE','AVAILABLE','RESERVED','DIALING',
                                   'CONNECTED','WRAP_UP','PAUSED')),
    reserved_until TIMESTAMPTZ,
    campaign_id   BIGINT NOT NULL,
    version       BIGINT NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS ix_agents_campaign_available
    ON agents (campaign_id) WHERE state = 'AVAILABLE';

CREATE TABLE IF NOT EXISTS borrowers (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    phone         TEXT NOT NULL,
    campaign_id   BIGINT NOT NULL,
    state         TEXT NOT NULL DEFAULT 'QUEUED',
    attempt_count INT NOT NULL DEFAULT 0,
    locked_until  TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS calls (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    agent_id    BIGINT REFERENCES agents(id),
    borrower_id BIGINT REFERENCES borrowers(id),
    provider    TEXT,
    state       TEXT NOT NULL DEFAULT 'QUEUED'
                CHECK (state IN ('QUEUED','RESERVED','INITIATED','RINGING',
                                 'ANSWERED','CONNECTED','COMPLETED','FAILED','CANCELLED')),
    state_rank  INT NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_calls_active
    ON calls (state) WHERE state NOT IN ('COMPLETED','FAILED','CANCELLED');

-- Invariant 1, second independent guard: at most one non-terminal call per
-- agent. Agent DIALING/CONNECTED are the agent-side mirror of these call
-- states. Even if reservation lock logic is ever wrong, Postgres refuses the
-- insert. FOR UPDATE SKIP LOCKED is the primary guard; this is belt-and-braces.
CREATE UNIQUE INDEX IF NOT EXISTS one_active_call_per_agent
    ON calls (agent_id)
    WHERE state IN ('RESERVED','INITIATED','RINGING','ANSWERED','CONNECTED');

-- Idempotency: dedup key is the provider's own event id. ON CONFLICT DO NOTHING
-- on this PK drops replays.
CREATE TABLE IF NOT EXISTS provider_events (
    provider_event_id TEXT PRIMARY KEY,
    call_id           BIGINT REFERENCES calls(id),
    event_type        TEXT NOT NULL,
    received_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS pacing_decisions (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts            TIMESTAMPTZ NOT NULL DEFAULT now(),
    campaign_id   BIGINT NOT NULL,
    requested     INT NOT NULL,
    approved      INT NOT NULL,
    reason        TEXT NOT NULL,
    snapshot_json JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_pacing_campaign_ts
    ON pacing_decisions (campaign_id, ts DESC);
