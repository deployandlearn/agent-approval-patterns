"""HTTP API: scan, list, approve (202 + poll), reject.

Run with:  uv run uvicorn agent_approval_patterns.api:default_app --factory --reload
"""

from __future__ import annotations

import uuid
from typing import Callable

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from agent_approval_patterns import proposals, tasks
from agent_approval_patterns.agent import run_agent
from agent_approval_patterns.approval_graph import Deps
from agent_approval_patterns.config import Settings
from agent_approval_patterns.db import connect, create_schema
from agent_approval_patterns.release_service import ReleaseService


class ApproveRequest(BaseModel):
    user: str
    action: str


class RejectRequest(BaseModel):
    user: str
    reason: str


def create_app(deps: Deps, enqueue: Callable = tasks.enqueue) -> FastAPI:
    app = FastAPI(title="agent-approval-patterns")
    url = deps.settings.database_url

    @app.post("/scan")
    def scan():
        result = run_agent(deps)
        return {"run_id": result["run_id"], "acted": result["acted"],
                "escalated": result["escalated"]}

    @app.get("/proposals")
    def list_proposals(status: str = proposals.PENDING):
        with connect(url) as conn:
            return proposals.list_by_status(conn, status)

    @app.get("/proposals/{proposal_id}")
    def get_proposal(proposal_id: int):
        with connect(url) as conn:
            row = proposals.get(conn, proposal_id)
        if row is None:
            raise HTTPException(404, "proposal not found")
        return row

    @app.post("/proposals/{proposal_id}/approve", status_code=202)
    def approve(proposal_id: int, body: ApproveRequest):
        run_id = f"approval-{uuid.uuid4().hex[:12]}"
        with connect(url) as conn:
            row = proposals.get(conn, proposal_id)
            if row is None:
                raise HTTPException(404, "proposal not found")
            # The reviewer may only pick one of the fixes the proposal offered.
            if body.action not in row["proposed_actions"]:
                raise HTTPException(422, f"{body.action!r} is not one of the proposed actions")

            won = proposals.claim(conn, proposal_id, user=body.user, approval_run_id=run_id,
                                  selected_action=body.action,
                                  stale_after=deps.settings.claim_stale_after)
            if not won:
                current = proposals.get(conn, proposal_id)
                return JSONResponse(status_code=409, content={
                    "detail": "already decided",
                    "status": current["status"], "decided_by": current["approved_by"]})

            try:
                enqueue(conn, "approval",
                        {"proposal_id": proposal_id, "approval_run_id": run_id})
            except Exception:
                # Nothing will run this approval: give the proposal back.
                proposals.release(conn, proposal_id, approval_run_id=run_id)
                raise HTTPException(503, "could not queue the approval; try again")

        return {"approval_run_id": run_id, "status_url": f"/proposals/{proposal_id}"}

    @app.post("/proposals/{proposal_id}/reject")
    def reject(proposal_id: int, body: RejectRequest):
        with connect(url) as conn:
            if not proposals.reject(conn, proposal_id, user=body.user, reason=body.reason):
                raise HTTPException(409, "proposal is not pending")
        return {"status": proposals.REJECTED}

    return app


def default_app() -> FastAPI:
    """App factory for uvicorn --factory, configured from the environment."""
    settings = Settings.from_env()
    create_schema(settings.database_url)
    return create_app(Deps(settings=settings, service=ReleaseService(settings.database_url)))
