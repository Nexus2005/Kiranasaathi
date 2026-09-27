from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.security import get_current_user
from app.services import demand

router = APIRouter(prefix="/demand", tags=["demand"])


@router.get("/overview")
async def overview(user: dict = Depends(get_current_user)) -> dict[str, Any]:
    """Dashboard payload: KPIs, rising/falling products, category trends."""
    try:
        return await demand.demand_overview(user["store_id"])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"Demand analysis failed: {exc}")


@router.get("/summary")
async def summary(user: dict = Depends(get_current_user)) -> dict[str, Any]:
    return await demand.demand_summary(user["store_id"])


@router.get("/daily-sales")
async def daily_sales(days: int = 30, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    days = max(1, min(days, 120))
    series = await demand.store_daily_sales(user["store_id"], days)
    return {"days": days, "series": series}


@router.get("/day-of-week")
async def day_of_week(user: dict = Depends(get_current_user)) -> dict[str, Any]:
    return await demand.day_of_week_pattern(user["store_id"])


@router.get("/products/{product_id}/trend")
async def product_trend(product_id: str, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return await demand.product_trend(user["store_id"], product_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.get("/products/{product_id}/velocity")
async def product_velocity(product_id: str, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    return await demand.sales_velocity(user["store_id"], product_id)


@router.get("/products/{product_id}/forecast")
async def product_forecast(
    product_id: str, horizon_days: int = 7, user: dict = Depends(get_current_user)
) -> dict[str, Any]:
    try:
        return await demand.forecast_product_demand(user["store_id"], product_id, horizon_days)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


class ForecastManyIn(BaseModel):
    product_ids: list[str] = Field(min_length=1, max_length=25)
    horizon_days: int = Field(default=7, ge=1, le=30)


@router.post("/forecasts")
async def forecast_many(body: ForecastManyIn, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    """Forecasts for the selected products (dashboard forecast panel)."""
    out = []
    for pid in body.product_ids[:25]:
        try:
            out.append(await demand.forecast_product_demand(user["store_id"], pid, body.horizon_days))
        except ValueError:
            continue
    return {"items": out, "horizon_days": body.horizon_days}
