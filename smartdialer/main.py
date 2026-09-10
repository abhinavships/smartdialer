"""FastAPI web application for SmartDialer."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from smartdialer.calls import ingest_event
from smartdialer.db import init_schema, make_engine

engine = make_engine()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize DB schema on startup
    await init_schema(engine)
    yield
    await engine.dispose()


app = FastAPI(
    title="SmartDialer API",
    description="PostgreSQL-Native Predictive Dialer Engine",
    version="1.0.0",
    lifespan=lifespan,
)


class AgentCreate(BaseModel):
    campaign_id: int
    state: str = "AVAILABLE"


class BorrowerCreate(BaseModel):
    campaign_id: int
    phone: str


class WebhookEvent(BaseModel):
    provider_event_id: str
    call_id: int
    event_type: str


@app.get("/health")
async def health_check() -> Dict[str, str]:
    return {"status": "ok", "database": "connected"}


@app.get("/metrics")
async def get_metrics(campaign_id: int = 1) -> Dict[str, Any]:
    async with engine.begin() as conn:
        agent_counts = (
            await conn.execute(
                text(
                    "SELECT state, COUNT(*) FROM agents WHERE campaign_id = :cid GROUP BY state"
                ),
                {"cid": campaign_id},
            )
        ).fetchall()

        call_counts = (
            await conn.execute(
                text(
                    "SELECT state, COUNT(*) FROM calls GROUP BY state"
                )
            )
        ).fetchall()

    return {
        "campaign_id": campaign_id,
        "agents": {row[0]: row[1] for row in agent_counts},
        "calls": {row[0]: row[1] for row in call_counts},
    }


@app.get("/decisions")
async def get_pacing_decisions(campaign_id: int = 1, limit: int = 20) -> List[Dict[str, Any]]:
    async with engine.begin() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT id, ts, requested, approved, reason, snapshot_json
                    FROM pacing_decisions
                    WHERE campaign_id = :cid
                    ORDER BY ts DESC
                    LIMIT :limit
                    """
                ),
                {"cid": campaign_id, "limit": limit},
            )
        ).fetchall()

    return [
        {
            "id": r[0],
            "timestamp": r[1].isoformat(),
            "requested": r[2],
            "approved": r[3],
            "reason": r[4],
            "snapshot": r[5],
        }
        for r in rows
    ]


@app.post("/agents")
async def create_agent(agent: AgentCreate) -> Dict[str, Any]:
    async with engine.begin() as conn:
        aid = (
            await conn.execute(
                text(
                    "INSERT INTO agents (state, campaign_id) VALUES (:state, :cid) RETURNING id"
                ),
                {"state": agent.state, "cid": agent.campaign_id},
            )
        ).scalar_one()
    return {"id": aid, "state": agent.state, "campaign_id": agent.campaign_id}


@app.post("/borrowers")
async def create_borrower(borrower: BorrowerCreate) -> Dict[str, Any]:
    async with engine.begin() as conn:
        bid = (
            await conn.execute(
                text(
                    "INSERT INTO borrowers (phone, campaign_id, state) VALUES (:phone, :cid, 'QUEUED') RETURNING id"
                ),
                {"phone": borrower.phone, "cid": borrower.campaign_id},
            )
        ).scalar_one()
    return {"id": bid, "phone": borrower.phone, "campaign_id": borrower.campaign_id}


@app.post("/webhooks/provider")
async def provider_webhook(evt: WebhookEvent) -> Dict[str, str]:
    try:
        res = await ingest_event(engine, evt.provider_event_id, evt.call_id, evt.event_type)
        return {"status": res, "provider_event_id": evt.provider_event_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
