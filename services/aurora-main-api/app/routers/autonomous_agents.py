"""
Aurora LTS — Autonomous Agents Router (P2-04)
================================================
Generic dispatch endpoint for any registered autonomous service.

  GET  /api/v1/autonomous/agents
       List registered agent names + brief description.

  POST /api/v1/autonomous/agents/{agent_name}/run
       Invoke the named agent. Payload is forwarded to the service's
       `run(payload, db)`. JSON in, AutonomousResult shape out.

AUTH:
  require_admin — only the founder/operator can invoke directly.
  (Production schedulers can use api-key auth via a separate route.)
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from aurora_shared.database import get_db
from aurora_shared.middleware.auth_middleware import require_admin
from aurora_shared.middleware.rate_limit import limiter
from app.services.autonomous.registry import get_service, list_services

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/autonomous", tags=["autonomous"])


class RunResponse(BaseModel):
    feature: str
    status: str
    payload: dict
    duration_ms: float | None = None
    reason: str | None = None


@router.get("/agents")
@limiter.limit("60/minute")
def list_agents(
    request: Request,
    current_user=Depends(require_admin),
) -> dict:
    """List all registered autonomous services."""
    return {"agents": list_services()}


@router.post("/agents/{agent_name}/run", response_model=RunResponse)
@limiter.limit("30/minute")
async def run_agent(
    agent_name: str,
    payload: dict,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
) -> RunResponse:
    """Dispatch the named agent with the supplied payload."""
    try:
        svc = get_service(agent_name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    result = await svc.run(payload, db)
    return RunResponse(
        feature=result.feature,
        status=result.status,
        payload=result.payload,
        duration_ms=getattr(result, "duration_ms", None),
        reason=getattr(result, "reason", None),
    )
