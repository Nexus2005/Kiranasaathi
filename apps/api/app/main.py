from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.database import db, wait_for_db
from app.routers import (
    agent,
    alerts,
    auth,
    commerce,
    counter,
    customers,
    dashboard,
    demand,
    enrichment,
    festivals,
    inventory,
    marketing,
    meta,
    orders,
    payments,
    products,
    purchases,
    retail,
    sales,
    suppliers,
    external,
)

logger = logging.getLogger("kirana")
logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    try:
        await wait_for_db(retries=15, delay=1.0)
        logger.info("Database connected")
    except Exception as exc:  # noqa: BLE001
        logger.error("Database not available at startup: %s", exc)
        # Allow API to start for non-DB routes; DB routes will 503
    yield
    await db.close()


app = FastAPI(title="KiranaSaathi AI API", version="0.1.0", lifespan=lifespan)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins or ["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.get("/health")
async def health() -> dict:
    try:
        await db.fetchval("select 1")
        db_ok = True
    except Exception:  # noqa: BLE001
        db_ok = False
    return {"status": "ok", "database": "up" if db_ok else "down"}


@app.get("/health/counter")
async def health_counter() -> dict:
    """Unauthenticated liveness summary for orchestrators (§45) — no
    sensitive detail, no model paths. Authenticated readiness with provider
    names lives at GET /api/counter/health."""
    try:
        await db.fetchval("select 1")
        db_ok = True
    except Exception:  # noqa: BLE001
        db_ok = False
    return {
        "status": "ok" if db_ok else "degraded",
        "database": "up" if db_ok else "down",
        "counter": "module-present",
    }


app.include_router(auth.router, prefix="/api")
app.include_router(products.router, prefix="/api")
app.include_router(sales.router, prefix="/api")
app.include_router(customers.router, prefix="/api")
app.include_router(dashboard.router, prefix="/api")
app.include_router(meta.router, prefix="/api")
app.include_router(inventory.router, prefix="/api")
app.include_router(purchases.router, prefix="/api")
app.include_router(suppliers.router, prefix="/api")
app.include_router(alerts.router, prefix="/api")
app.include_router(agent.router, prefix="/api")
app.include_router(demand.router, prefix="/api")
app.include_router(festivals.router, prefix="/api")
app.include_router(marketing.router, prefix="/api")
app.include_router(external.router, prefix="/api")
app.include_router(orders.router, prefix="/api")
app.include_router(payments.router, prefix="/api")
app.include_router(retail.router, prefix="/api")
app.include_router(counter.router, prefix="/api")
app.include_router(enrichment.router, prefix="/api")
app.include_router(commerce.router, prefix="/api")
app.include_router(commerce.public)  # public storefront: no /api prefix, no auth

# Merchant product images (uploaded via /api/products/{id}/image)
_MEDIA_ROOT = Path(__file__).resolve().parents[1] / "media"
_MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=str(_MEDIA_ROOT)), name="media")
