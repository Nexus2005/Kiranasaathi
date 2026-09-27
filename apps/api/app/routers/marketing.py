from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.security import get_current_user
from app.services import marketing, whatsapp
from app.services.marketing import CampaignError

router = APIRouter(prefix="/campaigns", tags=["marketing"])


def _http(exc: CampaignError) -> HTTPException:
    code_map = {
        "not_found": 404,
        "bad_status": 409,
        "already_sent": 409,
        "bad_payload": 400,
        "no_recipients": 422,
        "product_unavailable": 422,
        "provider_not_configured": 409,
    }
    return HTTPException(code_map.get(exc.code, 400), str(exc))


class ProductRef(BaseModel):
    product_id: str
    offer_text: Optional[str] = Field(default=None, max_length=200)


class CampaignIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    campaign_type: str
    audience: dict[str, Any] = Field(default_factory=dict)
    products: list[ProductRef] = Field(default_factory=list, max_length=5)
    message_template: Optional[str] = Field(default=None, max_length=1000)
    channel: str = "whatsapp"


class CampaignPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=120)
    audience: Optional[dict[str, Any]] = None
    products: Optional[list[ProductRef]] = Field(default=None, max_length=5)
    message_template: Optional[str] = Field(default=None, max_length=1000)


@router.get("")
async def list_campaigns(status: Optional[str] = None, limit: int = 50, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    items = await marketing.list_campaigns(user["store_id"], status, limit)
    return {"items": items, "count": len(items)}


@router.post("", status_code=201)
async def create(body: CampaignIn, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return await marketing.create_campaign(
            user["store_id"], user["id"],
            name=body.name, campaign_type=body.campaign_type,
            audience=body.audience,
            products=[p.model_dump() for p in body.products],
            message_template=body.message_template, channel=body.channel,
        )
    except CampaignError as exc:
        raise _http(exc)


@router.get("/provider")
async def provider(user: dict = Depends(get_current_user)) -> dict[str, Any]:
    return await whatsapp.provider_status(user["store_id"])


@router.get("/audience-preview")
async def audience_preview(
    segment: Optional[str] = None,
    category: Optional[str] = None,
    product_id: Optional[str] = None,
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    from app.services import customers_intel

    try:
        return await customers_intel.eligible_campaign_audience(
            user["store_id"], segment=segment, category=category, product_id=product_id
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/{campaign_id}")
async def detail(campaign_id: str, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return await marketing.campaign_detail(user["store_id"], campaign_id)
    except CampaignError as exc:
        raise _http(exc)


@router.patch("/{campaign_id}")
async def patch(campaign_id: str, body: CampaignPatch, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return await marketing.update_campaign(
            user["store_id"], user["id"], campaign_id,
            name=body.name, audience=body.audience,
            products=[p.model_dump() for p in body.products] if body.products is not None else None,
            message_template=body.message_template,
        )
    except CampaignError as exc:
        raise _http(exc)


@router.post("/{campaign_id}/submit")
async def submit(campaign_id: str, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return await marketing.submit_for_review(user["store_id"], user["id"], campaign_id)
    except CampaignError as exc:
        raise _http(exc)


@router.post("/{campaign_id}/approve")
async def approve(campaign_id: str, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return await marketing.approve_campaign(user["store_id"], user["id"], campaign_id)
    except CampaignError as exc:
        raise _http(exc)


@router.post("/{campaign_id}/send")
async def send(campaign_id: str, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return await marketing.send_campaign(user["store_id"], user["id"], campaign_id)
    except CampaignError as exc:
        raise _http(exc)


@router.post("/{campaign_id}/cancel")
async def cancel(campaign_id: str, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return await marketing.cancel_campaign(user["store_id"], user["id"], campaign_id)
    except CampaignError as exc:
        raise _http(exc)


@router.get("/{campaign_id}/tracking")
async def tracking(campaign_id: str, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return await marketing.campaign_tracking(user["store_id"], campaign_id)
    except CampaignError as exc:
        raise _http(exc)
