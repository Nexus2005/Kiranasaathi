"""Phase 4 integration scenario (spec section 51): festival -> demand ->
inventory -> reorder engine -> supplier -> customers -> campaign -> AI plan
-> merchant approval -> validated execution -> audit -> explanation.

Runs against a live API. Exits non-zero on failure.
"""
from __future__ import annotations

import io
import json
import os
import sys
import urllib.error
import urllib.request

# Bypass any system proxy (Windows registry proxy breaks localhost calls)
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

API = os.environ.get("API_URL", "http://127.0.0.1:8001")
EMAIL = "ramesh@kirana.demo"
PASSWORD = "Demo@12345"

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if cond:
        PASS += 1
    else:
        FAIL += 1


def req(method: str, path: str, body=None, token=None):
    url = f"{API}{path}"
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    try:
        with OPENER.open(r, timeout=60) as res:
            return res.status, json.loads(res.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}


def main() -> int:
    s, login = req("POST", "/api/auth/login", {"email": EMAIL, "password": PASSWORD})
    tok = login["access_token"]
    check("login", s == 200 and tok, str(s))

    # 1-2. Merchant has history; festival is approaching
    s, fests = req("GET", "/api/festivals?horizon_days=120", token=tok) if False else req("GET", "/api/festivals", token=tok)
    upcoming = [f for f in fests["upcoming"] if f["has_store_relevance"]]
    check("1-2. festival approaching with store relevance", len(upcoming) > 0, str(len(fests.get("upcoming", []))))
    fest = upcoming[0]

    # 3. Demand engine identifies relevant categories
    s, opp = req("GET", f"/api/festivals/{fest['id']}/opportunity", token=tok)
    cats = {p["category"] for p in opp["relevant_products"]}
    check("3. relevant categories identified", s == 200 and len(cats) > 0, str(cats))

    # 3b. Historical evidence is honest (either present or explicitly absent)
    s, ev = req("GET", f"/api/festivals/{fest['id']}/evidence", token=tok)
    check("3b. evidence honest", s == 200 and ("has_evidence" in ev and (ev["has_evidence"] or ev.get("reason"))), "")

    # 4. Inventory engine: current stock per relevant product
    stocks = {p["name"]: p["current_sellable_stock"] for p in opp["relevant_products"]}
    check("4. current stock computed", len(stocks) > 0, "")

    # 5. Reorder engine identifies gap products (REUSED, not recalculated)
    s, inv_plan = req("GET", "/api/inventory/intelligence", token=tok)
    check("5. existing reorder engine reachable", s == 200 and "items" in inv_plan, "")

    # 6. Supplier engine: supplier options for relevant products
    sups = opp.get("supplier_options", [])
    check("6. supplier options available", isinstance(sups, list) and len(sups) > 0, str(len(sups)))

    # 7. Customer engine: eligible customers (consent recorded first)
    s, custs = req("GET", "/api/customers", token=tok)
    for c in custs["items"][:10]:
        req("POST", f"/api/customers/{c['id']}/consent",
            {"marketing_consent": True, "consent_source": "store_entry"}, token=tok)
    s, elig = req("GET", "/api/campaigns/audience-preview", token=tok)
    check("7. eligible customers computed", s == 200 and elig["count"] > 0, str(elig.get("count")))

    # 8. Marketing engine prepares campaign (DRAFT)
    gap_products = [p for p in opp["relevant_products"] if p.get("gap_status") == "GAP" and p.get("current_sellable_stock", 0) > 0]
    promo = (gap_products or opp["relevant_products"])[0]
    in_stock = [p for p in opp["relevant_products"] if p["current_sellable_stock"] > 0]
    target = in_stock[0] if in_stock else None
    if target:
        # find product id via products list
        s, prods = req("GET", "/api/products", token=tok)
        match = next((p for p in prods["items"] if p["name"] == target["name"]), None)
        check("8. campaign product resolved from catalog", match is not None, target["name"])
        s, camp = req("POST", "/api/campaigns", {
            "name": f"{fest['name']} festival campaign",
            "campaign_type": "festival",
            # Category audience when history supports it; otherwise the documented
            # default (all consented customers with a phone) — never fabricated evidence.
            "audience": {"category": target["category"]} if opp["summary"]["customers_with_relevant_history"] > 0 else {},
            "products": [{"product_id": match["id"], "offer_text": "Festival special"}],
            "message_template": f"Hello! {fest['name']} is here — festival specials await you.",
        }, token=tok)
        check("8. campaign created as DRAFT", s == 201 and camp["status"] == "DRAFT", str(s))

        # 9-10. AI combines all information into a structured plan
        s, ai = req("POST", "/api/agent/ask",
                    {"question": f"{fest['name']} is coming. What should I prepare?"}, token=tok)
        check("9-10. AI cross-module plan", s == 200 and ai.get("intent") == "festival_prep" and "plan" in ai, str(ai.get("intent")))
        plan_sections = ai["answer"]
        check("9-10. plan covers DEMAND/INVENTORY/CUSTOMERS/MARKETING",
              all(w in plan_sections for w in ("DEMAND", "INVENTORY", "CUSTOMERS", "MARKETING")), "")

        # 11. Merchant reviews -> 12. approves individual actions (submit/approve/send separately)
        s, submitted = req("POST", f"/api/campaigns/{camp['id']}/submit", token=tok)
        check("12. campaign submitted for review", s == 200 and submitted["status"] == "READY_FOR_REVIEW", str(s))
        s, approved = req("POST", f"/api/campaigns/{camp['id']}/approve", token=tok)
        check("12. campaign approved", s == 200 and approved["status"] == "APPROVED", str(s))

        # 13. Backend validates current state (eligibility re-checked at send)
        s, sent = req("POST", f"/api/campaigns/{camp['id']}/send", token=tok)
        check("13-14. send validated + executed", s == 200 and sent["status"] in ("SENT", "PARTIALLY_SENT"), str(sent.get("status")))
        check("13-14. dev mode honestly labeled", "Development mode" in (sent.get("delivery_note") or ""), "")

        # 15. All mutations logged
        s, act = req("GET", "/api/activity?limit=50", token=tok)
        events = [a.get("event_type") for a in act.get("items", [])]
        check("15. campaign audit trail", any(e in events for e in ("CAMPAIGN_APPROVED", "CAMPAIGN_SENT")), str(set(events)))

        # 16. Dashboard/panels reflect the state (tracking endpoint)
        s, track = req("GET", f"/api/campaigns/{camp['id']}/tracking", token=tok)
        check("16. tracking reflects sends", s == 200 and track["recipient_counts"].get("sent", 0) == sent.get("sent", 0),
              f"{track['recipient_counts']} vs {sent.get('sent')}")

        # 17. AI can later explain what happened
        s, ai2 = req("POST", "/api/agent/ask", {"question": "How did my campaign do?"}, token=tok)
        check("17. AI explains campaign state", s == 200 and ai2.get("intent") == "marketing", str(ai2.get("intent")))
    else:
        check("8. campaign product resolved from catalog", False, "no in-stock relevant product for scenario")

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
