"""Phase 2 end-to-end verification (run against a live API).

Covers: pricing/margin engine, minimum price policy, bargaining decisions,
negotiated-price sale (floor enforced), inventory intelligence, expiry
separation, purchase create/receive/cancel (inventory + batches + cost
history), supplier compare, reorder, alerts dedup + read, settings.

Never prints the token. Exits non-zero on any failed check.
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
        with OPENER.open(r, timeout=30) as res:
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

    # ---------- seed product pick ----------
    s, prods = req("GET", "/api/products", token=tok)
    check("products list", s == 200 and prods["count"] > 0, str(s))
    items = [p for p in prods["items"] if p["quantity"] >= 15]
    pid = items[0]["id"]
    cost = float(items[0]["purchase_price"])
    sp = float(items[0]["selling_price"])

    # ---------- pricing & margin engine ----------
    s, pr = req("GET", f"/api/inventory/products/{pid}/pricing", token=tok)
    check("pricing endpoint", s == 200 and pr.get("purchase_cost") == cost, str(s))
    check("margin math", abs(pr["gross_profit"] - round(sp - cost, 2)) < 0.01, str(pr))
    expected_floor = pr["minimum_acceptable_price"]
    check("floor >= cost", expected_floor >= cost, f"{expected_floor} vs {cost}")
    floor = expected_floor

    s, di = req("POST", f"/api/inventory/products/{pid}/discount-impact",
                {"proposed_price": round(floor - 1, 2)}, token=tok)
    check("discount below floor rejected", s == 200 and di.get("allowed") is False, str(di))
    s, di2 = req("POST", f"/api/inventory/products/{pid}/discount-impact",
                 {"proposed_price": floor}, token=tok)
    check("discount at floor allowed", s == 200 and di2.get("allowed") is True, str(di2))

    # ---------- bargaining ----------
    s, b = req("POST", f"/api/inventory/products/{pid}/bargain",
               {"offer": max(cost - 5, 0.5), "quantity": 1}, token=tok)
    check("bargain below cost -> REJECT", s == 200 and b.get("decision") == "REJECT", str(b))
    check("reject reason mentions cost", "cost" in b.get("reason", "").lower(), b.get("reason", ""))

    offer = round(max(floor - 2, cost + 0.5), 2)
    if offer < cost:
        offer = round(cost + 0.5, 2)
    s, b = req("POST", f"/api/inventory/products/{pid}/bargain", {"offer": offer}, token=tok)
    if offer < floor:
        check("bargain below floor -> COUNTER", b.get("decision") == "COUNTER", str(b))
        check("counteroffer == floor", b.get("counteroffer") == floor, str(b.get("counteroffer")))
    else:
        check("bargain near floor -> ACCEPT", b.get("decision") == "ACCEPT", str(b))

    s, b = req("POST", f"/api/inventory/products/{pid}/bargain",
               {"offer": round(min(floor + 1, sp), 2)}, token=tok)
    check("bargain at/above floor -> ACCEPT", b.get("decision") == "ACCEPT", str(b))
    s, b = req("POST", f"/api/inventory/products/{pid}/bargain",
               {"offer": round(sp + 50, 2)}, token=tok)
    check("bargain above selling price -> INVALID", b.get("decision") == "INVALID", str(b))

    # ---------- negotiated-price sale (policy enforced server-side) ----------
    s, inv0 = req("GET", f"/api/inventory/products/{pid}/intelligence", token=tok)
    qty0 = inv0["pricing"].get("units_in_stock") if "units_in_stock" in inv0["pricing"] else None
    s, intel_prod = req("GET", "/api/inventory/intelligence", token=tok)
    me = [i for i in intel_prod["items"] if i["product_id"] == pid][0]
    qty0 = me["sellable_quantity"]

    s, sale = req("POST", "/api/sales", {
        "items": [{"product_id": pid, "quantity": 1, "unit_price": floor}],
        "payment_method": "cash",
    }, token=tok)
    check("negotiated sale accepted at floor", s == 201 and abs(sale["total"] - floor) < 0.01,
          f"{s} {sale}")
    s, me2 = req("GET", "/api/inventory/intelligence", token=tok)
    me2item = [i for i in me2["items"] if i["product_id"] == pid][0]
    check("inventory decreased after bargained sale", me2item["quantity"] == me["quantity"] - 1,
          f"{me['quantity']} -> {me2item['quantity']}")

    s, _ = req("POST", "/api/sales", {
        "items": [{"product_id": pid, "quantity": 1, "unit_price": round(max(floor - 10, 0), 2)}],
        "payment_method": "cash",
    }, token=tok)
    check("sale below floor rejected", s == 400, str(s))

    # ---------- inventory intelligence ----------
    s, intel = req("GET", "/api/inventory/intelligence", token=tok)
    check("intelligence endpoint", s == 200 and intel["count"] > 0, str(s))
    valid_statuses = {"HEALTHY", "LOW_STOCK", "CRITICAL_STOCK", "OVERSTOCKED", "SLOW_MOVING",
                      "DEAD_STOCK", "OUT_OF_STOCK"}
    all_ok = all(i["stock_status"] in valid_statuses for i in intel["items"])
    check("stock statuses valid enum", all_ok)
    sum_cost = round(sum(i["stock_value_cost"] for i in intel["items"]), 2)
    check("valuation summary consistent",
          abs(intel["summary"]["total_cost_value"] - sum_cost) < 0.05,
          f"{intel['summary']['total_cost_value']} vs {sum_cost}")
    any_velocity = any(i["velocity"] is not None and float(i["velocity"]) > 0 for i in intel["items"])
    check("velocity computed from sales", any_velocity)

    # ---------- expiry ----------
    s, exp = req("GET", "/api/inventory/expiry", token=tok)
    check("expiry endpoint", s == 200 and "expired_batches" in exp, str(s))
    for eb in exp["expired_batches"]:
        check("expired separated from sellable", eb["status"] == "expired", str(eb.get("status")))
        break
    has_expiry_cases = len(exp["sellable_batches"]) + len(exp["expired_batches"]) > 0
    check("expiry engine has real cases (seeded near-expiry)", has_expiry_cases,
          str(exp["summary"]))
    for b in exp["sellable_batches"]:
        check("expiry estimate labelled", "expected_sales_before_expiry_estimate" in b, str(b.keys()))
        check("expiry recommendation present", bool(b.get("recommendation")), str(b))
        break

    # ---------- purchases ----------
    s, sups = req("GET", "/api/suppliers", token=tok)
    check("suppliers list", s == 200 and sups["count"] > 0, str(s))
    sup = sups["items"][0]
    if not sup.get("lead_time_days"):
        pass  # optional field

    s, po = req("POST", "/api/purchases", {
        "supplier_id": sup["id"],
        "items": [{"product_id": pid, "quantity": 10,
                   "unit_cost": round(cost + 1, 2), "expiry_date": "2027-03-01"}],
        "invoice_no": "E2E-INV-001",
    }, token=tok)
    check("purchase created pending", s == 201 and po.get("status") == "pending", f"{s} {po}")
    po_id = po["id"]

    qty_before = me2item["quantity"]
    s, rec = req("POST", f"/api/purchases/{po_id}/receive", token=tok)
    check("purchase received", s == 200 and rec.get("status") == "received", f"{s} {rec}")

    s, prod_after = req("GET", f"/api/products/{pid}", token=tok)
    check("inventory increased by 10", prod_after["quantity"] == qty_before + 10,
          f"{qty_before} -> {prod_after['quantity']}")
    new_batch = [b for b in prod_after["batches"] if b["quantity"] == 10]
    check("batch created on receive", len(new_batch) >= 1, str(len(prod_after["batches"])))

    s, ch = req("GET", f"/api/inventory/products/{pid}/cost-history", token=tok)
    check("cost history recorded", s == 200 and len(ch.get("changes", [])) >= 1
          and float(ch["changes"][0]["new_value"]) == round(cost + 1, 2), str(ch.get("changes", [])[:1]))
    check("old cost preserved", ch["changes"][0].get("old_value") is not None, str(ch["changes"][:1]))

    s, _ = req("POST", f"/api/purchases/{po_id}/receive", token=tok)
    check("double receive rejected 409", s == 409, str(s))

    s, po2 = req("POST", "/api/purchases", {
        "supplier_id": sup["id"],
        "items": [{"product_id": pid, "quantity": 5, "unit_cost": round(cost + 2, 2)}],
    }, token=tok)
    s, _ = req("POST", f"/api/purchases/{po2['id']}/cancel", token=tok)
    check("pending PO cancelled", s == 200, str(s))
    s, _ = req("POST", f"/api/purchases/{po2['id']}/receive", token=tok)
    check("receive cancelled PO rejected", s == 409, str(s))

    # ---------- supplier profile & comparison ----------
    s, prof = req("GET", f"/api/suppliers/{sup['id']}", token=tok)
    check("supplier profile", s == 200 and prof.get("order_count", 0) >= 1, str(s))
    s, cmp = req("GET", f"/api/inventory/products/{pid}/suppliers/compare", token=tok)
    check("supplier comparison", s == 200 and len(cmp.get("options", [])) > 0, str(s))
    ok_opts = [o for o in cmp["options"] if o.get("comparison_status") == "ok"]
    if ok_opts:
        best_avg = min(o["avg_cost"] for o in ok_opts)
        check("best fit = lowest avg cost", cmp["best_fit"]["avg_cost"] == best_avg, str(cmp["best_fit"]))
    else:
        check("comparison honest about missing data", "insufficient" in cmp.get("note", "").lower(),
              cmp.get("note", ""))

    # ---------- reorder ----------
    s, re_ = req("GET", f"/api/inventory/products/{pid}/reorder", token=tok)
    check("reorder endpoint", s == 200 and re_.get("status") in ("ok", "insufficient_data"), str(s))
    if re_.get("status") == "ok":
        check("reorder math fields", all(k in re_ for k in
              ("velocity_per_day", "days_of_stock", "reorder_point")), str(re_.keys()))
        if re_.get("reorder_required"):
            check("reorder qty positive when required", re_["recommended_quantity"] > 0, str(re_))

    # ---------- alerts ----------
    s, al = req("GET", "/api/alerts", token=tok)
    check("alerts list", s == 200 and "open_count" in al, str(s))
    open_before = al["open_count"]
    s, r1 = req("POST", "/api/alerts/refresh", token=tok)
    s, al2 = req("GET", "/api/alerts", token=tok)
    check("refresh state-based (no alert spam)", al2["open_count"] == open_before,
          f"{open_before} -> {al2['open_count']}")
    s, r2 = req("POST", "/api/alerts/refresh", token=tok)
    check("second refresh creates 0 alerts", r2.get("alerts_created") == 0, str(r2))

    if al2["items"]:
        aid = al2["items"][0]["id"]
        unread0 = al2["unread_count"]
        s, _ = req("POST", f"/api/alerts/{aid}/read", token=tok)
        s, al3 = req("GET", "/api/alerts", token=tok)
        check("mark read decrements unread", al3["unread_count"] == unread0 - 1,
              f"{unread0} -> {al3['unread_count']}")
        s, _ = req("POST", f"/api/alerts/{aid}/acknowledge", {"note": "e2e ack"}, token=tok)
        check("acknowledge works", s == 200, str(s))

    # ---------- settings ----------
    s, st = req("GET", "/api/inventory/settings", token=tok)
    check("settings readable", s == 200 and st.get("min_margin_pct") is not None, str(st))
    old_margin = st["min_margin_pct"]
    s, _ = req("PATCH", "/api/inventory/settings", {"min_margin_pct": 25}, token=tok)
    s, pr2 = req("GET", f"/api/inventory/products/{pid}/pricing", token=tok)
    check("floor rises with min_margin 25%", pr2["minimum_acceptable_price"] > floor,
          f"{pr2['minimum_acceptable_price']} vs {floor}")
    req("PATCH", "/api/inventory/settings", {"min_margin_pct": old_margin}, token=tok)

    # ---------- authz ----------
    s, _ = req("GET", "/api/inventory/intelligence")
    check("intelligence requires auth", s in (401, 403), str(s))
    s, _ = req("GET", "/api/purchases")
    check("purchases require auth", s in (401, 403), str(s))

    print(f"\nPHASE2 RESULT: {PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
