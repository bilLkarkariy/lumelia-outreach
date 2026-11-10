"""Durable token reservations. Restarts never reset the daily allowance."""

import secrets
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import domain as d
from . import models as m
from . import schemas as s
from .agent_actions import agent_auth
from .db import get_db
from .security import admin, digest

router = APIRouter(prefix="/api/agent", tags=["agent budget"])


def limits(client):
    return {
        "per_task_tokens": int(client.state.get("per_task_tokens", 200_000)),
        "daily_tokens": int(client.state.get("daily_tokens", 1_000_000)),
        "max_seconds": 600,
        "max_turns": 6,
        "max_output_tokens": 2500,
    }


def usage(db, client):
    day = m.now().date().isoformat()
    rows = list(
        db.scalars(
            select(m.Job).where(
                m.Job.kind == "agent_usage", m.Job.payload["client_id"].as_string() == client.id
            )
        )
    )
    used = reserved = post_call_used = post_call_pending = 0
    for row in rows:
        if row.status == "running" and row.locked_at and row.updated_at < m.now() - timedelta(minutes=13):
            row.status = "uncertain"
            row.error = "Exécution interrompue : réserve conservée faute de relevé final."
        if row.payload.get("day") != day:
            continue
        observed = int(row.result.get("total_tokens", 0))
        if row.payload.get("lane") == "post_call":
            post_call_used += observed
            post_call_pending += int(row.status != "completed")
            continue
        if row.status == "completed":
            used += observed
        else:
            reserved += max(observed, int(row.payload.get("reserved_tokens", 0)))
    cap = limits(client)
    return {
        **cap,
        "day": day,
        "used_tokens": used,
        "reserved_tokens": reserved,
        "post_call_tokens": post_call_used,
        "post_call_measurements_pending": post_call_pending,
        "total_observed_tokens": used + post_call_used,
        "remaining_tokens": max(0, cap["daily_tokens"] - used - reserved),
    }


def reserve(db, client, action):
    current = usage(db, client)
    post_call = action.kind == "post_call_report" or action.payload.get("lane") == "post_call"
    if not post_call and current["remaining_tokens"] < current["per_task_tokens"]:
        return None
    secret = secrets.token_urlsafe(36)
    run = m.Job(
        kind="agent_usage",
        provider="agent",
        status="running",
        locked_at=m.now(),
        attempts=1,
        idempotency_key="agent-usage:" + m.uid(),
        payload={
            "client_id": client.id,
            "action_id": action.id,
            "day": current["day"],
            "reserved_tokens": None if post_call else current["per_task_tokens"],
            "lane": "post_call" if post_call else "research",
            "usage_token_hash": digest(secret),
        },
    )
    db.add(run)
    db.flush()
    execution_limits = limits(client)
    if post_call:
        execution_limits = {
            "per_task_tokens": None,
            "daily_tokens": None,
            "max_seconds": 1800,
            "max_turns": None,
            "max_output_tokens": None,
            "lane": "post_call",
        }
    elif action.kind == "qualify_batch":
        execution_limits = {**execution_limits, "max_turns": 12, "max_output_tokens": 8000}
    return {**execution_limits, "run_id": run.id, "usage_token": secret}


class RuntimePolicy(s.Input):
    execution_host: Literal["hosted", "local"] = "hosted"
    per_task_tokens: int = Field(default=200_000, ge=20_000, le=500_000)
    interval_minutes: int = Field(default=60, ge=60, le=1440)
    daily_tokens: int = Field(default=1_000_000, ge=20_000, le=5_000_000)


@router.post("/runtime-policy")
def configure(data: RuntimePolicy, user=Depends(admin), db: Session = Depends(get_db)):
    client = db.scalar(select(m.AgentClient).limit(1))
    if not client:
        raise HTTPException(409, "Connectez l’agent avant de définir ses limites.")
    if data.daily_tokens < data.per_task_tokens:
        raise HTTPException(422, "Le plafond quotidien doit couvrir une mission.")
    if db.scalar(select(m.AgentAction.id).where(m.AgentAction.status == "running")):
        raise HTTPException(409, "Attendez la fin de la mission en cours avant de déplacer l’agent.")
    client.state = {**client.state, **data.model_dump(), "runtime_status": "idle", "message": ""}
    last_run = db.scalar(
        select(m.Job)
        .where(m.Job.kind == "agent_usage", m.Job.payload["client_id"].as_string() == client.id)
        .order_by(m.Job.locked_at.desc())
        .limit(1)
    )
    if last_run and last_run.locked_at:
        client.state = {
            **client.state,
            "next_scheduled_at": (last_run.locked_at + timedelta(minutes=data.interval_minutes)).isoformat(),
        }
    client.next_run_at = None
    db.commit()
    return {"execution_host": data.execution_host, "budget": usage(db, client)}


@router.get("/budget")
def budget(user=Depends(admin), db: Session = Depends(get_db)):
    client = db.scalar(select(m.AgentClient).limit(1))
    return usage(db, client) if client else None


class UsageReport(s.Input):
    total_tokens: int = Field(ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cached_tokens: int = Field(default=0, ge=0)
    final: bool = False
    complete_measurement: bool = False
    model: str = Field(default="", max_length=100)
    requested_model: str = Field(default="", max_length=60)
    reasoning_effort: str = Field(default="", max_length=20)
    selection_reason: str = Field(default="", max_length=180)
    stopped_for: str = Field(default="", max_length=100)


@router.post("/runtime/usage/{run_id}")
def report(
    run_id: str,
    data: UsageReport,
    request: Request,
    client=Depends(agent_auth),
    db: Session = Depends(get_db),
):
    run = d.get(db, m.Job, run_id)
    if (
        run.kind != "agent_usage"
        or run.payload.get("client_id") != client.id
        or not secrets.compare_digest(
            run.payload.get("usage_token_hash", ""), digest(request.headers.get("x-agent-usage", ""))
        )
    ):
        raise HTTPException(403, "Relevé non autorisé.")
    previous = run.result
    if data.total_tokens < int(previous.get("total_tokens", 0)):
        raise HTTPException(409, "Le relevé de consommation ne peut pas diminuer.")
    if run.status == "completed":
        return {"recorded": True, "budget": usage(db, client)}
    run.result = {**data.model_dump(), "observed_at": m.now().isoformat()}
    if data.final:
        run.status = "completed" if data.complete_measurement else "uncertain"
    client.last_seen_at = m.now()
    action = db.get(m.AgentAction, run.payload.get("action_id", ""))
    if action and action.status == "running" and action.client_id == client.id and client.enabled:
        action.lease_until = m.now() + timedelta(minutes=12)
    cap = run.payload.get("reserved_tokens")
    current = usage(db, client)
    db.commit()
    return {
        "recorded": True,
        "budget": current,
        "stop": not client.enabled or (cap is not None and data.total_tokens >= cap),
    }
