from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field

from app.database import db
from app.security import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=2, max_length=120)
    store_name: str = Field(min_length=2, max_length=120)
    location: Optional[str] = None


class LoginIn(BaseModel):
    email: EmailStr
    password: str


def _user_payload(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "email": row["email"],
        "full_name": row["full_name"],
        "merchant_id": row["merchant_id"],
        "merchant_name": row["merchant_name"],
        "store_id": row["store_id"],
        "store_name": row["store_name"],
        "store_location": row["store_location"],
        "currency": row["currency"],
    }


async def _load_user(user_id: str) -> dict[str, Any]:
    row = await db.fetchrow(
        """
        select u.id, u.email, u.full_name,
               m.id as merchant_id, m.name as merchant_name,
               s.id as store_id, s.name as store_name, s.location as store_location, s.currency
        from users u
        left join merchants m on m.user_id = u.id
        left join stores s on s.merchant_id = m.id
        where u.id = $1
        order by s.created_at
        limit 1
        """,
        user_id,
    )
    if not row:
        raise HTTPException(404, "User not found")
    return dict(row)


@router.post("/register")
async def register(body: RegisterIn) -> dict[str, Any]:
    existing = await db.fetchval("select 1 from users where email=$1", body.email.lower())
    if existing:
        raise HTTPException(400, "Email already registered")

    password_hash = hash_password(body.password)
    async with db.pool.acquire() as conn:
        async with conn.transaction():
            user = await conn.fetchrow(
                """
                insert into users (email, password_hash, full_name)
                values ($1, $2, $3)
                returning id, email, full_name
                """,
                body.email.lower(),
                password_hash,
                body.full_name.strip(),
            )
            merchant = await conn.fetchrow(
                "insert into merchants (user_id, name) values ($1, $2) returning id, name",
                user["id"],
                body.full_name.strip(),
            )
            store = await conn.fetchrow(
                """
                insert into stores (merchant_id, name, location)
                values ($1, $2, $3)
                returning id, name, location, currency
                """,
                merchant["id"],
                body.store_name.strip(),
                body.location.strip() if body.location else None,
            )
            await conn.execute(
                """
                insert into activity_logs (store_id, user_id, event_type, entity_type, message)
                values ($1, $2, 'MERCHANT_REGISTERED', 'store', $3)
                """,
                store["id"],
                user["id"],
                f"Store {store['name']} created",
            )

    token = create_access_token(user["id"], user["email"])
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user["id"],
            "email": user["email"],
            "full_name": user["full_name"],
            "merchant_id": merchant["id"],
            "merchant_name": merchant["name"],
            "store_id": store["id"],
            "store_name": store["name"],
            "store_location": store["location"],
            "currency": store["currency"],
        },
    }


@router.post("/login")
async def login(body: LoginIn) -> dict[str, Any]:
    row = await db.fetchrow(
        "select id, email, password_hash, full_name from users where email=$1",
        body.email.lower(),
    )
    if not row or not verify_password(body.password, row["password_hash"]):
        raise HTTPException(401, "Invalid email or password")

    user = await _load_user(row["id"])
    if not user.get("store_id"):
        raise HTTPException(400, "Account has no store")
    token = create_access_token(user["id"], user["email"])
    return {"access_token": token, "token_type": "bearer", "user": _user_payload(user)}


@router.get("/me")
async def me(user: dict = Depends(get_current_user)) -> dict[str, Any]:
    return {"user": _user_payload(user)}
