from __future__ import annotations

import asyncio
from typing import Any, Optional

import asyncpg

from app.config import get_settings


class Database:
    def __init__(self) -> None:
        self._pool: Optional[asyncpg.Pool] = None

    def _url(self) -> str:
        # asyncpg parses the DSN itself and expects the percent-encoded
        # password intact — do not unquote/rebuild the URL.
        url = get_settings().database_url.strip()
        if not url:
            raise RuntimeError("DATABASE_URL is not configured")
        return url

    async def connect(self) -> None:
        if self._pool:
            return
        self._pool = await asyncpg.create_pool(
            self._url(),
            min_size=1,
            max_size=5,
            command_timeout=60,
        )

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None

    @property
    def pool(self) -> asyncpg.Pool:
        if not self._pool:
            raise RuntimeError("Database not connected")
        return self._pool

    async def fetch(self, query: str, *args: Any) -> list[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetch(query, *args)

    async def fetchrow(self, query: str, *args: Any) -> Optional[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(query, *args)

    async def fetchval(self, query: str, *args: Any) -> Any:
        async with self.pool.acquire() as conn:
            return await conn.fetchval(query, *args)

    async def execute(self, query: str, *args: Any) -> str:
        async with self.pool.acquire() as conn:
            return await conn.execute(query, *args)

    async def transaction(self):
        conn = await self.pool.acquire()
        try:
            async with conn.transaction():
                yield conn
        finally:
            self.pool.release(conn)


db = Database()


async def wait_for_db(retries: int = 10, delay: float = 1.0) -> None:
    last: Exception | None = None
    for _ in range(retries):
        try:
            await db.connect()
            await db.fetchval("select 1")
            return
        except Exception as exc:  # noqa: BLE001
            last = exc
            await asyncio.sleep(delay)
    raise RuntimeError(f"Database unavailable: {last}")
