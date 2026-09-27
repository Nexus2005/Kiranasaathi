"""Agent Reach — the ONLY doorway from the outside world into KiranaSaathi.

Rules (Phase 5 contract):
- Runs server-side only. The browser never calls external sites through us
  beyond this controlled, rate-limited, allow-listed client.
- Source clients are pluggable (SourceClient interface). Phase 5 ships:
  * ManualSourceClient  — merchant/curated notes (no network)
  * RssSourceClient     — standard RSS/Atom feeds (opt-in per source)
- Every fetch produces CANDIDATE evidence: raw payload + provenance
  (source, url, published_at, retrieved_at, region, category). Nothing here
  is ever treated as merchant truth, and nothing auto-enters recommendations.
- Verification happens in services/evidence.py, not here.
- Robots/fetch discipline: single fetch per source per refresh call, timeout,
  size cap, no retries storm, no JS execution, no cookies.
"""

from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Optional
from urllib import robotparser
from urllib.parse import urlparse

import httpx

from app.database import db

MAX_BYTES = 512 * 1024          # hard response size cap
FETCH_TIMEOUT = 12.0            # seconds
USER_AGENT = "KiranaSaathiAI-AgentReach/0.1 (+evidence collection; contact: store owner)"


class ReachError(Exception):
    pass


class Candidate(dict):
    """A candidate evidence record (not yet verified)."""

    pass


class SourceClient(ABC):
    kind: str = "abstract"

    @abstractmethod
    async def fetch(self, source: dict[str, Any]) -> list[Candidate]:
        """Return candidates for one configured source. Raises ReachError on failure."""


# ---------------------------------------------------------------- manual


class ManualSourceClient(SourceClient):
    """Merchant/curated observations entered in-app. No network. Highest trust
    potential (verifiable by the merchant personally)."""

    kind = "manual"

    async def fetch(self, source: dict[str, Any]) -> list[Candidate]:
        # Manual evidence is created via the API, not fetched. Return empty.
        return []


# ---------------------------------------------------------------- rss


_TAG = lambda e: e.tag.rsplit("}", 1)[-1].lower()  # noqa: E731


def _text(el: Optional[ET.Element]) -> str:
    return (el.text or "").strip() if el is not None else ""


class RssSourceClient(SourceClient):
    """Conservative RSS/Atom reader: titles + links + dates only.

    A feed item becomes AT MOST one candidate. HTML is stripped from
    descriptions; nothing is inferred. If robots.txt disallows the host,
    the fetch is refused — we do not scrape against site policy.
    """

    kind = "rss"

    async def fetch(self, source: dict[str, Any]) -> list[Candidate]:
        base_url = source["base_url"]
        feed_url = self._feed_url(base_url)
        await self._check_robots(feed_url)

        try:
            async with httpx.AsyncClient(
                timeout=FETCH_TIMEOUT, follow_redirects=True,
                headers={"User-Agent": USER_AGENT}, verify=True,
            ) as client:
                resp = await client.get(feed_url)
                resp.raise_for_status()
                if len(resp.content) > MAX_BYTES:
                    raise ReachError(f"Response too large from {feed_url}")
                body = resp.content
        except httpx.HTTPError as exc:
            raise ReachError(f"Fetch failed for {feed_url}: {exc}") from exc

        return self._parse_feed(body, source, feed_url)

    @staticmethod
    def _feed_url(base_url: str) -> str:
        u = urlparse(base_url)
        if not u.scheme:
            raise ReachError(f"Source base_url needs a scheme: {base_url}")
        return base_url

    @staticmethod
    async def _check_robots(feed_url: str) -> None:
        parsed = urlparse(feed_url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        try:
            async with httpx.AsyncClient(
                timeout=FETCH_TIMEOUT, follow_redirects=True,
                headers={"User-Agent": USER_AGENT},
            ) as client:
                resp = await client.get(robots_url)
            if resp.status_code >= 400:
                return  # no robots.txt reachable: proceed once, politely
            body = resp.text[:MAX_BYTES]
        except httpx.HTTPError:
            return
        rp = robotparser.RobotFileParser()
        rp.parse(body.splitlines())
        if not rp.can_fetch(USER_AGENT, feed_url):
            raise ReachError(f"robots.txt disallows fetching {feed_url}")

    def _parse_feed(self, body: bytes, source: dict[str, Any], feed_url: str) -> list[Candidate]:
        try:
            root = ET.fromstring(body)
        except ET.ParseError as exc:
            raise ReachError(f"Unparseable feed {feed_url}: {exc}") from exc

        items: list[ET.Element] = []
        if root.tag.rsplit("}", 1)[-1].lower() == "rss":
            items = list(root.iter("item"))[:25]
        else:  # atom
            items = [e for e in root.iter() if _TAG(e) == "entry"][:25]

        now = datetime.now(timezone.utc)
        candidates: list[Candidate] = []
        for item in items:
            title = _text(next((e for e in item if _TAG(e) == "title"), None))
            if not title:
                continue
            link_el = next((e for e in item if _TAG(e) == "link"), None)
            link = _text(link_el) or (link_el.get("href") if link_el is not None and link_el.get("href") else None)
            pub = _text(next((e for e in item if _TAG(e) in ("pubdate", "published", "updated", "date")), None)) or None
            desc_el = next((e for e in item if _TAG(e) in ("description", "summary", "content")), None)
            desc = re.sub(r"<[^>]+>", " ", _text(desc_el))[:400] if desc_el is not None else None
            candidates.append(Candidate({
                "source_kind": "rss",
                "source_name": source["name"],
                "source_url": link or feed_url,
                "title": title[:300],
                "summary": desc,
                "raw_payload": {"feed": feed_url, "title": title[:300], "link": link},
                "published_at": self._parse_date(pub) if pub else None,
                "retrieved_at": now,
                "region": source.get("region"),
                "category": None,
            }))
        return candidates

    @staticmethod
    def _parse_date(value: str) -> Optional[datetime]:
        fmts = ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d")
        for f in fmts:
            try:
                dt = datetime.strptime(value.strip(), f)
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return None


REGISTRY: dict[str, SourceClient] = {
    "manual": ManualSourceClient(),
    "rss": RssSourceClient(),
}


def get_client(kind: str) -> SourceClient:
    if kind not in REGISTRY:
        raise ReachError(f"No source client for kind '{kind}'")
    return REGISTRY[kind]


# ---------------------------------------------------------------- dedup + persist


def dedup_hash(cand: Candidate) -> str:
    basis = f"{cand['source_name']}|{cand['title']}|{cand.get('source_url') or ''}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


async def refresh_source(store_id: str, source_row: Any) -> dict[str, Any]:
    """Fetch ONE configured source and store candidates as UNVERIFIED evidence."""
    client = get_client(source_row["kind"])
    try:
        candidates = await client.fetch(dict(source_row))
        error = None
    except ReachError as exc:
        candidates, error = [], str(exc)

    inserted = 0
    for cand in candidates:
        try:
            await db.execute(
                """
                insert into evidence_items
                  (store_id, source_id, source_kind, source_name, source_url, title,
                   summary, raw_payload, published_at, retrieved_at, region, category,
                   verification_status, trust_tier, dedup_hash)
                values ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10, $11, $12,
                        'UNVERIFIED', $13, $14)
                on conflict (store_id, dedup_hash) do nothing
                """,
                store_id, source_row["id"], cand["source_kind"], cand["source_name"],
                cand.get("source_url"), cand["title"], cand.get("summary"),
                json.dumps(dict(cand)), cand.get("published_at"),
                cand.get("retrieved_at") or datetime.now(timezone.utc),
                cand.get("region"), cand.get("category"),
                source_row["trust_tier"], dedup_hash(cand),
            )
            inserted += 1
        except Exception:  # noqa: BLE001 — one bad item must not kill the batch
            continue

    await db.execute(
        "update external_sources set last_fetched_at = now() where id = $1",
        source_row["id"],
    )
    return {
        "source_key": source_row["source_key"],
        "kind": source_row["kind"],
        "candidates": len(candidates),
        "stored": inserted,
        "error": error,
    }
