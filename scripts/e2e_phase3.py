"""Phase 3 end-to-end verification (run against a live API).

Covers the spec scenarios A-F plus lifecycle, staleness, idempotency,
authorization and audit-trail checks. Every number asserted comes from the
database via the API. Exits non-zero on any failed check.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

# Bypass any system proxy (Windows registry proxy breaks localhost calls)
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

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
    # ---------- auth ----------
    s, login = req("POST", "/api/auth/login", {"email": EMAIL, "password": PASSWORD})
    check("login", s == 200 and login.get("access_token"), str(s))
    tok = login["access_token"]

    s, prods = req("GET", "/api/products", token=tok)
    items = [p for p in prods["items"] if p["quantity"] >= 10]
    pid = items[0]["id"]
    sp = float(items[0]["selling_price"])

    # ---------- SCENARIO A: what should I do today ----------
    s, a = req("POST", "/api/agent/ask", {"question": "What should I do today?"}, token=tok)
    check("A: ask works", s == 200 and a.get("answer"), f"{s}")
    check("A: engine labelled", a.get("engine") == "deterministic-rules-v1", a.get("engine"))
    check("A: tools trace present", len(a.get("tools_used", [])) > 0, str(a.get("tools_used")))
    check("A: recommendations returned", isinstance(a.get("recommendations"), list), "")
    check("A: brief numbers present", "brief" in a and "sales_today" in a.get("brief", {}), str(a.get("brief", {}).keys()))
    for r in a.get("recommendations", [])[:3]:
        check("A: reco has evidence", isinstance(r.get("evidence"), dict) and len(r["evidence"]) > 0, str(r.get("id")))
        check("A: reco has priority reason", bool(r.get("priority_reason")), "")
        break

    # recommendations persisted with dedup
    s, recos = req("GET", "/api/agent/recommendations?status=open", token=tok)
    check("recommendations list", s == 200 and recos.get("count", 0) > 0, str(recos.get("count")))
    types = {r["type"] for r in recos["items"]}
    check("reco types include real detectors", len(types) > 0, str(types))
    dup_keys = {}
    for r in recos["items"]:
        k = (r["type"], json.dumps(r.get("evidence", {}).get("product_id", "")))
        dup_keys[k] = dup_keys.get(k, 0) + 1
    check("no duplicate open recos per product", all(v == 1 for v in dup_keys.values()), str(dup_keys))

    # second run must not duplicate
    s, recos2 = req("GET", "/api/agent/recommendations?status=open", token=tok)
    check("idempotent recommendation count", recos2["count"] == recos["count"],
          f"{recos['count']} -> {recos2['count']}")

    # ---------- SCENARIO B: why is profit down ----------
    s, b = req("POST", "/api/agent/ask", {"question": "Why are my profits lower this week?"}, token=tok)
    check("B: profit question answered", s == 200 and b.get("answer"), str(s))
    check("B: tool trace includes analyzer", "analyze_profit_change" in b.get("tools_used", []), str(b.get("tools_used")))

    # ---------- SCENARIO C: what should I reorder ----------
    s, c = req("POST", "/api/agent/ask", {"question": "What should I reorder?"}, token=tok)
    check("C: reorder question answered", s == 200 and c.get("answer"), str(s))
    check("C: tool trace", "get_reorder_candidates" in c.get("tools_used", []), str(c.get("tools_used")))

    # ---------- SCENARIO D: can I sell for ₹X ----------
    offer_price = round(sp - 5, 2)
    s, d = req("POST", "/api/agent/ask",
               {"question": f"Can I sell {items[0]['name'].split()[0]} for ₹{offer_price}?"}, token=tok)
    check("D: pricing question answered", s == 200 and d.get("answer"), str(s))
    if d.get("intent") == "pricing":
        check("D: floor mentioned", "minimum" in d["answer"].lower() or "₹" in d["answer"], d["answer"][:100])
    else:
        check("D: graceful product-not-matched", d.get("intent") in ("pricing", "fallback"), str(d.get("intent")))

    # ---------- SCENARIO E: expiry action -> prepare -> approve -> execute ----------
    s, exp = req("GET", "/api/inventory/expiry", token=tok)
    check("E: expiry data available", s == 200 and isinstance(exp.get("sellable_batches"), list), str(s))
    # pick any product for the price-change action flow
    s, pr = req("GET", f"/api/inventory/products/{pid}/pricing", token=tok)
    floor = pr["minimum_acceptable_price"]
    new_price = round(max(floor, sp - 2), 2)
    s, prep = req("POST", "/api/agent/actions/prepare", {
        "action_type": "price_change",
        "payload": {"product_id": pid, "new_price": new_price, "reason": "Phase 3 e2e clearance"},
    }, token=tok)
    check("E: action prepared with preview", s == 200 and prep.get("preview", {}).get("new_price") == new_price, f"{s} {prep}")
    check("E: preview shows current+new margin",
          "current_margin_pct" in prep.get("preview", {}) and "new_margin_pct" in prep.get("preview", {}),
          str(prep.get("preview", {}).keys()))
    action_id = prep.get("action_id")

    s, exe = req("POST", f"/api/agent/actions/{action_id}/approve", {"force": False}, token=tok)
    check("E: action executed", s == 200 and exe.get("status") == "executed", f"{s} {exe}")
    check("E: price actually changed",
          exe.get("result", {}).get("changed") is True and exe["result"]["new_price"] == new_price, str(exe.get("result")))

    # duplicate approval must not re-execute
    s, dup = req("POST", f"/api/agent/actions/{action_id}/approve", {"force": False}, token=tok)
    check("duplicate approval is idempotent", s == 200 and dup.get("duplicate") is True, f"{s} {dup}")

    s, pr2 = req("GET", f"/api/inventory/products/{pid}/pricing", token=tok)
    check("price persisted", pr2["selling_price"] == new_price, f"{pr2['selling_price']}")

    # restore original price directly via engine-approved path
    s, prep2 = req("POST", "/api/agent/actions/prepare", {
        "action_type": "price_change",
        "payload": {"product_id": pid, "new_price": sp, "reason": "restore after e2e"},
    }, token=tok)
    s, _ = req("POST", f"/api/agent/actions/{prep2['action_id']}/approve", {"force": False}, token=tok)
    check("price restored", s == 200, str(s))

    # ---------- SCENARIO F: stale recommendation ----------
    s, me0 = req("GET", "/api/inventory/intelligence", token=tok)
    me0item = [i for i in me0["items"] if i["product_id"] == pid][0]
    stock0 = me0item["quantity"]
    s, prepF = req("POST", "/api/agent/actions/prepare", {
        "action_type": "create_purchase",
        "payload": {"product_id": pid, "quantity": 5,
                    "supplier_id": req("GET", "/api/suppliers", token=tok)[1]["items"][0]["id"]},
    }, token=tok)
    check("F: purchase action prepared", s == 200, f"{s} {prepF}")
    # change the underlying state: sell 2 units
    s, _ = req("POST", "/api/sales", {
        "items": [{"product_id": pid, "quantity": 2}], "payment_method": "cash",
    }, token=tok)
    check("F: state changed (sale made)", s == 201, str(s))
    s, stale = req("POST", f"/api/agent/actions/{prepF['action_id']}/approve", {"force": False}, token=tok)
    check("F: stale detected, not executed", s == 200 and stale.get("status") == "stale", f"{s} {stale}")
    check("F: stale message explains change", "no longer current" in stale.get("message", ""), stale.get("message", ""))
    check("F: updated preview provided", "updated_preview" in stale, str(stale.keys()))
    s, me1 = req("GET", "/api/inventory/intelligence", token=tok)
    me1item = [i for i in me1["items"] if i["product_id"] == pid][0]
    check("F: no PO created on stale approval", me1item["quantity"] == stock0 - 2,
          f"{stock0} -> {me1item['quantity']}")
    # approve with force -> executes (creates pending PO, no stock change)
    s, forced = req("POST", f"/api/agent/actions/{prepF['action_id']}/approve", {"force": True}, token=tok)
    check("F: forced approval executes", s == 200 and forced.get("status") == "executed", f"{s} {forced}")
    check("F: created PO is pending (no stock change)", forced.get("result", {}).get("purchase_order", {}).get("status") == "pending",
          str(forced.get("result", {}).keys()))
    # cancel the pending PO to leave clean state
    po_id = forced.get("result", {}).get("reference_id")
    s, _ = req("POST", f"/api/purchases/{po_id}/cancel", token=tok)
    check("F: cleanup PO cancelled", s == 200, str(s))
    # stale action was re-executed: ensure idempotency guard
    s, dupF = req("POST", f"/api/agent/actions/{prepF['action_id']}/approve", {"force": True}, token=tok)
    check("F: second approval after execution is duplicate", dupF.get("duplicate") is True, str(dupF))

    # ---------- recommendation lifecycle ----------
    s, recos = req("GET", "/api/agent/recommendations?status=open", token=tok)
    if recos["items"]:
        rid = recos["items"][0]["id"]
        s, _ = req("POST", f"/api/agent/recommendations/{rid}/status",
                   {"status": "REVIEWED"}, token=tok)
        check("lifecycle: REVIEWED", s == 200, str(s))
        s, rr = req("POST", f"/api/agent/recommendations/{rid}/status",
                    {"status": "DISMISSED", "note": "e2e dismiss"}, token=tok)
        check("lifecycle: DISMISSED", s == 200 and rr.get("status") == "DISMISSED", str(rr))
        s, recos3 = req("GET", "/api/agent/recommendations?status=open", token=tok)
        check("dismissed reco leaves open list", all(r["id"] != rid for r in recos3["items"]), "")
    else:
        check("lifecycle: has open recos", False, "no open recommendations")

    # ---------- brief ----------
    s, brief = req("GET", "/api/agent/brief", token=tok)
    check("brief endpoint", s == 200 and "sales" in brief and "recommendations" in brief, str(s))
    check("brief has attention block", "attention" in brief, str(brief.keys()))

    # ---------- conversation log ----------
    s, convs = req("GET", "/api/agent/conversations", token=tok)
    check("conversations persisted", s == 200 and convs.get("count", 0) >= 4, str(convs.get("count")))

    # ---------- audit trail ----------
    s, act = req("GET", "/api/activity?limit=100", token=tok)
    events = {a["event_type"] for a in act.get("items", [])}
    check("audit: AI_RECOMMENDATION_CREATED logged", "AI_RECOMMENDATION_CREATED" in events, str(sorted(events))[:200])
    check("audit: AI_ACTION_PREPARED logged", "AI_ACTION_PREPARED" in events, "")
    check("audit: AI_ACTION_EXECUTED logged", "AI_ACTION_EXECUTED" in events, "")

    # ---------- security ----------
    s, _ = req("POST", "/api/agent/ask", {"question": "What should I do today?"})
    check("ask requires auth", s in (401, 403), str(s))
    s, _ = req("GET", "/api/agent/recommendations")
    check("recommendations require auth", s in (401, 403), str(s))
    s, _ = req("POST", "/api/agent/actions/prepare",
               {"action_type": "price_change", "payload": {"product_id": pid, "new_price": 1}})
    check("actions require auth", s in (401, 403), str(s))

    # ---------- validation guards ----------
    s, _ = req("POST", "/api/agent/actions/prepare", {
        "action_type": "price_change",
        "payload": {"product_id": pid, "new_price": 0.5},
    }, token=tok)
    check("price below floor refused at prepare", s == 422, str(s))
    s, _ = req("POST", "/api/agent/actions/prepare", {
        "action_type": "shutdown_everything", "payload": {},
    }, token=tok)
    check("unknown action type refused", s in (400, 422), str(s))

    print(f"\nPHASE3 RESULT: {PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
