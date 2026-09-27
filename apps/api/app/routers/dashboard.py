from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.database import db
from app.security import get_current_user

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard/summary")
async def dashboard_summary(user: dict = Depends(get_current_user)) -> dict[str, Any]:
    store_id = user["store_id"]

    kpis = await db.fetchrow(
        """
        with today as (
          select
            coalesce(sum(total), 0) as sales,
            count(*) as orders
          from sales
          where store_id = $1
            and status = 'completed'
            and created_at::date = current_date
        ),
        yesterday as (
          select coalesce(sum(total), 0) as sales
          from sales
          where store_id = $1
            and status = 'completed'
            and created_at::date = current_date - 1
        ),
        stock as (
          select
            count(*) filter (where quantity > 0) as in_stock_products,
            count(*) as total_products,
            coalesce(sum(quantity * (
              select purchase_price from products p where p.id = inventory.product_id
            )), 0) as inventory_value
          from inventory
          where store_id = $1
        ),
        risk as (
          select
            count(*) filter (where quantity <= p.reorder_level) as low_stock,
            count(*) filter (where quantity = 0) as out_of_stock
          from inventory i
          join products p on p.id = i.product_id
          where i.store_id = $1 and p.is_active
        ),
        expiry as (
          select coalesce(count(*), 0) as expiring
          from inventory_batches b
          where b.store_id = $1
            and b.quantity > 0
            and b.expiry_date <= current_date + 14
        ),
        profit as (
          select coalesce(sum((s.unit_price - s.unit_cost) * s.quantity), 0) as gross
          from sale_items s
          join sales sale on sale.id = s.sale_id
          where sale.store_id = $1
            and sale.status = 'completed'
            and sale.created_at::date = current_date
        )
        select
          t.sales as today_sales,
          t.orders as today_orders,
          y.sales as yesterday_sales,
          st.inventory_value,
          st.total_products,
          st.in_stock_products,
          r.low_stock,
          r.out_of_stock,
          e.expiring,
          p.gross as today_profit
        from today t, yesterday y, stock st, risk r, expiry e, profit p
        """,
        store_id,
    )

    priorities = await db.fetch(
        """
        select id, type, severity, title, description, created_at
        from alerts
        where store_id = $1 and status = 'open'
        order by
          case severity when 'critical' then 1 when 'warning' then 2 when 'opportunity' then 3 else 4 end,
          created_at desc
        limit 8
        """,
        store_id,
    )

    week = await db.fetch(
        """
        select to_char(created_at, 'DY') as day,
               date_trunc('day', created_at) as date,
               sum(total) as total
        from sales
        where store_id = $1
          and status = 'completed'
          and created_at >= date_trunc('day', current_date) - interval '6 days'
        group by 1, 2
        order by 2
        """,
        store_id,
    )

    top_products = await db.fetch(
        """
        select p.name,
               p.category,
               sum(si.quantity) as units,
               sum(si.line_total) as revenue,
               sum((si.unit_price - si.unit_cost) * si.quantity) as profit
        from sale_items si
        join sales s on s.id = si.sale_id
        join products p on p.id = si.product_id
        where s.store_id = $1
          and s.status = 'completed'
          and s.created_at >= current_date - interval '30 days'
        group by p.name, p.category
        order by revenue desc
        limit 5
        """,
        store_id,
    )

    recommendations = await db.fetch(
        """
        select id, type, title, description, evidence, severity, confidence, status, proposed_action, created_at
        from ai_recommendations
        where store_id = $1
        order by
          case severity when 'critical' then 1 when 'warning' then 2 when 'opportunity' then 3 else 4 end,
          created_at desc
        limit 6
        """,
        store_id,
    )

    activity = await db.fetch(
        """
        select id, event_type, message, created_at, entity_type
        from activity_logs
        where store_id = $1
        order by created_at desc
        limit 12
        """,
        store_id,
    )

    cats = await db.fetch(
        """
        select p.category,
               sum(si.line_total) as revenue
        from sale_items si
        join sales s on s.id = si.sale_id
        join products p on p.id = si.product_id
        where s.store_id = $1 and s.status = 'completed'
          and s.created_at >= date_trunc('month', current_date)
        group by p.category
        order by revenue desc
        limit 6
        """,
        store_id,
    )

    k = dict(kpis) if kpis else {}
    y_sales = float(k.get("yesterday_sales") or 0)
    t_sales = float(k.get("today_sales") or 0)
    if y_sales > 0:
        sales_delta = round(((t_sales - y_sales) / y_sales) * 100, 1)
    else:
        sales_delta = 100.0 if t_sales > 0 else 0.0

    total_products = int(k.get("total_products") or 0)
    low_stock = int(k.get("low_stock") or 0)
    expiring = int(k.get("expiring") or 0)

    return {
        "store": {
            "name": user["store_name"],
            "location": user["store_location"],
            "currency": user["currency"],
        },
        "user": {
            "name": user["full_name"],
            "email": user["email"],
        },
        "kpis": {
            "today_sales": float(k.get("today_sales") or 0),
            "yesterday_sales": y_sales,
            "sales_delta_pct": sales_delta,
            "today_orders": int(k.get("today_orders") or 0),
            "today_profit": float(k.get("today_profit") or 0),
            "inventory_value": float(k.get("inventory_value") or 0),
            "total_products": total_products,
            "low_stock": low_stock,
            "out_of_stock": int(k.get("out_of_stock") or 0),
            "expiring_soon": expiring,
            "open_alerts": len(priorities),
            "recommendations": len(recommendations),
        },
        "priorities": [dict(r) for r in priorities],
        "week_sales": [dict(r) for r in week],
        "top_products": [
            {
                **dict(r),
                "revenue": float(r["revenue"]),
                "profit": float(r["profit"]),
                "units": int(r["units"]),
            }
            for r in top_products
        ],
        "categories": [
            {"category": r["category"], "revenue": float(r["revenue"])}
            for r in cats
        ],
        "recommendations": [dict(r) for r in recommendations],
        "activity": [dict(r) for r in activity],
    }
