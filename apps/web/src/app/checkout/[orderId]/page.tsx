"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import {
  AlertTriangle,
  ArrowLeft,
  Ban,
  CheckCircle2,
  CircleDashed,
  Clock,
  Loader2,
  Printer,
  ReceiptText,
  RefreshCw,
  Users,
  XCircle,
} from "lucide-react";
import { AppShell, AuthGuard } from "@/components/shell";
import { PageHeader } from "@/components/page-header";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { StatusBadge, statusTone } from "@/components/status-badge";
import { api } from "@/lib/api";
import { formatINR } from "@/lib/types";
import { cn } from "@/lib/cn";

type Issue = { code: string; message: string; product?: string; available?: number; requested?: number };
type OrderItem = { product_id: string; name: string; quantity: number; unit_price: number; line_total: number };
type Order = {
  id: string;
  state: string;
  payment_method: string;
  cart_discount: number;
  total: number;
  sale_id: string | null;
  created_at: string;
  customer_name: string | null;
  customer_phone: string | null;
  items: OrderItem[] | null;
};
type Payment = { id: string; amount: number; state: string; method: string; provider: string; error_note: string | null };
type Split = { id: string; payer_label: string; amount: number; state: string; position: number };
type SplitGroup = {
  id: string;
  total_amount: number;
  paid_amount: number;
  remaining_amount: number;
  mode: string;
  status: string;
  completed: boolean;
  splits: Split[];
};

export default function CheckoutPage() {
  return (
    <AuthGuard>
      <Checkout />
    </AuthGuard>
  );
}

function Checkout() {
  const router = useRouter();
  const params = useParams<{ orderId: string }>();
  const orderId = params?.orderId;

  const [order, setOrder] = useState<Order | null>(null);
  const [reval, setReval] = useState<{ issues: Issue[]; valid: boolean; subtotal_current: number; total_current: number } | null>(null);
  const [payments, setPayments] = useState<Payment[]>([]);
  const [split, setSplit] = useState<SplitGroup | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [banner, setBanner] = useState<{ tone: "success" | "error" | "info"; text: string } | null>(null);

  // Split builder state
  const [splitMode, setSplitMode] = useState<"EQUAL" | "CUSTOM">("EQUAL");
  const [splitCount, setSplitCount] = useState(2);
  const [customSplits, setCustomSplits] = useState<{ label: string; amount: string }[]>([
    { label: "", amount: "" },
    { label: "", amount: "" },
  ]);

  const load = useCallback(async () => {
    if (!orderId) return;
    setLoading(true);
    setError(null);
    try {
      const o = await api<{ order: Order; revalidation: { issues: Issue[]; valid: boolean; subtotal_current: number; total_current: number } }>(
        `/api/orders/${orderId}`
      );
      setOrder(o.order);
      setReval(o.revalidation);
      const p = await api<{ items: Payment[]; paid_total: number; split_group: SplitGroup | null }>(
        `/api/payments/order/${orderId}`
      );
      setPayments(p.items);
      setSplit(p.split_group);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load order");
    } finally {
      setLoading(false);
    }
  }, [orderId]);

  useEffect(() => {
    void load();
  }, [load]);

  const paidTotal = payments.filter((p) => p.state === "PAID").reduce((s, p) => s + Number(p.amount), 0);
  const total = reval?.total_current ?? Number(order?.total ?? 0);
  const remaining = Math.max(0, total - paidTotal);
  const completed = order?.state === "COMPLETED";
  const blockingIssues = reval?.issues ?? [];
  const isSplit = order?.payment_method === "split";

  async function createPayment(amount: number, idempotencyKey?: string): Promise<Payment> {
    const res = await api<{ payment: Payment }>("/api/payments", {
      method: "POST",
      json: {
        order_id: orderId,
        amount,
        method: order?.payment_method ?? "cash",
        idempotency_key: idempotencyKey,
      },
    });
    return res.payment;
  }

  /** Cash / UPI / card in person: create + immediately confirm (manual provider). */
  async function payFull() {
    setBusy("pay");
    setBanner(null);
    try {
      const key = `full:${orderId}`;
      const payment = await createPayment(remaining, key);
      await api(`/api/payments/${payment.id}/confirm`, {
        method: "POST",
        json: { note: "Confirmed at counter" },
      });
      await load();
      setBanner({ tone: "success", text: "Payment confirmed. Order completed." });
    } catch (err) {
      setBanner({ tone: "error", text: err instanceof Error ? err.message : "Payment failed" });
    } finally {
      setBusy(null);
    }
  }

  async function createSplit() {
    setBusy("split");
    setBanner(null);
    try {
      let splits: { payer_label: string; amount: number }[];
      if (splitMode === "EQUAL") {
        const each = Math.floor((total / splitCount) * 100) / 100;
        splits = Array.from({ length: splitCount }, (_, i) => ({
          payer_label: `Person ${i + 1}`,
          amount: i === splitCount - 1 ? Math.round((total - each * (splitCount - 1)) * 100) / 100 : each,
        }));
      } else {
        splits = customSplits.map((s, i) => ({
          payer_label: s.label.trim() || `Person ${i + 1}`,
          amount: Number(s.amount) || 0,
        }));
      }
      await api("/api/payments/splits", {
        method: "POST",
        json: { order_id: orderId, mode: splitMode, splits },
      });
      await load();
      setBanner({ tone: "info", text: "Split created. Collect each part — the order completes when all are confirmed." });
    } catch (err) {
      setBanner({ tone: "error", text: err instanceof Error ? err.message : "Split failed" });
    } finally {
      setBusy(null);
    }
  }

  async function paySplit(splitId: string, label: string) {
    setBusy(`split-${splitId}`);
    setBanner(null);
    try {
      await api(`/api/payments/splits/${splitId}/pay`, { method: "POST", json: {} });
      await load();
      setBanner({ tone: "success", text: `${label} paid.` });
    } catch (err) {
      setBanner({ tone: "error", text: err instanceof Error ? err.message : "Split payment failed" });
    } finally {
      setBusy(null);
    }
  }

  async function cancelOrder() {
    if (!confirm("Cancel this order? Unpaid orders only — paid orders follow the refund workflow.")) return;
    setBusy("cancel");
    setBanner(null);
    try {
      await api(`/api/orders/${orderId}/cancel`, { method: "POST", json: { reason: "Cancelled at counter" } });
      await load();
      setBanner({ tone: "info", text: "Order cancelled. No payment was captured." });
    } catch (err) {
      setBanner({ tone: "error", text: err instanceof Error ? err.message : "Cancellation failed" });
    } finally {
      setBusy(null);
    }
  }

  if (loading) {
    return (
      <AuthGuard>
        <AppShell>
          <LoadingState />
        </AppShell>
      </AuthGuard>
    );
  }
  if (error || !order) {
    return (
      <AuthGuard>
        <AppShell>
          <ErrorState message={error || "Order not found"} onRetry={() => void load()} />
        </AppShell>
      </AuthGuard>
    );
  }

  const items = order.items ?? [];

  return (
    <AppShell>
      <div className="mx-auto max-w-3xl space-y-5">
        <PageHeader
          title={completed ? "Receipt" : "Checkout"}
          description={
            completed
              ? `Order ${order.id.slice(0, 8)} completed.`
              : `Order ${order.id.slice(0, 8)} · ${new Date(order.created_at).toLocaleString("en-IN")}`
          }
          icon={completed ? ReceiptText : ReceiptText}
          action={
            <button type="button" className="btn btn-ghost" onClick={() => router.push("/orders")}>
              <ArrowLeft className="h-4 w-4" />
              All orders
            </button>
          }
        />

        {banner ? (
          <div
            className={cn(
              "flex items-center gap-2 rounded-xl border px-4 py-3 text-sm",
              banner.tone === "success"
                ? "border-[#bbf7d0] bg-[#f0fdf4] text-[#15803d]"
                : banner.tone === "error"
                  ? "border-[#fecaca] bg-[#fef2f2] text-[#b91c1c]"
                  : "border-[#bfdbfe] bg-[#eff6ff] text-[#1d4ed8]"
            )}
          >
            {banner.tone === "success" ? <CheckCircle2 className="h-4 w-4" /> : banner.tone === "error" ? <XCircle className="h-4 w-4" /> : <CircleDashed className="h-4 w-4" />}
            {banner.text}
          </div>
        ) : null}

        {/* Revalidation issues — never silently proceed */}
        {!completed && blockingIssues.length > 0 ? (
          <div className="rounded-xl border border-[#fde68a] bg-[#fffbeb] p-4">
            <p className="flex items-center gap-2 text-sm font-bold text-[#b45309]">
              <AlertTriangle className="h-4 w-4" />
              Review required before payment
            </p>
            <ul className="mt-2 space-y-1.5 text-sm text-[#92400e]">
              {blockingIssues.map((i, idx) => (
                <li key={idx}>{i.message}</li>
              ))}
            </ul>
            <p className="mt-2 text-xs text-[#b45309]">
              Adjust the bill at the Smart Counter (stock changed at the counter is normal during the
              day) — this order cannot complete while these issues remain.
            </p>
            <button
              type="button"
              className="btn btn-secondary mt-3"
              onClick={() => router.push("/smart-counter")}
            >
              Back to Smart Counter
            </button>
          </div>
        ) : null}

        {/* ORDER SUMMARY */}
        <div className="card p-5">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-base font-bold">Order summary</h2>
            <StatusBadge label={order.state.replace("_", " ").toLowerCase()} tone={statusTone(order.state.toLowerCase())} />
          </div>
          {items.length === 0 ? (
            <EmptyState title="No items" description="This order has no items." />
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-text-muted">
                  <th className="py-2">Product</th>
                  <th className="py-2 text-center">Qty</th>
                  <th className="py-2 text-right">Unit price</th>
                  <th className="py-2 text-right">Total</th>
                </tr>
              </thead>
              <tbody>
                {items.map((it) => (
                  <tr key={it.product_id} className="border-b border-border/60">
                    <td className="py-2 font-medium">{it.name}</td>
                    <td className="py-2 text-center">{it.quantity}</td>
                    <td className="py-2 text-right">{formatINR(Number(it.unit_price))}</td>
                    <td className="py-2 text-right font-semibold">{formatINR(Number(it.line_total))}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <div className="mt-3 space-y-1 text-sm">
            <div className="flex justify-between text-text-secondary">
              <span>Subtotal</span>
              <span>{formatINR(reval?.subtotal_current ?? total + Number(order.cart_discount))}</span>
            </div>
            <div className="flex justify-between text-text-secondary">
              <span>Discount</span>
              <span>- {formatINR(Number(order.cart_discount))}</span>
            </div>
            <div className="flex justify-between border-t border-border pt-2 text-base font-bold">
              <span>Total</span>
              <span>{formatINR(total)}</span>
            </div>
          </div>
          <div className="mt-3 rounded-xl bg-[#f8fafc] p-3 text-xs text-text-secondary">
            <p>
              <span className="font-semibold">Customer:</span> {order.customer_name || "Walk-in"}
              {order.customer_phone ? ` · ${order.customer_phone}` : ""}
            </p>
            <p>
              <span className="font-semibold">Payment method:</span> {order.payment_method.toUpperCase()} ·{" "}
              <span className="font-semibold">Store currency:</span> INR
            </p>
          </div>
        </div>

        {/* RECEIPT STATE */}
        {completed ? (
          <div className="card p-5">
            <div className="flex items-center gap-2 text-sm font-bold text-[#15803d]">
              <CheckCircle2 className="h-5 w-5" />
              Payment successful — order completed
            </div>
            <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1.5 text-sm">
              <dt className="text-text-muted">Order ID</dt>
              <dd className="font-semibold">{order.id}</dd>
              <dt className="text-text-muted">Sale recorded</dt>
              <dd className="font-semibold">{order.sale_id?.slice(0, 8)}</dd>
              <dt className="text-text-muted">Total paid</dt>
              <dd className="font-semibold">{formatINR(paidTotal)}</dd>
              <dt className="text-text-muted">Payment method</dt>
              <dd className="font-semibold">{order.payment_method.toUpperCase()}</dd>
              <dt className="text-text-muted">Inventory</dt>
              <dd className="font-semibold">Deducted at sale completion</dd>
            </dl>
            <div className="mt-4 flex flex-wrap gap-2">
              <button type="button" className="btn btn-primary" onClick={() => router.push("/smart-counter")}>
                New sale
              </button>
              <button type="button" className="btn btn-secondary" onClick={() => window.print()}>
                <Printer className="h-4 w-4" />
                Print receipt
              </button>
              <button type="button" className="btn btn-ghost" onClick={() => router.push("/orders")}>
                View orders
              </button>
            </div>
          </div>
        ) : (
          <>
            {/* PAYMENT STATE */}
            <div className="card p-5">
              <div className="mb-3 flex items-center justify-between">
                <h2 className="text-base font-bold">Payment</h2>
                <span className="text-xs text-text-muted">
                  Paid {formatINR(paidTotal)} · Remaining {formatINR(remaining)}
                </span>
              </div>

              {payments.length > 0 ? (
                <ul className="mb-3 space-y-2">
                  {payments.map((p) => (
                    <li key={p.id} className="flex items-center justify-between rounded-xl border border-border px-3 py-2 text-sm">
                      <span className="font-semibold">{formatINR(Number(p.amount))} · {p.method.toUpperCase()}</span>
                      <span className="flex items-center gap-2">
                        {p.error_note ? <span className="text-xs text-[#b91c1c]">{p.error_note}</span> : null}
                        <StatusBadge
                          label={p.state === "PENDING" ? "verifying" : p.state.toLowerCase()}
                          tone={p.state === "PAID" ? "success" : p.state === "FAILED" ? "danger" : "info"}
                        />
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mb-3 text-sm text-text-muted">No payments recorded yet.</p>
              )}

              {isSplit ? (
                <>
                  {split ? (
                    <div className="space-y-2">
                      <p className="text-sm text-text-secondary">
                        Split ({split.mode.toLowerCase()}) — {split.splits.length} participants
                      </p>
                      {split.splits.map((s) => (
                        <div key={s.id} className="flex items-center justify-between rounded-xl border border-border px-3 py-2">
                          <div>
                            <p className="text-sm font-semibold">{s.payer_label}</p>
                            <p className="text-xs text-text-muted">{formatINR(Number(s.amount))}</p>
                          </div>
                          {s.state === "PAID" ? (
                            <StatusBadge label="paid" tone="success" />
                          ) : s.state === "FAILED" ? (
                            <div className="flex items-center gap-2">
                              <StatusBadge label="failed" tone="danger" />
                              <button
                                type="button"
                                className="btn btn-secondary py-1 text-xs"
                                disabled={busy !== null}
                                onClick={() => void paySplit(s.id, s.payer_label)}
                              >
                                Retry
                              </button>
                            </div>
                          ) : (
                            <button
                              type="button"
                              className="btn btn-primary py-1 text-xs"
                              disabled={busy !== null}
                              onClick={() => void paySplit(s.id, s.payer_label)}
                            >
                              {busy === `split-${s.id}` ? (
                                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                              ) : (
                                <Users className="h-3.5 w-3.5" />
                              )}
                              Confirm received
                            </button>
                          )}
                        </div>
                      ))}
                      <p className="text-xs text-text-muted">
                        The order stays PARTIALLY_PAID until every participant's amount is confirmed.
                      </p>
                    </div>
                  ) : (
                    <div className="space-y-3">
                      <div className="flex gap-2">
                        {(["EQUAL", "CUSTOM"] as const).map((m) => (
                          <button
                            key={m}
                            type="button"
                            className={cn(
                              "rounded-xl border px-3 py-1.5 text-sm font-semibold",
                              splitMode === m ? "border-primary bg-[#eff6ff] text-primary" : "border-border bg-white text-text-secondary"
                            )}
                            onClick={() => setSplitMode(m)}
                          >
                            {m === "EQUAL" ? "Equal split" : "Custom amounts"}
                          </button>
                        ))}
                      </div>
                      {splitMode === "EQUAL" ? (
                        <label className="block text-sm">
                          <span className="mb-1 block font-medium">Number of people</span>
                          <input
                            className="input w-28"
                            type="number"
                            min={2}
                            max={20}
                            value={splitCount}
                            onChange={(e) => setSplitCount(Math.max(2, Math.min(20, Number(e.target.value) || 2)))}
                          />
                          <span className="mt-1 block text-xs text-text-muted">
                            {formatINR(total)} / {splitCount} ≈ {formatINR(Math.round((total / splitCount) * 100) / 100)} each
                          </span>
                        </label>
                      ) : (
                        <div className="space-y-2">
                          {customSplits.map((s, i) => (
                            <div key={i} className="flex gap-2">
                              <input
                                className="input"
                                placeholder={`Person ${i + 1} name`}
                                value={s.label}
                                onChange={(e) => setCustomSplits(customSplits.map((c, j) => (j === i ? { ...c, label: e.target.value } : c)))}
                              />
                              <input
                                className="input w-32"
                                type="number"
                                min="0"
                                step="0.01"
                                placeholder="₹ amount"
                                value={s.amount}
                                onChange={(e) => setCustomSplits(customSplits.map((c, j) => (j === i ? { ...c, amount: e.target.value } : c)))}
                              />
                            </div>
                          ))}
                          <button
                            type="button"
                            className="btn btn-ghost text-xs"
                            onClick={() => setCustomSplits([...customSplits, { label: "", amount: "" }])}
                          >
                            + Add person
                          </button>
                          <p className="text-xs text-text-muted">
                            Amounts must sum to exactly {formatINR(total)}.
                          </p>
                        </div>
                      )}
                      <button type="button" className="btn btn-primary w-full justify-center" disabled={busy !== null} onClick={() => void createSplit()}>
                        {busy === "split" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Users className="h-4 w-4" />}
                        Create split for {formatINR(total)}
                      </button>
                    </div>
                  )}
                </>
              ) : (
                /* Cash / UPI / card — manual provider confirmation at the counter */
                <div className="space-y-3">
                  <p className="text-sm text-text-secondary">
                    {order.payment_method === "cash"
                      ? "Collect cash and confirm the amount received."
                      : order.payment_method === "upi"
                        ? "Show your UPI QR / accept the transfer, then confirm the amount received."
                        : "Process the card on your machine, then confirm the amount received."}
                  </p>
                  <button type="button" className="btn btn-primary w-full justify-center" disabled={busy !== null || blockingIssues.length > 0} onClick={() => void payFull()}>
                    {busy === "pay" ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
                    Confirm {formatINR(remaining)} received
                  </button>
                  <p className="text-[11px] text-text-muted">
                    Payment provider: <span className="font-semibold">manual (in-person)</span>. Online
                    provider verification (Razorpay/Paytm etc.) activates once provider credentials are
                    configured — the app never fakes a provider confirmation.
                  </p>
                </div>
              )}
            </div>

            {/* CANCEL */}
            {["DRAFT", "PENDING_PAYMENT", "PAYMENT_FAILED"].includes(order.state) ? (
              <button type="button" className="btn btn-ghost w-full justify-center text-[#b91c1c]" disabled={busy !== null} onClick={() => void cancelOrder()}>
                <Ban className="h-4 w-4" />
                Cancel order
              </button>
            ) : null}
          </>
        )}
      </div>
    </AppShell>
  );
}
