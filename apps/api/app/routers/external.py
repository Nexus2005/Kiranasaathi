from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.security import get_current_user
from app.services import agent_reach, evidence
from app.services.agent_reach import ReachError

router = APIRouter(prefix="/external", tags=["external-intelligence"])


class ManualEvidenceIn(BaseModel):
    title: str = Field(min_length=4, max_length=300)
    summary: Optional[str] = Field(default=None, max_length=600)
    source_url: Optional[str] = Field(default=None, max_length=500)
    category: Optional[str] = Field(default=None, max_length=80)
    region: Optional[str] = Field(default=None, max_length=120)


@router.get("/context")
async def context(category: Optional[str] = None, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    """External intelligence pack (verified signals + separation note)."""
    return await evidence.external_context(user["store_id"], category)


@router.get("/evidence")
async def list_evidence(
    status: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 50,
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    items = await evidence.list_evidence(user["store_id"], status=status, category=category, limit=limit)
    return {"items": items, "count": len(items)}


@router.post("/evidence", status_code=201)
async def add_manual(body: ManualEvidenceIn, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    try:
        item = await evidence.add_manual_evidence(
            user["store_id"], user["id"],
            title=body.title, summary=body.summary,
            source_url=body.source_url, category=body.category, region=body.region,
        )
        return item
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/evidence/{evidence_id}/verify")
async def verify(evidence_id: str, body: dict, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    decision = (body or {}).get("decision")
    try:
        return await evidence.verify_evidence(user["store_id"], user["id"], evidence_id, decision)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/sources")
async def sources(user: dict = Depends(get_current_user)) -> dict[str, Any]:
    items = await evidence.list_sources(user["store_id"])
    return {
        "items": items,
        "count": len(items),
        "note": (
            "RSS/web sources are opt-in and robots-respecting. Nothing is fetched "
            "unless a source is enabled and you trigger a refresh."
        ),
    }


@router.post("/sources/{source_id}/enable")
async def enable_source(source_id: str, body: dict, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    enabled = bool((body or {}).get("enabled", True))
    try:
        return await evidence.set_source_enabled(user["store_id"], source_id, enabled)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.post("/refresh")
async def refresh(user: dict = Depends(get_current_user)) -> dict[str, Any]:
    """Refresh ALL enabled non-manual sources now (on-demand, rate-limited by design)."""
    store_id = user["store_id"]
    rows = await agent_reach.db.fetch(
        "select * from external_sources where store_id = $1 and enabled and kind <> 'manual'",
        store_id,
    )
    results = []
    for r in rows:
        try:
            results.append(await agent_reach.refresh_source(store_id, r))
        except ReachError as exc:
            results.append({"source_key": r["source_key"], "error": str(exc)})
    auto = await evidence.auto_verify_pending(store_id)
    return {
        "sources": results,
        "auto_verification": auto,
        "note": "Only enabled sources were contacted. Candidates stored as UNVERIFIED unless OFFICIAL.",
    }
