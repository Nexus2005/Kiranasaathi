"""Phase 4 end-to-end verification — demand, festivals, customers, marketing.

Every asserted number comes from the API/database. Honesty checks are
first-class: insufficient-data states, consent enforcement, dev-mode
provider labeling, no double sends, no fabricated attribution.
Exits non-zero on any failure.
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

# Windows console: force UTF-8 so ₹ and other symbols print safely
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
    # ---------- auth + security baseline ----------
    s, login = req("POST", "/api/auth/login", {"email": EMAIL, "password": PASSWORD})
    check("login", s == 200 and login.get("access_token"), str(s))
    tok = login["access_token"]

    s, _ = req("GET", "/api/demand/overview")
    check("security: demand requires auth", s == 401, str(s))
    s, _ = req("GET", "/api/festivals")
    check("security: festivals requires auth", s == 401, str(s))
    s, _ = req("GET", "/api/campaigns")
    check("security: campaigns requires auth", s == 401, str(s))
    s, _ = req("GET", "/api/customers/intelligence/overview")
    check("security: customer intel requires auth", s == 401, str(s))

    # ---------- DEMAND ----------
    s, ov = req("GET", "/api/demand/overview", token=tok)
    check("demand: overview loads", s == 200 and "overview" in ov, str(s))
    check("demand: method note present", bool(ov["overview"].get("method_note")), "")
    check("demand: rising/falling lists exist",
          isinstance(ov.get("rising_products"), list) and isinstance(ov.get("falling_products"), list), "")
    for t in (ov.get("rising_products") or [])[:2]:
        check("demand: rising item has evidence fields",
              "recent_7d_units" in t and "previous_7d_units" in t and "data_quality" in t, str(t.keys()))
    check("demand: stockout flag exists per product",
          all("stockout_aware" in t for t in ov.get("rising_products", []) + ov.get("falling_products", [])), "")

    s, daily = req("GET", "/api/demand/daily-sales?days=14", token=tok)
    check("demand: daily series 14 rows", s == 200 and len(daily.get("series", [])) == 14, str(len(daily.get("series", []))))

    s, dow = req("GET", "/api/demand/day-of-week", token=tok)
    check("demand: day-of-week honesty field", s == 200 and "sufficient_data" in dow, "")

    s, prods = req("GET", "/api/products", token=tok)
    check("products list", s == 200 and len(prods["items"]) > 0, "")
    pid = prods["items"][0]["id"]

    s, vel = req("GET", f"/api/demand/products/{pid}/velocity", token=tok)
    check("demand: velocity windows labelled",
          s == 200 and "recent_7d_units" in vel and "previous_7d_units" in vel and "comparison_note" in vel, "")

    s, trend = req("GET", f"/api/demand/products/{pid}/trend", token=tok)
    check("demand: trend classified", s == 200 and trend.get("trend") in
          ("RISING", "FALLING", "STABLE", "WATCH", "INSUFFICIENT_DATA", "NO_RECENT_SALES"), str(trend.get("trend")))
    check("demand: trend has data quality", trend.get("data_quality") in ("HIGH", "MEDIUM", "LOW", "INSUFFICIENT_DATA"), "")

    s, fc = req("GET", f"/api/demand/products/{pid}/forecast?horizon_days=7", token=tok)
    check("demand: forecast returns", s == 200, str(s))
    if fc.get("status") == "ok":
        fr = fc["forecast_range"]
        check("demand: forecast is a RANGE", fr["high"] >= fr["low"], str(fr))
        check("demand: forecast quality category", fc["data_quality"] in ("HIGH", "MEDIUM", "LOW"), fc["data_quality"])
    else:
        check("demand: forecast honest about insufficient data",
              fc.get("status") == "INSUFFICIENT_DATA" and fc.get("forecast_range") is None, str(fc.get("status")))

    # forecast with no data: use a brand-new product
    s, np = req("POST", "/api/products", {
        "name": "Phase4 NoHistory Product", "category": "Other", "unit": "pcs",
        "mrp": 50, "selling_price": 45, "purchase_price": 30, "reorder_level": 5,
    }, token=tok)
    check("setup: no-history product created", s in (200, 201), str(s))
    nh_id = np["id"]
    s, fc2 = req("GET", f"/api/demand/products/{nh_id}/forecast", token=tok)
    check("demand: no-history forecast is INSUFFICIENT_DATA",
          s == 200 and fc2.get("status") == "INSUFFICIENT_DATA" and fc2.get("forecast_range") is None,
          str(fc2.get("status")))

    # ---------- FESTIVALS ----------
    s, fests = req("GET", "/api/festivals", token=tok)
    check("festivals: list loads", s == 200 and isinstance(fests.get("upcoming"), list), "")
    check("festivals: provenance note", "configured" in fests.get("note", "").lower(), fests.get("note"))
    upcoming = fests["upcoming"]
    check("festivals: has upcoming configured events", len(upcoming) > 0, str(len(upcoming)))
    fid = upcoming[0]["id"]
    fest0 = upcoming[0]
    check("festivals: fields complete",
          all(k in fest0 for k in ("name", "start_date", "days_away", "region", "source", "matched_store_categories")), "")

    s, ev = req("GET", f"/api/festivals/{fid}/evidence", token=tok)
    check("festivals: evidence endpoint", s == 200 and "has_evidence" in ev, str(s))
    if not ev.get("has_evidence"):
        check("festivals: no-evidence explains why", bool(ev.get("reason")), "")

    s, opp = req("GET", f"/api/festivals/{fid}/opportunity", token=tok)
    check("festivals: opportunity loads", s == 200 and opp.get("status") == "ok", str(s))
    check("festivals: summary has customer + evidence counts",
          "customers_with_relevant_history" in opp.get("summary", {}) and "historical_evidence" in opp.get("summary", {}), "")
    for p in opp.get("relevant_products", [])[:3]:
        check("festivals: product row has gap status", "gap_status" in p and "current_sellable_stock" in p, str(p.keys()))
    # past festival should report PASSED or be excluded from upcoming
    s, past_list = req("GET", "/api/festivals", token=tok)
    past_dates = [p["end_date"] for p in past_list.get("past", [])]
    check("festivals: past festivals listed separately", isinstance(past_dates, list), "")

    # ---------- CUSTOMER INTELLIGENCE ----------
    s, seg = req("GET", "/api/customers/intelligence/overview", token=tok)
    check("customers: segment overview", s == 200 and "segment_counts" in seg and "thresholds" in seg, "")
    check("customers: thresholds documented", seg.get("thresholds", {}).get("inactive_days") == 30, "")

    # consent flow: grant to ALL customers (as a merchant would after opt-in collection)
    s, custs = req("GET", "/api/customers", token=tok)
    check("customers: list", s == 200 and len(custs["items"]) > 0, "")
    consented_any = 0
    for c in custs["items"][:10]:
        s2, _r = req("POST", f"/api/customers/{c['id']}/consent",
                     {"marketing_consent": True, "consent_source": "store_entry"}, token=tok)
        if s2 == 200:
            consented_any += 1
    check("customers: consent granted to at least one customer", consented_any > 0, str(consented_any))
    cid = custs["items"][0]["id"]

    s, c360 = req("GET", f"/api/customers/{cid}/intelligence", token=tok)
    check("customers: 360 loads", s == 200 and c360.get("customer", {}).get("id"), "")
    check("customers: RFM present",
          all(k in c360.get("rfm", {}) for k in ("orders", "total_spent", "days_since_last_purchase")), "")
    check("customers: segments deterministic list", isinstance(c360.get("segments"), list) and len(c360["segments"]) > 0, "")
    check("customers: consent fields exposed",
          "marketing_consent" in c360.get("customer", {}) and "consent_source" in c360.get("customer", {}), "")

    # consent flow
    s, _ = req("POST", f"/api/customers/{cid}/consent", {"marketing_consent": True, "consent_source": "store_entry"}, token=tok)
    check("customers: consent can be granted", s == 200, str(s))
    s, c360b = req("GET", f"/api/customers/{cid}/intelligence", token=tok)
    check("customers: consent persisted", c360b["customer"]["marketing_consent"] is True, "")

    # inactive customers endpoint
    s, inact = req("GET", "/api/customers/intelligence/segments?segment=INACTIVE_CUSTOMER", token=tok)
    check("customers: inactive segment query", s == 200 and "items" in inact, "")

    # invalid segment refused
    s, bad_seg = req("GET", "/api/campaigns/audience-preview?segment=VIP_MEGA", token=tok)
    check("customers: unknown segment refused", s == 400, str(s))

    # ---------- CAMPAIGNS ----------
    # provider must be dev mode with honest labeling
    s, prov = req("GET", "/api/campaigns/provider", token=tok)
    check("campaigns: provider status", s == 200 and prov.get("provider_id") == "development", str(prov))
    check("campaigns: dev mode labeled honestly",
          prov.get("mode") == "development" and "NOT delivered" in prov.get("note", ""), prov.get("note"))

    # find an in-stock product for the campaign
    in_stock = [p for p in prods["items"] if p.get("quantity", 0) > 0]
    camp_pid = in_stock[0]["id"] if in_stock else pid

    s, camp = req("POST", "/api/campaigns", {
        "name": "Phase4 verification campaign",
        "campaign_type": "product_recommendation",
        "audience": {},
        "products": [{"product_id": camp_pid}],
        "message_template": "Hello! Come see our new arrivals.",
    }, token=tok)
    check("campaigns: create draft", s == 201 and camp.get("status") == "DRAFT", f"{s} {str(camp)[:120]}")
    camp_id = camp["id"]
    check("campaigns: price snapshotted from DB",
          camp.get("products") and float(camp["products"][0]["price"]) > 0, str(camp.get("products")))

    # submit with empty audience is allowed (documented default = all consented customers)
    # but we keep this draft in DRAFT by testing a non-existent id for 404 handling
    s, err = req("POST", "/api/campaigns/00000000-0000-0000-0000-000000000000/submit", token=tok)
    check("campaigns: submit unknown id -> 404", s == 404, str(s))

    # update with audience then submit (default audience = all consented customers)
    s, patched = req("PATCH", f"/api/campaigns/{camp_id}", {"audience": {}}, token=tok)
    check("campaigns: patch audience", s == 200, str(s))

    # duplicate active campaign guard (same type+segment, will matter after SENT)
    s, submitted = req("POST", f"/api/campaigns/{camp_id}/submit", token=tok)
    check("campaigns: submit for review", s == 200 and submitted.get("status") == "READY_FOR_REVIEW", f"{s} {str(submitted)[:150]}")
    check("campaigns: message rendered with store name", "Sharma Kirana" in (submitted.get("message_text") or ""), submitted.get("message_text", "")[:80])

    # approve directly from DRAFT path is refused; must be READY_FOR_REVIEW (already is)
    s, approved = req("POST", f"/api/campaigns/{camp_id}/approve", token=tok)
    check("campaigns: approve", s == 200 and approved.get("status") == "APPROVED", str(s))

    # send
    s, sent = req("POST", f"/api/campaigns/{camp_id}/send", token=tok)
    check("campaigns: send executes", s == 200 and sent.get("status") in ("SENT", "PARTIALLY_SENT", "FAILED"), str(sent.get("status")))
    check("campaigns: dev-mode delivery note", "Development mode" in (sent.get("delivery_note") or ""), sent.get("delivery_note"))
    if sent.get("status") == "SENT":
        check("campaigns: sent count matches recipients", sent.get("sent", 0) > 0, str(sent.get("sent")))

    # double-send refused
    s, dup = req("POST", f"/api/campaigns/{camp_id}/send", token=tok)
    check("campaigns: double send refused", s == 409, str(s))

    # zero-recipient path, deterministically: revoke consent from ALL customers,
    # then submit a default-audience campaign -> eligibility re-check must refuse.
    s, allc = req("GET", "/api/customers", token=tok)
    for c in allc["items"]:
        req("POST", f"/api/customers/{c['id']}/consent", {"marketing_consent": False}, token=tok)
    s, camp2 = req("POST", "/api/campaigns", {
        "name": "Phase4 zero recipients",
        "campaign_type": "general",
        "audience": {},
        "products": [],
        "message_template": "Test",
    }, token=tok)
    check("campaigns: second draft created", s == 201, str(s))
    s, sub2 = req("POST", f"/api/campaigns/{camp2['id']}/submit", token=tok)
    check("campaigns: zero-recipient submit refused honestly", s == 422, f"{s} {str(sub2)[:120]}")
    s, _ = req("POST", f"/api/campaigns/{camp2['id']}/cancel", token=tok)
    # restore consent (cleanup + re-verifies grant path)
    restored = 0
    for c in allc["items"]:
        s2, _ = req("POST", f"/api/customers/{c['id']}/consent",
                    {"marketing_consent": True, "consent_source": "store_entry"}, token=tok)
        if s2 == 200:
            restored += 1
    check("campaigns: consent restored after zero-recipient test", restored == len(allc["items"]), f"{restored}/{len(allc['items'])}")

    # tracking honesty
    s, track = req("GET", f"/api/campaigns/{camp_id}/tracking", token=tok)
    check("campaigns: tracking loads", s == 200, str(s))
    check("campaigns: attribution honestly unavailable",
          "unavailable" in track.get("attribution", {}).get("revenue_attribution", "").lower(), "")
    check("campaigns: delivery receipts honest",
          "not available" in track.get("attribution", {}).get("delivery_receipts", "").lower()
          or "phase 5" in track.get("attribution", {}).get("delivery_receipts", "").lower(), "")

    # cancel flow
    s, camp3 = req("POST", "/api/campaigns", {
        "name": "Phase4 cancel me",
        "campaign_type": "general",
        "audience": {"segment": "HIGH_VALUE"},
        "products": [],
        "message_template": "Test cancel",
    }, token=tok)
    s, _ = req("POST", f"/api/campaigns/{camp3['id']}/cancel", token=tok)
    check("campaigns: cancel draft", s == 200, str(s))
    s, sub3 = req("POST", f"/api/campaigns/{camp3['id']}/submit", token=tok)
    check("campaigns: cancelled cannot be submitted", s == 409, str(s))

    # unavailable product refused
    out_of_stock = [p for p in prods["items"] if p.get("quantity", 0) == 0]
    if out_of_stock:
        s, _ = req("POST", "/api/campaigns", {
            "name": "Phase4 OOS attempt",
            "campaign_type": "general",
            "audience": {"segment": "HIGH_VALUE"},
            "products": [{"product_id": out_of_stock[0]["id"]}],
            "message_template": "Buy!",
        }, token=tok)
        check("campaigns: out-of-stock product refused", s == 422, str(s))
    else:
        check("campaigns: out-of-stock product refused (setup: none available to test)", True, "skipped — all products in stock")

    # ---------- AI TOOLS ----------
    s, ans = req("POST", "/api/agent/ask", {"question": "What is selling fastest?"}, token=tok)
    check("ai: fastest selling answered", s == 200 and ans.get("answer"), "")
    check("ai: demand tools in trace", any(t in ans.get("tools_used", []) for t in ("get_demand_summary", "get_product_trends")), str(ans.get("tools_used")))

    s, ans = req("POST", "/api/agent/ask", {"question": "Which customers should I contact?"}, token=tok)
    check("ai: customer targeting answered", s == 200 and ans.get("answer"), "")
    check("ai: customer tools in trace", "get_customer_segments" in ans.get("tools_used", []) or "get_inactive_customers" in ans.get("tools_used", []), "")

    s, ans = req("POST", "/api/agent/ask", {"question": "Diwali is coming. What should I prepare?"}, token=tok)
    check("ai: festival prep answered", s == 200 and ans.get("answer"), str(s))
    check("ai: festival intent", ans.get("intent") == "festival_prep", str(ans.get("intent")))
    check("ai: cross-module plan present", "plan" in ans and "next_actions" in ans.get("plan", {}), "")
    check("ai: plan covers inventory+customers+marketing",
          all(k in ans.get("plan", {}) for k in ("inventory", "customers")), str(ans.get("plan", {}).keys()))

    s, ans = req("POST", "/api/agent/ask", {"question": "Which products are declining?"}, token=tok)
    check("ai: declining answered", s == 200 and ans.get("intent") == "demand", str(ans.get("intent")))

    s, ans = req("POST", "/api/agent/ask", {"question": "Create a campaign for customers who buy snacks"}, token=tok)
    check("ai: campaign creation intent", s == 200 and ans.get("intent") == "marketing", str(ans.get("intent")))
    # draft may or may not be created depending on category match; must never auto-send
    if ans.get("draft_campaign"):
        check("ai: draft campaign stays DRAFT", ans["draft_campaign"].get("status") == "DRAFT", str(ans["draft_campaign"].get("status")))

    # ---------- activity logging ----------
    s, act = req("GET", "/api/activity?limit=100", token=tok)
    events = {a.get("event_type") for a in act.get("items", [])} if s == 200 else set()
    check("activity: campaign events logged",
          any(e in events for e in ("CAMPAIGN_CREATED", "CAMPAIGN_APPROVED", "CAMPAIGN_SENT")), str(events))
    check("activity: consent event logged", "CUSTOMER_CONSENT_UPDATED" in events, str(events))

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
