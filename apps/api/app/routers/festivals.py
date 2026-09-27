from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.security import get_current_user
from app.services import festivals as fest_service

router = APIRouter(prefix="/festivals", tags=["festivals"])


@router.get("")
async def list_festivals(user: dict = Depends(get_current_user)) -> dict[str, Any]:
    return await fest_service.festival_list_with_flags(user["store_id"])


@router.get("/upcoming")
async def upcoming(horizon_days: int = 45, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    horizon_days = max(1, min(horizon_days, 180))
    items = await fest_service.upcoming_festivals(user["store_id"], horizon_days)
    return {"items": items, "count": len(items)}


@router.get("/{festival_id}/evidence")
async def evidence(festival_id: str, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return await fest_service.festival_historical_evidence(user["store_id"], festival_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.get("/{festival_id}/opportunity")
async def opportunity(festival_id: str, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return await fest_service.festival_opportunity(user["store_id"], festival_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
