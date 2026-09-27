"""Phase 7 — retail operations (online-first, Supabase single source of truth).

Everything here is deterministic backend logic:
- idempotency: retrying a critical mutation with the same key returns the
  original result instead of duplicating it.
- barcodes: multi-barcode mapping + GS1 AI(01)/AI(10)/AI(17) parsing.
- receiving: case conversion server-side, partial receipts, damaged/rejected
  quantities, multi-batch, MFD+shelf-life expiry calculation.
- adjustments/cycle counts: never silently change stock — always a movement +
  audit record with a merchant-selected reason.
- returns: classification decides restock; damaged/expired never re-enter
  sellable inventory.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any, Optional

from app.database import db


class RetailError(Exception):
    def __init__(self, message: str, code: str = "RETAIL_ERROR", status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


# ==================================================================
# Idempotency
# ==================================================================
async def idempotent_result(
    store_id: str, key: Optional[str], endpoint: str
) -> Optional[dict[str, Any]]:
    """Return the stored result for a retried idempotency key, else None."""
    if not key:
        return None
    row = await db.fetchrow(
        "select result, status_code from idempotency_keys where store_id=$1 and key=$2 and endpoint=$3",
        store_id, key, endpoint,
    )
    if not row:
        return None
    result = row["result"]
    if isinstance(result, str):
        result = json.loads(result)
    return {"stored": True, "status_code": row["status_code"], "result": result}


async def store_idempotent_result(
    store_id: str, key: Optional[str], endpoint: str, result: dict[str, Any],
    status_code: int = 200,
) -> None:
    if not key:
        return
    await db.execute(
        """
        insert into idempotency_keys (store_id, key, endpoint, result, status_code)
        values ($1, $2, $3, $4::jsonb, $5)
        on conflict (store_id, key, endpoint) do nothing
        """,
        store_id, key, endpoint, json.dumps(result, default=str), status_code,
    )


# ==================================================================
# GS1 / structured barcode parsing (deterministic)
# ==================================================================
def parse_gs1(raw: str) -> dict[str, Any]:
    """Parse GS1-128 / DataMatrix style element strings.

    Supports the AIs that matter on retail shelves:
      (01) GTIN, (10) batch/lot, (11) MFD YYMMDD, (17) expiry YYMMDD,
      (21) serial, (30) count.
    Returns {gtin, batch, expiry_date, mfd_date, serial, count, raw} —
    fields absent from the code simply stay None. Never guesses.
    """
    out: dict[str, Any] = {"gtin": None, "batch": None, "expiry_date": None,
                           "mfd_date": None, "serial": None, "count": None,
                           "raw": raw}
    s = raw.strip()
    # accept human-readable parentheses form too: (01)123...(10)LOT
    s = re.sub(r"\((\d{2})\)", r"\1", s)
    if not re.match(r"^01\d{14}", s) and not re.match(r"^\d{2}10", s):
        return out  # not a GS1 element string — caller falls back to plain lookup

    def _yymmdd(v: str) -> Optional[date]:
        try:
            yy, mm, dd = int(v[0:2]), int(v[2:4]), int(v[4:6])
            year = 2000 + yy
            return date(year, mm, dd or 1)  # day "00" = end-of-month convention
        except ValueError:
            return None

    # Application-identifier walker. Fixed-length AIs advance exactly; the
    # variable-length AIs we support (10 batch, 21 serial, 30 count, 37 qty)
    # run to the end unless a GS1 separator (ASCII 29) starts a new field.
    GS = "\x1d"
    i = 0
    while i < len(s):
        if s[i] == GS:  # separators separate fields; skip and continue
            i += 1
            continue
        ai = s[i:i + 2]
        if not ai.isdigit():
            break
        if ai == "01":
            out["gtin"] = s[i + 2:i + 16]; i += 16
        elif ai == "11":
            out["mfd_date"] = _yymmdd(s[i + 2:i + 8]); i += 8
        elif ai == "17":
            out["expiry_date"] = _yymmdd(s[i + 2:i + 8]); i += 8
        elif ai == "10":
            rest = s[i + 2:]
            # Split on separators first; if none, the batch runs to the end
            # unless an unambiguous DATE AI (11/17, always 6 digits parsing
            # as YYMMDD) follows. We deliberately do NOT terminate on digit
            # pairs that merely look like other AIs inside the value.
            parts = rest.split(GS)
            value = parts[0]
            if GS not in rest:
                stop = len(value)
                for marker in ("17", "11"):
                    p = value.find(marker)
                    if p > 0:
                        candidate = value[p + 2:p + 8]
                        if len(candidate) == 6 and candidate.isdigit() and _yymmdd(candidate) is not None:
                            stop = min(stop, p)
                value = value[:stop]
            out["batch"] = value or None
            i += 2 + len(value)
            if GS in rest:
                i += 1  # skip separator; next chars are the next AI
        elif ai == "21":
            rest = s[i + 2:]
            parts = rest.split(GS)
            value = parts[0]
            if GS not in rest:
                stop = len(value)
                for marker in ("17", "11"):
                    p = value.find(marker)
                    if p > 0:
                        candidate = value[p + 2:p + 8]
                        if len(candidate) == 6 and candidate.isdigit() and _yymmdd(candidate) is not None:
                            stop = min(stop, p)
                value = value[:stop]
            out["serial"] = value or None
            i += 2 + len(value)
            if GS in rest:
                i += 1  # skip separator; next chars are the next AI
        elif ai == "30":
            m = re.match(r"30(\d{1,8})", s[i:])
            out["count"] = int(m.group(1)) if m else None
            i += 2 + (len(m.group(1)) if m else 0)
        else:
            break  # unsupported AI — stop; never guess remaining fields
    return out


async def lookup_barcode(store_id: str, raw_code: str) -> dict[str, Any]:
    """Resolve any scanned code: product_barcodes first, legacy products.barcode
    second, GS1 GTIN third. Returns product + structured payload when present."""
    code = raw_code.strip()
    gs1 = parse_gs1(code)

    # 1) mapped barcodes (each/pack/case level)
    row = await db.fetchrow(
        """
        select pb.*, p.id as product_id, p.name, p.category, p.sku, p.unit,
               p.mrp, p.selling_price, p.purchase_price, p.is_active,
               coalesce(i.quantity, 0) as quantity
        from product_barcodes pb
        join products p on p.id = pb.product_id and p.store_id = pb.store_id
        left join inventory i on i.product_id = p.id and i.store_id = p.store_id
        where pb.store_id = $1 and pb.barcode = $2 and pb.active
        """,
        store_id, code,
    )
    if row:
        d = dict(row)
        d["resolved_from"] = "product_barcodes"
        d["units_represented"] = d["quantity_represented"]
        return {"result": "FOUND", "product": d, "gs1": gs1 if gs1["gtin"] else None}

    # 2) case-level scans must resolve via packaging mapping even if code is the
    #    legacy product barcode — fall through to legacy lookup for that.
    legacy = await db.fetchrow(
        """
        select p.id as product_id, p.name, p.category, p.sku, p.barcode, p.unit,
               p.mrp, p.selling_price, p.purchase_price, p.is_active,
               coalesce(i.quantity, 0) as quantity
        from products p
        left join inventory i on i.product_id = p.id and i.store_id = p.store_id
        where p.store_id = $1 and p.barcode = $2
        order by p.is_active desc, p.name
        """,
        store_id, code,
    )
    if legacy:
        matches = [dict(legacy)]
        # there may be several products sharing a legacy barcode
        if matches and matches[0]["is_active"] is False:
            return {"result": "INACTIVE_PRODUCT", "product": matches[0], "gs1": None}
        return {"result": "FOUND", "product": matches[0],
                "gs1": gs1 if gs1["gtin"] else None}

    # 3) GS1 GTIN against mapped barcodes (a GTIN identifies the each-level code)
    if gs1["gtin"]:
        ldr = gs1["gtin"].lstrip("0")
        row = await db.fetchrow(
            """
            select pb.*, p.id as product_id, p.name, p.category, p.sku, p.unit,
                   p.mrp, p.selling_price, p.purchase_price, p.is_active,
                   coalesce(i.quantity, 0) as quantity
            from product_barcodes pb
            join products p on p.id = pb.product_id and p.store_id = pb.store_id
            left join inventory i on i.product_id = p.id and i.store_id = p.store_id
            where pb.store_id = $1 and lpad(pb.barcode, 14, '0') = lpad($2, 14, '0')
              and pb.active
            """,
            store_id, ldr,
        )
        if row:
            d = dict(row)
            d["resolved_from"] = "gs1_gtin"
            return {"result": "FOUND", "product": d, "gs1": gs1}

    return {"result": "NOT_FOUND", "product": None, "gs1": gs1 if gs1["gtin"] else None}


# ==================================================================
# Receiving orchestration
# ==================================================================
async def receive_shipment(
    store_id: str, user_id: str, purchase_order_id: str,
    lines: list[dict[str, Any]], idempotency_key: Optional[str] = None,
) -> dict[str, Any]:
    """Receive a shipment against a PO.

    Line input is merchant-facing: {product_id, level, qty, unit_cost, batch_no,
    manufacturing_date, expiry_date, shelf_life_value, shelf_life_unit,
    damaged, rejected}. Case/pack conversion happens HERE (server-side), the
    RPC only ever sees EACH units. Partial receipts keep the PO
    partially_received; damaged units are quarantined, never sellable.
    """
    stored = await idempotent_result(store_id, idempotency_key, "receive_shipment")
    if stored:
        return stored["result"]

    owned = await db.fetchval(
        "select 1 from purchase_orders where id=$1 and store_id=$2",
        purchase_order_id, store_id,
    )
    if not owned:
        raise RetailError("Purchase order not found.", "NOT_FOUND", 404)

    rpc_lines: list[dict[str, Any]] = []
    conversion_notes: list[dict[str, Any]] = []
    for ln in lines:
        pid = ln.get("product_id")
        level = (ln.get("level") or "EACH").upper()
        qty = int(ln.get("qty") or 0)
        if not pid:
            raise RetailError("Receipt line missing product.", "INVALID_LINE")
        units = qty
        if level != "EACH":
            factor = await db.fetchval(
                "select conversion_factor from product_packaging "
                "where store_id=$1 and product_id=$2 and level=$3 and active",
                store_id, pid, level,
            )
            if not factor:
                raise RetailError(
                    f"No packaging conversion configured for level {level}. "
                    "Receive in EACH units or configure the packaging first.",
                    "NO_CONVERSION",
                )
            units = qty * int(factor)
            conversion_notes.append({"product_id": pid, "level": level,
                                     "cases": qty, "units": units})
        rpc_lines.append({
            "product_id": pid,
            "units": units,
            "unit_cost": float(ln.get("unit_cost") or 0),
            "batch_no": ln.get("batch_no"),
            "manufacturing_date": ln.get("manufacturing_date"),
            "expiry_date": ln.get("expiry_date"),
            "shelf_life_value": ln.get("shelf_life_value"),
            "shelf_life_unit": ln.get("shelf_life_unit"),
            "damaged": int(ln.get("damaged") or 0),
            "rejected": int(ln.get("rejected") or 0),
        })

    try:
        raw = await db.fetchval(
            "select receive_purchase_v2($1::uuid, $2::uuid, $3::jsonb, $4::uuid)",
            store_id, purchase_order_id, json.dumps(rpc_lines), user_id,
        )
    except Exception as exc:  # noqa: BLE001 — map SQL messages to honest errors
        msg = str(exc)
        if "exceeds remaining" in msg:
            raise RetailError(msg.split("DETAIL:")[-1].strip(), "OVER_RECEIPT") from exc
        if "not on this purchase order" in msg:
            raise RetailError(msg.split("DETAIL:")[-1].strip(), "WRONG_PO") from exc
        if "cannot receive" in msg:
            raise RetailError(msg.split("DETAIL:")[-1].strip(), "PO_CLOSED", 409) from exc
        raise RetailError("Receiving failed; nothing was changed.", "RECEIVE_FAILED") from exc

    result = json.loads(raw) if isinstance(raw, str) else raw
    result["conversions"] = conversion_notes
    await store_idempotent_result(store_id, idempotency_key, "receive_shipment", result, 200)
    return result


async def po_receiving_status(store_id: str, purchase_order_id: str) -> dict[str, Any]:
    po = await db.fetchrow(
        """
        select po.id, po.status, po.supplier_id, s.name as supplier_name, po.created_at
        from purchase_orders po left join suppliers s on s.id = po.supplier_id
        where po.id = $1 and po.store_id = $2
        """,
        purchase_order_id, store_id,
    )
    if not po:
        raise RetailError("Purchase order not found.", "NOT_FOUND", 404)
    lines = await db.fetch(
        """
        select pi.product_id, p.name, pi.quantity as ordered,
               pi.quantity_received, pi.quantity_damaged, pi.quantity_rejected,
               pi.unit_cost,
               (pi.quantity - pi.quantity_received - pi.quantity_damaged - pi.quantity_rejected) as remaining
        from purchase_items pi join products p on p.id = pi.product_id
        where pi.purchase_order_id = $1
        """,
        purchase_order_id,
    )
    return {"po": dict(po), "lines": [dict(r) for r in lines]}


# ==================================================================
# Expiry assistance (deterministic — the AI/OCR never does date math)
# ==================================================================
def calculate_expiry_client(
    expiry_date: Optional[str], manufacturing_date: Optional[str],
    shelf_life_value: Optional[int], shelf_life_unit: Optional[str],
) -> dict[str, Any]:
    exp = None
    if expiry_date:
        exp = date.fromisoformat(expiry_date)
    elif manufacturing_date and shelf_life_value and shelf_life_unit:
        # mirror of the SQL function for the assist payload; authoritative
        # calculation happens again inside receive_purchase_v2.
        mfd = date.fromisoformat(manufacturing_date)
        v = int(shelf_life_value)
        if shelf_life_unit == "day":
            from datetime import timedelta
            exp = mfd + timedelta(days=v)
        elif shelf_life_unit == "week":
            from datetime import timedelta
            exp = mfd + timedelta(weeks=v)
        elif shelf_life_unit == "month":
            total = mfd.month - 1 + v
            exp = date(mfd.year + total // 12, total % 12 + 1, min(mfd.day, 28))
        elif shelf_life_unit == "year":
            try:
                exp = date(mfd.year + v, mfd.month, mfd.day)
            except ValueError:
                exp = date(mfd.year + v, mfd.month, 28)
    return {
        "calculated_expiry": exp.isoformat() if exp else None,
        "method": ("explicit" if expiry_date else
                   "mfd_shelf_life" if exp else "none"),
        "requires_confirmation": exp is not None,
    }


# ==================================================================
# Stock adjustments + cycle counting
# ==================================================================
ADJUST_REASONS = ("DAMAGE", "WASTAGE", "SHRINKAGE", "COUNTING_ERROR", "EXPIRED",
                  "RETURN_TO_SUPPLIER", "OTHER")


async def adjust_stock(
    store_id: str, user_id: str, product_id: str, change: int,
    reason: str, note: Optional[str] = None, counted: Optional[int] = None,
    cycle_count_id: Optional[str] = None, idempotency_key: Optional[str] = None,
) -> dict[str, Any]:
    if reason not in ADJUST_REASONS:
        raise RetailError("Adjustment reason is required.", "INVALID_REASON")
    if change == 0:
        raise RetailError("Adjustment change cannot be zero.", "INVALID_CHANGE")

    stored = await idempotent_result(store_id, idempotency_key, "adjust_stock")
    if stored:
        return stored["result"]

    async with db.pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                update inventory set quantity = quantity + $3, updated_at = now()
                where store_id=$1 and product_id=$2 and quantity + $3 >= 0
                returning quantity
                """,
                store_id, product_id, change,
            )
            if not row:
                cur = await conn.fetchval(
                    "select quantity from inventory where store_id=$1 and product_id=$2",
                    store_id, product_id,
                )
                raise RetailError(
                    f"Adjustment would make stock negative (current: {cur}).",
                    "NEGATIVE_STOCK",
                )
            await conn.execute(
                """
                insert into inventory_movements
                  (store_id, product_id, change, quantity_after, reason, movement_type,
                   reference_type, reference_id)
                values ($1, $2, $3, $4, $5, case when $3 > 0 then 'ADJUSTMENT_IN' else 'ADJUSTMENT_OUT' end,
                        'stock_adjustment', null)
                """,
                store_id, product_id, change, row["quantity"], reason,
            )
            adj_id = await conn.fetchval(
                """
                insert into stock_adjustments
                  (store_id, product_id, change, reason, note, counted_quantity,
                   system_quantity, cycle_count_id, user_id)
                values ($1, $2, $3, $4, $5, $6,
                        (select quantity from inventory where store_id=$1 and product_id=$2) - $3,
                        $7, $8)
                returning id
                """,
                store_id, product_id, change, reason, note, counted,
                cycle_count_id, user_id,
            )
            await conn.execute(
                """
                insert into activity_logs (store_id, user_id, event_type, entity_type, entity_id, new_state, message)
                values ($1, $2, 'INVENTORY_ADJUSTED', 'product', $3, $4::jsonb, $5)
                """,
                store_id, user_id, product_id,
                json.dumps({"change": change, "reason": reason}),
                f"Stock adjusted {change:+d} ({reason})",
            )
    result = {"adjustment_id": str(adj_id), "quantity_after": row["quantity"], "change": change, "reason": reason}
    await store_idempotent_result(store_id, idempotency_key, "adjust_stock", result, 200)
    return result


async def create_cycle_count(store_id: str, user_id: str, product_ids: list[str],
                             scope_note: Optional[str] = None) -> dict[str, Any]:
    if not product_ids:
        raise RetailError("Select at least one product to count.", "EMPTY_COUNT")
    async with db.pool.acquire() as conn:
        async with conn.transaction():
            cid = await conn.fetchval(
                "insert into cycle_counts (store_id, created_by, scope_note) values ($1,$2,$3) returning id",
                store_id, user_id, scope_note,
            )
            for pid in product_ids:
                expected = await conn.fetchval(
                    "select coalesce(quantity,0) from inventory where store_id=$1 and product_id=$2",
                    store_id, pid,
                )
                await conn.execute(
                    """
                    insert into cycle_count_lines (cycle_count_id, product_id, expected_quantity, counted_quantity, variance)
                    values ($1, $2, $3, $3, 0)
                    """,
                    cid, pid, expected or 0,
                )
    return {"cycle_count_id": str(cid)}


async def submit_count_line(store_id: str, user_id: str, cycle_count_id: str,
                            product_id: str, counted: int, reason: Optional[str]) -> dict[str, Any]:
    row = await db.fetchrow(
        """
        update cycle_count_lines
        set counted_quantity = $3, variance = $3 - expected_quantity, reason = $4
        where cycle_count_id=$1 and product_id=$2
          and cycle_count_id in (select id from cycle_counts where store_id=$5 and status='OPEN')
        returning id, expected_quantity
        """,
        cycle_count_id, product_id, counted, reason, store_id,
    )
    if not row:
        raise RetailError("Count line not found or count is not open.", "NOT_FOUND", 404)
    return {"line_id": str(row["id"]), "expected": row["expected_quantity"],
            "variance": counted - row["expected_quantity"]}


async def complete_cycle_count(store_id: str, user_id: str, cycle_count_id: str) -> dict[str, Any]:
    """Apply every non-zero variance as an audited adjustment; close the count."""
    cc = await db.fetchrow(
        "select status from cycle_counts where id=$1 and store_id=$2", cycle_count_id, store_id
    )
    if not cc:
        raise RetailError("Cycle count not found.", "NOT_FOUND", 404)
    if cc["status"] != "OPEN":
        raise RetailError(f"Cycle count is {cc['status']}.", "INVALID_STATE", 409)

    lines = await db.fetch(
        "select product_id, counted_quantity, variance, reason from cycle_count_lines "
        "where cycle_count_id=$1 and variance <> 0",
        cycle_count_id,
    )
    applied = 0
    for ln in lines:
        if not ln["reason"]:
            raise RetailError(
                "Every variance needs a reason before completing the count.",
                "REASON_REQUIRED",
            )
        await adjust_stock(store_id, user_id, str(ln["product_id"]), int(ln["variance"]),
                           ln["reason"], note=f"Cycle count {cycle_count_id}",
                           counted=ln["counted_quantity"], cycle_count_id=cycle_count_id)
        await db.execute("update cycle_count_lines set applied=true where cycle_count_id=$1 and product_id=$2",
                         cycle_count_id, ln["product_id"])
        applied += 1
    await db.execute(
        "update cycle_counts set status='COMPLETED', completed_at=now() where id=$1",
        cycle_count_id,
    )
    return {"applied_adjustments": applied, "status": "COMPLETED"}


# ==================================================================
# Sale returns
# ==================================================================
async def create_return(
    store_id: str, user_id: str, sale_id: str, product_id: str, quantity: int,
    classification: str, reason: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> dict[str, Any]:
    if classification not in ("RESALEABLE", "DAMAGED", "EXPIRED", "OTHER"):
        raise RetailError("Return classification is required.", "INVALID_CLASSIFICATION")
    if quantity <= 0:
        raise RetailError("Return quantity must be positive.", "INVALID_QUANTITY")

    stored = await idempotent_result(store_id, idempotency_key, "create_return")
    if stored:
        return stored["result"]

    sale = await db.fetchrow(
        "select id, store_id from sales where id=$1 and store_id=$2", sale_id, store_id
    )
    if not sale:
        raise RetailError("Sale not found.", "NOT_FOUND", 404)
    sold = await db.fetchval(
        "select coalesce(sum(quantity),0) from sale_items where sale_id=$1 and product_id=$2",
        sale_id, product_id,
    )
    already = await db.fetchval(
        "select coalesce(sum(quantity),0) from sale_returns where sale_id=$1 and product_id=$2",
        sale_id, product_id,
    )
    if int(already) + quantity > int(sold):
        raise RetailError(
            f"Cannot return {quantity}: only {int(sold) - int(already)} unit(s) of this product were sold on this sale.",
            "OVER_RETURN",
        )

    price_row = await db.fetchrow(
        "select unit_price from sale_items where sale_id=$1 and product_id=$2 limit 1",
        sale_id, product_id,
    )
    refund_amount = round(float(price_row["unit_price"]) * quantity, 2) if price_row else 0.0

    restock = classification == "RESALEABLE"
    batch_id = None
    async with db.pool.acquire() as conn:
        async with conn.transaction():
            if restock:
                # back to the earliest-expiring eligible batch (FEFO-consistent)
                batch_id = await conn.fetchval(
                    """
                    select id from inventory_batches
                    where store_id=$1 and product_id=$2 and status='sellable' and quantity > 0
                      and (expiry_date is null or expiry_date >= current_date)
                    order by expiry_date nulls last, received_at, id
                    limit 1
                    """,
                    store_id, product_id,
                )
                if batch_id:
                    await conn.execute(
                        "update inventory_batches set quantity = quantity + $3 where id=$1 and store_id=$2",
                        batch_id, store_id, quantity,
                    )
                inv = await conn.fetchrow(
                    """
                    update inventory set quantity = quantity + $3, updated_at = now()
                    where store_id=$1 and product_id=$2 returning quantity
                    """,
                    store_id, product_id, quantity,
                )
                if not inv:
                    await conn.execute(
                        "insert into inventory (store_id, product_id, quantity) values ($1,$2,$3)",
                        store_id, product_id, quantity,
                    )
                    inv_qty = quantity
                else:
                    inv_qty = inv["quantity"]
                await conn.execute(
                    """
                    insert into inventory_movements
                      (store_id, product_id, batch_id, change, quantity_after, reason,
                       movement_type, reference_type, reference_id)
                    values ($1,$2,$3,$4,$5,'CUSTOMER_RETURN','RETURN','sale',$6)
                    """,
                    store_id, product_id, batch_id, quantity, inv_qty, sale_id,
                )
            elif classification == "DAMAGED":
                # damaged returns: quarantine a batch (never sellable), no saleable increase
                batch_id = await conn.fetchval(
                    """
                    insert into inventory_batches
                      (store_id, product_id, batch_no, quantity, purchase_cost, status, source)
                    values ($1,$2,'RETURN-DAMAGED',$3,0,'quarantined','return')
                    returning id
                    """,
                    store_id, product_id, quantity,
                )
                # saleable inventory unchanged for damaged returns
            # EXPIRED / OTHER: recorded, no inventory effect without merchant action

            ret_id = await conn.fetchval(
                """
                insert into sale_returns
                  (store_id, sale_id, product_id, quantity, classification, restock,
                   batch_id, refund_amount, reason, user_id)
                values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                returning id
                """,
                store_id, sale_id, product_id, quantity, classification, restock,
                batch_id, refund_amount, reason, user_id,
            )
            await conn.execute(
                """
                insert into activity_logs (store_id, user_id, event_type, entity_type, entity_id, new_state, message)
                values ($1,$2,'RETURN_CREATED','sale',$3,$4::jsonb,$5)
                """,
                store_id, user_id, sale_id,
                json.dumps({"product_id": product_id, "quantity": quantity,
                            "classification": classification, "restocked": restock}),
                f"Return recorded: {quantity} x {classification}",
            )
    result = {
        "return_id": str(ret_id), "restocked": restock and batch_id is not None,
        "refund_amount": refund_amount,
        "refund_state": "NOT_REFUNDED",
        "note": ("Return recorded. Refund must be issued separately through the payments workflow."
                 if refund_amount else "Return recorded."),
    }
    await store_idempotent_result(store_id, idempotency_key, "create_return", result, 200)
    return result


# ==================================================================
# Product barcodes + packaging management
# ==================================================================
async def add_barcode(store_id: str, product_id: str, barcode: str, barcode_type: str,
                      packaging_level: str, quantity_represented: int,
                      is_primary: bool, source: str = "merchant") -> dict[str, Any]:
    if barcode_type not in ("EAN", "UPC", "GTIN", "ITF14", "GS1_128", "GS1_DATAMATRIX", "QR", "INTERNAL"):
        raise RetailError("Unsupported barcode type.", "INVALID_TYPE")
    if packaging_level not in ("EACH", "PACK", "CASE"):
        raise RetailError("Packaging level must be EACH, PACK or CASE.", "INVALID_LEVEL")
    owned = await db.fetchval(
        "select 1 from products where id=$1 and store_id=$2", product_id, store_id
    )
    if not owned:
        raise RetailError("Product not found.", "NOT_FOUND", 404)
    exists = await db.fetchval(
        "select 1 from product_barcodes where store_id=$1 and barcode=$2", store_id, barcode
    )
    if exists:
        raise RetailError("This barcode is already mapped in your store.", "DUPLICATE_BARCODE", 409)
    if is_primary:
        await db.execute(
            "update product_barcodes set is_primary=false where store_id=$1 and product_id=$2",
            store_id, product_id,
        )
    bid = await db.fetchval(
        """
        insert into product_barcodes
          (store_id, product_id, barcode, barcode_type, packaging_level,
           quantity_represented, is_primary, source)
        values ($1,$2,$3,$4,$5,$6,$7,$8)
        returning id
        """,
        store_id, product_id, barcode, barcode_type, packaging_level,
        max(1, quantity_represented), is_primary, source,
    )
    await db.execute(
        """
        insert into activity_logs (store_id, user_id, event_type, entity_type, entity_id, new_state, message)
        values ($1, null, 'BARCODE_ADDED', 'product', $2, $3::jsonb, $4)
        """,
        store_id, product_id,
        json.dumps({"barcode": barcode, "type": barcode_type, "level": packaging_level}),
        f"Barcode {barcode} mapped ({packaging_level})",
    )
    return {"barcode_id": str(bid)}


async def list_barcodes(store_id: str, product_id: str) -> list[dict[str, Any]]:
    rows = await db.fetch(
        "select * from product_barcodes where store_id=$1 and product_id=$2 and active order by is_primary desc, created_at",
        store_id, product_id,
    )
    return [dict(r) for r in rows]


async def set_packaging(store_id: str, product_id: str, level: str, label: str,
                        conversion_factor: int) -> dict[str, Any]:
    if level not in ("EACH", "PACK", "BOX", "CASE", "CARTON"):
        raise RetailError("Unsupported packaging level.", "INVALID_LEVEL")
    if conversion_factor <= 0:
        raise RetailError("Conversion factor must be positive.", "INVALID_FACTOR")
    owned = await db.fetchval(
        "select 1 from products where id=$1 and store_id=$2", product_id, store_id
    )
    if not owned:
        raise RetailError("Product not found.", "NOT_FOUND", 404)
    await db.execute(
        """
        insert into product_packaging (store_id, product_id, level, label, conversion_factor)
        values ($1,$2,$3,$4,$5)
        on conflict (store_id, product_id, level)
        do update set label = excluded.label, conversion_factor = excluded.conversion_factor, active = true
        """,
        store_id, product_id, level, label or level.title(), conversion_factor,
    )
    return {"ok": True, "level": level, "conversion_factor": conversion_factor}


async def get_packaging(store_id: str, product_id: str) -> list[dict[str, Any]]:
    rows = await db.fetch(
        "select * from product_packaging where store_id=$1 and product_id=$2 and active order by conversion_factor",
        store_id, product_id,
    )
    return [dict(r) for r in rows]


async def product_batches(store_id: str, product_id: str) -> dict[str, Any]:
    rows = await db.fetch(
        """
        select b.id, b.batch_no, b.quantity, b.purchase_cost, b.expiry_date,
               b.manufacturing_date, b.shelf_life_value, b.shelf_life_unit,
               b.expiry_source, b.status, b.received_at, b.source,
               s.name as supplier_name,
               (b.expiry_date - current_date) as days_remaining
        from inventory_batches b
        left join suppliers s on s.id = b.supplier_id
        where b.store_id=$1 and b.product_id=$2
        order by b.expiry_date nulls last, b.received_at
        """,
        store_id, product_id,
    )
    quantity = await db.fetchval(
        "select coalesce((select quantity from inventory where store_id=$1 and product_id=$2), 0)",
        store_id, product_id,
    )
    sellable = await db.fetchval(
        "select sellable_stock($1, $2)", store_id, product_id
    )
    return {
        "batches": [dict(r) for r in rows],
        "stock": {"quantity": int(quantity or 0), "sellable": int(sellable or 0)},
    }
