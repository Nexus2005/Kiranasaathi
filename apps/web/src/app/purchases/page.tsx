"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  CircleCheck,
  ClipboardList,
  PackagePlus,
  Plus,
  Truck,
  XCircle,
} from "lucide-react";
import { AppShell, AuthGuard } from "@/components/shell";
import { PageHeader } from "@/components/page-header";
import { EmptyState, ErrorState, LoadingState, SuccessBanner } from "@/components/states";
import { StatusBadge } from "@/components/status-badge";
import { api } from "@/lib/api";
import {
  formatDateTime,
  formatINR,
  type Product,
  type PurchaseOrder,
  type Supplier,
} from "@/lib/types";

type Tab = "orders" | "suppliers";

const PO_TONE: Record<string, "green" | "amber" | "gray"> = {
  received: "green",
  pending: "amber",
  cancelled: "gray",
};

export default function PurchasesPage() {
  return (
    <AuthGuard>
      <PurchasesScreen />
    </AuthGuard>
  );
}

function PurchasesScreen() {
  const [tab, setTab] = useState<Tab>("orders");
  const [orders, setOrders] = useState<PurchaseOrder[]>([]);
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [showSupplierForm, setShowSupplierForm] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [po, sup, prod] = await Promise.all([
        api<{ items: PurchaseOrder[] }>("/api/purchases"),
        api<{ items: Supplier[] }>("/api/suppliers"),
        api<{ items: Product[] }>("/api/products"),
      ]);
      setOrders(po.items);
      setSuppliers(sup.items);
      setProducts(prod.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load purchases");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function receive(po: PurchaseOrder) {
    if (busyId) return;
    setBusyId(po.id);
    setError(null);
    try {
      await api(`/api/purchases/${po.id}/receive`, { method: "POST" });
      setSuccess(
        `Purchase received — inventory and batches updated for ${po.items.length} product line(s).`
      );
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to receive purchase");
    } finally {
      setBusyId(null);
    }
  }

  async function cancel(po: PurchaseOrder) {
    if (busyId) return;
    setBusyId(po.id);
    try {
      await api(`/api/purchases/${po.id}/cancel`, { method: "POST" });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to cancel purchase");
    } finally {
      setBusyId(null);
    }
  }

  const pendingCount = orders.filter((o) => o.status === "pending").length;

  return (
    <AppShell>
      <div className="space-y-5">
        <PageHeader
          title="Purchases & Suppliers"
          description="Create purchase orders, receive stock into batches, and manage supplier records."
          icon={Truck}
          action={
            <button type="button" className="btn btn-primary" onClick={() => setShowForm((v) => !v)}>
              <Plus className="h-4 w-4" />
              New purchase
            </button>
          }
        />

        {success ? <SuccessBanner message={success} /> : null}

        <div className="flex gap-2">
          {(
            [
              ["orders", `Purchase orders (${pendingCount} pending)`],
              ["suppliers", `Suppliers (${suppliers.length})`],
            ] as const
          ).map(([t, label]) => (
            <button
              key={t}
              type="button"
              className={`btn ${tab === t ? "btn-primary" : "btn-ghost"}`}
              onClick={() => setTab(t)}
            >
              {label}
            </button>
          ))}
        </div>

        {tab === "orders" ? (
          <OrdersTab
            orders={orders}
            products={products}
            suppliers={suppliers}
            loading={loading}
            error={error}
            onRetry={() => void load()}
            showForm={showForm}
            setShowForm={setShowForm}
            onDone={async (msg) => {
              setSuccess(msg);
              setShowForm(false);
              await load();
            }}
            onReceive={receive}
            onCancel={cancel}
            busyId={busyId}
          />
        ) : (
          <SuppliersTab
            suppliers={suppliers}
            loading={loading}
            error={error}
            onRetry={() => void load()}
            showForm={showSupplierForm}
            setShowForm={setShowSupplierForm}
            onDone={async (msg) => {
              setSuccess(msg);
              setShowSupplierForm(false);
              await load();
            }}
          />
        )}
      </div>
    </AppShell>
  );
}

type LineDraft = { product_id: string; quantity: string; unit_cost: string; expiry_date: string };

function OrdersTab({
  orders,
  products,
  suppliers,
  loading,
  error,
  onRetry,
  showForm,
  setShowForm,
  onDone,
  onReceive,
  onCancel,
  busyId,
}: {
  orders: PurchaseOrder[];
  products: Product[];
  suppliers: Supplier[];
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  showForm: boolean;
  setShowForm: (v: boolean) => void;
  onDone: (msg: string) => Promise<void>;
  onReceive: (po: PurchaseOrder) => Promise<void>;
  onCancel: (po: PurchaseOrder) => Promise<void>;
  busyId: string | null;
}) {
  const [supplierId, setSupplierId] = useState("");
  const [invoiceNo, setInvoiceNo] = useState("");
  const [lines, setLines] = useState<LineDraft[]>([
    { product_id: "", quantity: "1", unit_cost: "", expiry_date: "" },
  ]);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const estimatedTotal = useMemo(
    () =>
      lines.reduce(
        (sum, l) => sum + (Number(l.quantity) || 0) * (Number(l.unit_cost) || 0),
        0
      ),
    [lines]
  );

  async function onCreate(e: FormEvent) {
    e.preventDefault();
    setFormError(null);
    if (!supplierId) {
      setFormError("Select a supplier.");
      return;
    }
    if (lines.some((l) => !l.product_id || Number(l.quantity) <= 0 || Number(l.unit_cost) < 0)) {
      setFormError("Every line needs a product, a positive quantity and a unit cost.");
      return;
    }
    setBusy(true);
    try {
      const res = await api<{ id: string }>("/api/purchases", {
        method: "POST",
        json: {
          supplier_id: supplierId,
          invoice_no: invoiceNo || null,
          items: lines.map((l) => ({
            product_id: l.product_id,
            quantity: Number(l.quantity),
            unit_cost: Number(l.unit_cost),
            expiry_date: l.expiry_date || null,
          })),
        },
      });
      await onDone(
        `Purchase order created (est. ${formatINR(estimatedTotal)}). Inventory updates when you receive it.`
      );
      setLines([{ product_id: "", quantity: "1", unit_cost: "", expiry_date: "" }]);
      setInvoiceNo("");
      setSupplierId("");
      void res;
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Failed to create purchase");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      {showForm ? (
        <form onSubmit={onCreate} className="card space-y-4 p-5">
          <h2 className="flex items-center gap-2 text-base font-bold">
            <ClipboardList className="h-4 w-4 text-primary" />
            New purchase order
          </h2>
          {formError ? (
            <div className="rounded-xl border border-[#fecaca] bg-[#fef2f2] px-3 py-2 text-sm text-[#b91c1c]">
              {formError}
            </div>
          ) : null}
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block text-sm">
              <span className="mb-1 block font-medium">Supplier *</span>
              <select className="input" required value={supplierId} onChange={(e) => setSupplierId(e.target.value)}>
                <option value="">Select supplier…</option>
                {suppliers.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="block text-sm">
              <span className="mb-1 block font-medium">Invoice / reference</span>
              <input className="input" value={invoiceNo} onChange={(e) => setInvoiceNo(e.target.value)} placeholder="e.g. INV-2041" />
            </label>
          </div>

          <div className="space-y-2">
            <span className="block text-sm font-medium">Products</span>
            {lines.map((line, idx) => (
              <div key={idx} className="grid gap-2 rounded-xl border border-border p-3 sm:grid-cols-[2fr_1fr_1fr_1fr_auto]">
                <select
                  className="input"
                  value={line.product_id}
                  onChange={(e) => {
                    const next = [...lines];
                    next[idx] = { ...line, product_id: e.target.value };
                    const p = products.find((p) => p.id === e.target.value);
                    if (p && !line.unit_cost) next[idx].unit_cost = String(p.purchase_price);
                    setLines(next);
                  }}
                  aria-label="Product"
                >
                  <option value="">Select product…</option>
                  {products.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
                <input
                  className="input"
                  type="number"
                  min="1"
                  placeholder="Qty"
                  value={line.quantity}
                  onChange={(e) => {
                    const next = [...lines];
                    next[idx] = { ...line, quantity: e.target.value };
                    setLines(next);
                  }}
                  aria-label="Quantity"
                />
                <input
                  className="input"
                  type="number"
                  min="0"
                  step="0.01"
                  placeholder="Unit cost ₹"
                  value={line.unit_cost}
                  onChange={(e) => {
                    const next = [...lines];
                    next[idx] = { ...line, unit_cost: e.target.value };
                    setLines(next);
                  }}
                  aria-label="Unit cost"
                />
                <input
                  className="input"
                  type="date"
                  value={line.expiry_date}
                  onChange={(e) => {
                    const next = [...lines];
                    next[idx] = { ...line, expiry_date: e.target.value };
                    setLines(next);
                  }}
                  aria-label="Expiry date"
                />
                <button
                  type="button"
                  className="btn btn-ghost h-9 w-9 p-0"
                  onClick={() => setLines(lines.filter((_, i) => i !== idx))}
                  disabled={lines.length === 1}
                  aria-label="Remove line"
                >
                  <XCircle className="h-4 w-4" />
                </button>
              </div>
            ))}
            <button
              type="button"
              className="btn btn-ghost text-xs"
              onClick={() => setLines([...lines, { product_id: "", quantity: "1", unit_cost: "", expiry_date: "" }])}
            >
              <Plus className="h-3.5 w-3.5" />
              Add another product
            </button>
          </div>

          <div className="flex items-center justify-between border-t border-border pt-3">
            <span className="text-sm text-text-secondary">
              Estimated total: <strong>{formatINR(estimatedTotal)}</strong>
            </span>
            <div className="flex gap-2">
              <button type="button" className="btn btn-ghost" onClick={() => setShowForm(false)} disabled={busy}>
                Cancel
              </button>
              <button type="submit" className="btn btn-primary" disabled={busy}>
                {busy ? "Saving…" : "Create purchase order"}
              </button>
            </div>
          </div>
          <p className="text-[11px] text-text-muted">
            Creating a purchase does not change stock — receiving it does (batches, inventory and
            cost history update together).
          </p>
        </form>
      ) : null}

      {loading ? <LoadingState /> : null}
      {error && !loading ? <ErrorState message={error} onRetry={onRetry} /> : null}

      {!loading && !error ? (
        orders.length === 0 ? (
          <EmptyState
            title="No purchase orders yet"
            description="Create your first purchase order to bring stock in with batches and expiry."
          />
        ) : (
          <div className="space-y-3">
            {orders.map((po) => (
              <div key={po.id} className="card p-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <p className="flex items-center gap-2 text-sm font-bold">
                      {po.supplier_name || "Unknown supplier"}
                      <StatusBadge label={po.status} tone={PO_TONE[po.status] ?? "gray"} />
                    </p>
                    <p className="text-[11px] text-text-muted">
                      {formatDateTime(po.created_at)}
                      {po.invoice_no ? ` · ${po.invoice_no}` : ""} · {po.line_count} line(s),{" "}
                      {po.unit_count} units
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-bold">{formatINR(po.total_amount || 0)}</span>
                    {po.status === "pending" ? (
                      <>
                        <button
                          type="button"
                          className="btn btn-primary text-xs"
                          onClick={() => void onReceive(po)}
                          disabled={busyId === po.id}
                        >
                          <CircleCheck className="h-3.5 w-3.5" />
                          {busyId === po.id ? "Receiving…" : "Receive stock"}
                        </button>
                        <button
                          type="button"
                          className="btn btn-ghost text-xs"
                          onClick={() => void onCancel(po)}
                          disabled={busyId === po.id}
                        >
                          Cancel
                        </button>
                      </>
                    ) : null}
                  </div>
                </div>
                {po.items.length > 0 ? (
                  <ul className="mt-3 space-y-1 border-t border-border pt-2 text-[12px] text-text-secondary">
                    {po.items.map((it) => (
                      <li key={`${po.id}-${it.product_id}`} className="flex justify-between gap-2">
                        <span>
                          {it.name} × {it.quantity}
                          {it.expiry_date
                            ? ` · exp ${new Date(it.expiry_date).toLocaleDateString("en-IN", {
                                day: "numeric",
                                month: "short",
                                year: "numeric",
                              })}`
                            : ""}
                        </span>
                        <span>{formatINR(it.line_total)}</span>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </div>
            ))}
          </div>
        )
      ) : null}
    </div>
  );
}

function SuppliersTab({
  suppliers,
  loading,
  error,
  onRetry,
  showForm,
  setShowForm,
  onDone,
}: {
  suppliers: Supplier[];
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  showForm: boolean;
  setShowForm: (v: boolean) => void;
  onDone: (msg: string) => Promise<void>;
}) {
  const [form, setForm] = useState({
    name: "",
    phone: "",
    address: "",
    categories: "",
    lead_time_days: "",
    payment_terms: "",
  });
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  async function onCreate(e: FormEvent) {
    e.preventDefault();
    setFormError(null);
    setBusy(true);
    try {
      await api("/api/suppliers", {
        method: "POST",
        json: {
          name: form.name,
          phone: form.phone || null,
          address: form.address || null,
          categories: form.categories || null,
          lead_time_days: form.lead_time_days ? Number(form.lead_time_days) : null,
          payment_terms: form.payment_terms || null,
        },
      });
      await onDone(`Supplier "${form.name}" added.`);
      setForm({ name: "", phone: "", address: "", categories: "", lead_time_days: "", payment_terms: "" });
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Failed to add supplier");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      {showForm ? (
        <form onSubmit={onCreate} className="card space-y-3 p-5">
          <h2 className="text-base font-bold">Add supplier</h2>
          {formError ? (
            <div className="rounded-xl border border-[#fecaca] bg-[#fef2f2] px-3 py-2 text-sm text-[#b91c1c]">
              {formError}
            </div>
          ) : null}
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <label className="block text-sm">
              <span className="mb-1 block font-medium">Name *</span>
              <input className="input" required minLength={2} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </label>
            <label className="block text-sm">
              <span className="mb-1 block font-medium">Phone</span>
              <input className="input" value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} />
            </label>
            <label className="block text-sm">
              <span className="mb-1 block font-medium">Categories supplied</span>
              <input className="input" placeholder="e.g. Beverages, Snacks" value={form.categories} onChange={(e) => setForm({ ...form, categories: e.target.value })} />
            </label>
            <label className="block text-sm">
              <span className="mb-1 block font-medium">Lead time (days)</span>
              <input className="input" type="number" min="0" value={form.lead_time_days} onChange={(e) => setForm({ ...form, lead_time_days: e.target.value })} />
            </label>
            <label className="block text-sm">
              <span className="mb-1 block font-medium">Payment terms</span>
              <input className="input" placeholder="e.g. 15 days credit" value={form.payment_terms} onChange={(e) => setForm({ ...form, payment_terms: e.target.value })} />
            </label>
            <label className="block text-sm">
              <span className="mb-1 block font-medium">Address</span>
              <input className="input" value={form.address} onChange={(e) => setForm({ ...form, address: e.target.value })} />
            </label>
          </div>
          <div className="flex gap-2">
            <button type="submit" className="btn btn-primary" disabled={busy}>
              {busy ? "Saving…" : "Add supplier"}
            </button>
            <button type="button" className="btn btn-ghost" onClick={() => setShowForm(false)} disabled={busy}>
              Cancel
            </button>
          </div>
        </form>
      ) : null}

      {loading ? <LoadingState /> : null}
      {error && !loading ? <ErrorState message={error} onRetry={onRetry} /> : null}

      {!loading && !error ? (
        suppliers.length === 0 ? (
          <EmptyState
            title="No suppliers yet"
            description="Add suppliers to compare prices and track purchase history."
            action={
              <button type="button" className="btn btn-primary" onClick={() => setShowForm(true)}>
                <PackagePlus className="h-4 w-4" />
                Add supplier
              </button>
            }
          />
        ) : (
          <div className="card table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Supplier</th>
                  <th>Categories</th>
                  <th>Lead time</th>
                  <th>Orders</th>
                  <th>Total purchased</th>
                  <th>Last purchase</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {suppliers.map((s) => (
                  <tr key={s.id}>
                    <td>
                      <div className="font-semibold">{s.name}</div>
                      <div className="text-[11px] text-text-muted">{s.phone || "—"}</div>
                    </td>
                    <td className="text-text-secondary">{s.categories || "—"}</td>
                    <td>{s.lead_time_days != null ? `${s.lead_time_days}d` : "—"}</td>
                    <td>
                      {s.received_orders}
                      {s.pending_orders > 0 ? (
                        <span className="ml-1 text-[11px] text-[#b45309]">(+{s.pending_orders} pending)</span>
                      ) : null}
                    </td>
                    <td>{s.has_history ? formatINR(Number(s.total_value)) : "—"}</td>
                    <td className="text-text-secondary">
                      {s.last_purchase_at ? formatDateTime(s.last_purchase_at) : "—"}
                    </td>
                    <td>
                      {s.has_history ? (
                        <StatusBadge label="active" tone="green" />
                      ) : (
                        <span className="text-[11px] text-text-muted">
                          Not enough purchase history to evaluate
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      ) : null}
    </div>
  );
}
