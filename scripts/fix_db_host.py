"""Switch DATABASE_URL in .env to the Supabase session pooler (IPv4-reachable).

Fixes: db.<ref>.supabase.co resolves only to IPv6, which fails with
`getaddrinfo failed` on IPv4-only networks. The session pooler
(aws-0-<region>.pooler.supabase.com:5432) is IPv4-reachable and is
Supabase's recommended workaround for the deprecated direct IPv4.

Never prints secrets — only redacted host/user info.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import urlparse, urlunparse

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"

PROJECT_REF = "aulgiuinmlbikwyrrjur"
REGION = "ap-southeast-1"
POOLER_HOST = f"aws-0-{REGION}.pooler.supabase.com"
DIRECT_HOST = f"db.{PROJECT_REF}.supabase.co"


def redact(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://***@{p.hostname}:{p.port}{p.path}"


def main() -> int:
    if not ENV_FILE.exists():
        print("ERROR: .env not found")
        return 1

    lines = ENV_FILE.read_text(encoding="utf-8").splitlines(keepends=True)
    changed = False
    for i, line in enumerate(lines):
        m = re.match(r"^DATABASE_URL=(.*)\s*$", line)
        if not m:
            continue
        url = m.group(1).strip().strip('"').strip("'")
        p = urlparse(url)

        if p.hostname == POOLER_HOST:
            print(f"Already on pooler: {redact(url)}")
            return 0

        is_pooler = p.hostname and ".pooler.supabase.com" in p.hostname
        if p.hostname != DIRECT_HOST and not is_pooler:
            print(f"Unexpected host, leaving unchanged: {redact(url)}")
            return 1

        pooler_url = urlunparse(
            p._replace(
                netloc=f"postgres.{PROJECT_REF}:{p.password}@{POOLER_HOST}:{p.port or 5432}",
                path="/postgres",
            )
        )
        lines[i] = f"DATABASE_URL={pooler_url}\n"
        changed = True
        print(f"Rewrote DATABASE_URL: {redact(url)} -> postgres.{PROJECT_REF}***@{POOLER_HOST}:{p.port or 5432}/postgres")
        break

    if not changed:
        print("ERROR: DATABASE_URL line not found")
        return 1

    ENV_FILE.write_text("".join(lines), encoding="utf-8")
    print("OK: .env updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
