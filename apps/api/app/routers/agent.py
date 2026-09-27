from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.database import db
from app.security import get_current_user
from app.agent import actions as agent_actions
from app.agent import orchestrator
from app.agent.actions import ActionError

router = APIRouter(prefix="/agent", tags=["agent"])


def _http(exc: ActionError) -> HTTPException:
    code_map = {
        "not_found": 404,
        "bad_status": 409,
        "already_executing": 409,
        "bad_type": 400,
        "bad_payload": 400,
        "below_floor": 422,
        "execution_failed": 500,
    }
    return HTTPException(code_map.get(exc.code, 400), str(exc))


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=500)


class RecoStatusIn(BaseModel):
    status: str = Field(pattern="^(REVIEWED|APPROVED|REJECTED|DISMISSED|COMPLETED)$")
    note: Optional[str] = Field(default=None, max_length=300)


class PrepareIn(BaseModel):
    action_type: str = Field(pattern="^(create_purchase|price_change|inventory_adjust)$")
    payload: dict[str, Any]
    recommendation_id: Optional[str] = None


class ApproveIn(BaseModel):
    force: bool = False


@router.post("/ask")
async def ask(body: AskIn, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    return await orchestrator.ask(user["store_id"], user["id"], body.question)


@router.get("/brief")
async def brief(user: dict = Depends(get_current_user)) -> dict[str, Any]:
    return await orchestrator.business_brief(user["store_id"])


@router.get("/recommendations")
async def list_recommendations(
    status: str = "open", limit: int = 20, user: dict = Depends(get_current_user)
) -> dict[str, Any]:
    items = await orchestrator.list_recommendations(user["store_id"], status, limit)
    return {"items": items, "count": len(items)}


@router.post("/recommendations/{reco_id}/status")
async def recommendation_status(
    reco_id: str, body: RecoStatusIn, user: dict = Depends(get_current_user)
) -> dict[str, Any]:
    try:
        return await orchestrator.set_recommendation_status(
            user["store_id"], user["id"], reco_id, body.status, body.note
        )
    except ActionError as exc:
        raise _http(exc)


@router.post("/actions/prepare")
async def prepare(body: PrepareIn, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return await agent_actions.prepare_action(
            user["store_id"],
            user["id"],
            action_type=body.action_type,
            payload=body.payload,
            recommendation_id=body.recommendation_id,
        )
    except ActionError as exc:
        raise _http(exc)


@router.post("/actions/{action_id}/approve")
async def approve(
    action_id: str, body: ApproveIn, user: dict = Depends(get_current_user)
) -> dict[str, Any]:
    try:
        return await agent_actions.approve_and_execute(
            user["store_id"], user["id"], action_id, force=body.force
        )
    except ActionError as exc:
        raise _http(exc)


@router.post("/actions/{action_id}/cancel")
async def cancel(action_id: str, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return await agent_actions.cancel_action(user["store_id"], user["id"], action_id)
    except ActionError as exc:
        raise _http(exc)


@router.get("/actions")
async def list_actions(limit: int = 50, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    items = await agent_actions.list_actions(user["store_id"], limit)
    return {"items": items, "count": len(items)}


@router.get("/actions/{action_id}")
async def get_action(action_id: str, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return await agent_actions.get_action(user["store_id"], action_id)
    except ActionError as exc:
        raise _http(exc)


@router.get("/conversations")
async def conversations(limit: int = 20, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    rows = await db.fetch(
        """
        select id, question, answer, tools_used, recommendations_created, engine, created_at
        from ai_conversations where store_id = $1
        order by created_at desc limit $2
        """,
        user["store_id"],
        max(1, min(int(limit), 100)),
    )
    items = []
    for r in rows:
        d = dict(r)
        if isinstance(d.get("tools_used"), str):
            d["tools_used"] = json.loads(d["tools_used"])
        items.append(d)
    return {"items": items, "count": len(items)}
