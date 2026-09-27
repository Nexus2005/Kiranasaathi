"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ClipboardList, Globe, Store } from "lucide-react";
import { AppShell, AuthGuard } from "@/components/shell";
import { PageHeader } from "@/components/page-header";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { StatusBadge, statusTone } from "@/components/status-badge";
import { api } from "@/lib/api";
import { formatINR } from "@/lib/types";
import { cn } from "@/lib/cn";

type OrderRow = {
  id: string;
  state: string;
  payment_method: string;
  cart_discount: number;
  total: number;
  sale_id: string | null;
  created_at: string;
  customer_name: string | null;
  channel: string;
  item_lines: number;
  units: number;
};

const STATE_FILTERS = [
  "",
  "PAID",
  "CONFIRMED",
  "PROCESSING",
  "READY",
  "OUT_FOR_DELIVERY",
  "PENDING_PAYMENT",
  "COMPLETED",
  "CANCELLED",
] as const;

// Fulfillment actions the merchant can take per state (mirrors backend rules)
const ACTIONS: Record<string, { action: string; label: string }[]> = {
  PAID: [{ action: "ACCEPT", label: "Accept" }],
  CONFIRMED: [{ action: "PREPARE", label: "Start preparing" }],
  PROCESSING: [{ action: "READY", label: "Mark ready" }],
  READY: [{ action: "COMPLETE", label: "Complete" }],
  OUT_FOR_DELIVERY: [{ action: "DELIVER", label: "Mark delivered" }],
  DELIVERED: [{ action: "COMPLETE_DELIVERED", label: "Complete" }],
};

export default function OrdersPage() {
  return (
    <AuthGuard>
      <Orders />
    </AuthGuard>
  );
}

function Orders() {
  const [state, setState] = useState<string>("");
  const [orders, setOrders] = useState<OrderRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ tone: "ok" | "err"; text: string } | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const qs = state ? `?state=${state}` : "";
      const res = await api<{ items: OrderRow[] }>(`/api/orders${qs}`);
      setOrders(res.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load orders");
    } finally {
      setLoading(false);
    }
  }, [state]);

  useEffect(() => {
    void load();
  }, [load]);

  async function runAction(orderId: string, action: string) {
    setBusyId(orderId);
    setNotice(null);
    try {
      await api(`/api/commerce/orders/${orderId}/action`, {
        method: "POST",
        json: { action },
      });
      setNotice({ tone: "ok", text: `Order ${action.toLowerCase().replace("_", " ")}d.` });
      await load();
    } catch (err) {
      setNotice({ tone: "err", text: err instanceof Error ? err.message : "Action failed" });
    } finally {
      setBusyId(null);
    }
  }

  return (
    <AppShell>
      <div className="space-y-5">
        <PageHeader
          title="Orders"
          description="Counter and online orders in one lifecycle — accept, prepare, and complete customer orders here."
          icon={ClipboardList}
        />

        {notice ? (
          <div
            className={cn(
              "rounded-xl border px-4 py-3 text-sm",
              notice.tone === "ok"
                ? "border-[#bbf7d0] bg-[#f0fdf4] text-[#15803d]"
                : "border-[#fecaca] bg-[#fef2f2] text-[#b91c1c]"
            )}
          >
            {notice.text}
          </div>
        ) : null}

        <div className="flex flex-wrap gap-2">
          {STATE_FILTERS.map((s) => (
            <button
              key={s || "all"}
              type="button"
              className={`rounded-full border px-3 py-1 text-xs font-semibold ${
                state === s ? "border-primary bg-[#eff6ff] text-primary" : "border-border bg-white text-text-secondary"
              }`}
              onClick={() => setState(s)}
            >
              {s ? s.replace(/_/g, " ").toLowerCase() : "all"}
            </button>
          ))}
        </div>

        {loading ? <LoadingState /> : null}
        {error && !loading ? <ErrorState message={error} onRetry={() => void load()} /> : null}

        {!loading && !error ? (
          orders.length === 0 ? (
            <EmptyState
              title="No orders here"
              description="Counter orders appear after checkout; online orders appear when customers order through your store link."
            />
          ) : (
            <div className="card overflow-x-auto p-0">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-text-muted">
                    <th className="px-4 py-3">Order</th>
                    <th className="px-4 py-3">Channel</th>
                    <th className="px-4 py-3">Customer</th>
                    <th className="px-4 py-3">Items</th>
                    <th className="px-4 py-3">Total</th>
                    <th className="px-4 py-3">State</th>
                    <th className="px-4 py-3"></th>
                  </tr>
                </thead>
                <tbody>
                  {orders.map((o) => {
                    const online = o.channel !== "POS";
                    const actions = ACTIONS[o.state] ?? [];
                    return (
                      <tr key={o.id} className="border-b border-border/60">
                        <td className="px-4 py-3">
                          <p className="font-semibold">{o.id.slice(0, 8)}…</p>
                          <p className="text-xs text-text-muted">{new Date(o.created_at).toLocaleString("en-IN")}</p>
                        </td>
                        <td className="px-4 py-3">
                          <span
                            className={cn(
                              "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-bold",
                              online ? "bg-[#eff6ff] text-primary" : "bg-[#f1f5f9] text-text-secondary"
                            )}
                          >
                            {online ? <Globe className="h-3 w-3" /> : <Store className="h-3 w-3" />}
                            {o.channel.replace(/_/g, " ").toLowerCase()}
                          </span>
                        </td>
                        <td className="px-4 py-3">{o.customer_name || "Walk-in"}</td>
                        <td className="px-4 py-3">{o.units} units · {o.item_lines} lines</td>
                        <td className="px-4 py-3 font-semibold">{formatINR(Number(o.total))}</td>
                        <td className="px-4 py-3">
                          <StatusBadge label={o.state.replace(/_/g, " ").toLowerCase()} tone={statusTone(o.state.toLowerCase())} />
                        </td>
                        <td className="px-4 py-3 text-right">
                          <div className="flex justify-end gap-1.5">
                            {online && actions.length > 0
                              ? actions.map((a) => (
                                  <button
                                    key={a.action}
                                    type="button"
                                    className="btn btn-primary px-2.5 py-1 text-xs"
                                    disabled={busyId === o.id}
                                    onClick={() => void runAction(o.id, a.action)}
                                  >
                                    {busyId === o.id ? "…" : a.label}
                                  </button>
                                ))
                              : null}
                            {online && o.state === "PAID" ? (
                              <button
                                type="button"
                                className="btn btn-secondary px-2.5 py-1 text-xs"
                                disabled={busyId === o.id}
                                onClick={() => void runAction(o.id, "REJECT")}
                              >
                                Reject
                              </button>
                            ) : null}
                            <Link className="btn btn-secondary py-1 text-xs" href={`/checkout/${o.id}`}>
                              {o.sale_id ? "Receipt" : "Open"}
                            </Link>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )
        ) : null}
      </div>
    </AppShell>
  );
}
