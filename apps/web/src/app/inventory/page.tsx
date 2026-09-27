"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  Boxes,
  CalendarClock,
  CircleDollarSign,
  Package,
  PackagePlus,
  Plus,
  Search,
  Skull,
  TrendingUp,
} from "lucide-react";
import { AppShell, AuthGuard } from "@/components/shell";
import { AddInventoryModal } from "@/components/add-inventory-modal";
import { ProductImage } from "@/components/product-image";
import { PageHeader } from "@/components/page-header";
import { MetricCard } from "@/components/metric-card";
import { EmptyState, ErrorState, LoadingState, SuccessBanner } from "@/components/states";
import { StatusBadge } from "@/components/status-badge";
import { api } from "@/lib/api";
import { formatINR, type InventoryIntel, type InventoryIntelItem } from "@/lib/types";
import { cn } from "@/lib/cn";

const STOCK_LABEL: Record<string, { label: string; tone: "green" | "amber" | "red" | "blue" | "gray" }> = {
  HEALTHY: { label: "healthy", tone: "green" },
  LOW_STOCK: { label: "low stock", tone: "amber" },
  CRITICAL_STOCK: { label: "critical", tone: "red" },
  OVERSTOCKED: { label: "overstocked", tone: "blue" },
  SLOW_MOVING: { label: "slow moving", tone: "blue" },
  DEAD_STOCK: { label: "dead stock", tone: "gray" },
  OUT_OF_STOCK: { label: "out of stock", tone: "red" },
};

const EXPIRY_LABEL: Record<string, { label: string; tone: "green" | "amber" | "red" | "gray" }> = {
  HEALTHY_SHELF_LIFE: { label: "shelf life ok", tone: "green" },
  EXPIRING_SOON: { label: "expiring soon", tone: "amber" },
  EXPIRING_CRITICAL: { label: "expiring critical", tone: "red" },
  EXPIRED: { label: "expired", tone: "gray" },
  NO_EXPIRY: { label: "no expiry", tone: "gray" },
};

type Filter = "all" | "low" | "expiry" | "dead";

function daysInStock(days: number | null): string {
  if (days == null) return "—";
  if (days > 999) return "999+";
  return `${days}d`;
}

export default function InventoryPage() {
  return (
    <AuthGuard>
      <InventoryScreen />
    </AuthGuard>
  );
}

function InventoryScreen() {
  const [intel, setIntel] = useState<InventoryIntel | null>(null);
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [showAdd, setShowAdd] = useState(false);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const [form, setForm] = useState({
    name: "",
    category: "Snacks",
    sku: "",
    unit: "pack",
    mrp: "",
    selling_price: "",
    purchase_price: "",
    reorder_level: "10",
    initial_stock: "0",
    expiry_date: "",
  });

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api<InventoryIntel>(
        `/api/inventory/intelligence?q=${encodeURIComponent(q)}`
      );
      setIntel(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load inventory");
    } finally {
      setLoading(false);
    }
  }, [q]);

  useEffect(() => {
    const t = setTimeout(() => void load(), 250);
    return () => clearTimeout(t);
  }, [load]);

  async function onCreate(e: FormEvent) {
    e.preventDefault();
    setFormError(null);
    setBusy(true);
    try {
      const payload = {
        name: form.name,
        category: form.category,
        sku: form.sku || null,
        unit: form.unit,
        mrp: Number(form.mrp),
        selling_price: Number(form.selling_price),
        purchase_price: Number(form.purchase_price),
        reorder_level: Number(form.reorder_level),
        initial_stock: Number(form.initial_stock),
        expiry_date: form.expiry_date || null,
      };
      if (payload.selling_price < payload.purchase_price) {
        throw new Error("Selling price cannot be below purchase price");
      }
      await api("/api/products", { method: "POST", json: payload });
      setSuccess(`Product "${payload.name}" created with ${payload.initial_stock} in stock.`);
      setForm({
        name: "",
        category: "Snacks",
        sku: "",
        unit: "pack",
        mrp: "",
        selling_price: "",
        purchase_price: "",
        reorder_level: "10",
        initial_stock: "0",
        expiry_date: "",
      });
      setShowForm(false);
      await load();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Failed to create product");
    } finally {
      setBusy(false);
    }
  }

  const filtered: InventoryIntelItem[] = (intel?.items ?? []).filter((i) => {
    if (filter === "low")
      return ["LOW_STOCK", "CRITICAL_STOCK", "OUT_OF_STOCK"].includes(i.stock_status);
    if (filter === "expiry")
      return ["EXPIRING_SOON", "EXPIRING_CRITICAL", "EXPIRED"].includes(i.expiry_status);
    if (filter === "dead") return ["DEAD_STOCK", "SLOW_MOVING"].includes(i.stock_status);
    return true;
  });

  const s = intel?.summary;

  return (
    <AppShell>
      <div className="space-y-5">
        <PageHeader
          title="Inventory"
          description="Live stock intelligence: valuation, expiry risk, velocity and reorder needs."
          icon={Boxes}
          action={
            <div className="flex items-center gap-2">
              <button type="button" className="btn btn-primary" onClick={() => setShowAdd(true)}>
                <Plus className="h-4 w-4" />
                Add products
              </button>
              <button type="button" className="btn btn-secondary" onClick={() => setShowForm((v) => !v)}>
                New product form
              </button>
            </div>
          }
        />

        {success ? <SuccessBanner message={success} /> : null}

        <AddInventoryModal
          open={showAdd}
          onClose={() => setShowAdd(false)}
          onDone={(msg) => {
            setSuccess(msg);
            void load();
          }}
        />

        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <MetricCard
            icon={CircleDollarSign}
            label="Stock value (cost)"
            value={formatINR(s?.total_cost_value ?? 0)}
            hint={`Sales value ${formatINR(s?.total_sales_value ?? 0)}`}
            tone="purple"
          />
          <MetricCard
            icon={TrendingUp}
            label="Potential margin"
            value={formatINR(s?.potential_gross_margin ?? 0)}
            hint="If all stock sells at current price"
            tone="green"
          />
          <MetricCard
            icon={CalendarClock}
            label="At-risk (expiry)"
            value={formatINR(s?.at_risk_value ?? 0)}
            hint={
              s && s.expired_value > 0
                ? `${formatINR(s.expired_value)} already expired`
                : "Inside expiry warning window"
            }
            tone="orange"
          />
          <MetricCard
            icon={Skull}
            label="Dead stock"
            value={formatINR(s?.dead_stock_value ?? 0)}
            hint={`${s?.status_counts["DEAD_STOCK"] ?? 0} product(s) with no sales`}
            tone="blue"
          />
        </div>

        {showForm ? (
          <form onSubmit={onCreate} className="card space-y-4 p-5">
            <h2 className="text-base font-bold">New product</h2>
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
                <span className="mb-1 block font-medium">Category</span>
                <input className="input" value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })} />
              </label>
              <label className="block text-sm">
                <span className="mb-1 block font-medium">SKU</span>
                <input className="input" value={form.sku} onChange={(e) => setForm({ ...form, sku: e.target.value })} />
              </label>
              <label className="block text-sm">
                <span className="mb-1 block font-medium">Unit</span>
                <input className="input" value={form.unit} onChange={(e) => setForm({ ...form, unit: e.target.value })} />
              </label>
              <label className="block text-sm">
                <span className="mb-1 block font-medium">MRP (₹) *</span>
                <input className="input" type="number" min="0" step="0.01" required value={form.mrp} onChange={(e) => setForm({ ...form, mrp: e.target.value })} />
              </label>
              <label className="block text-sm">
                <span className="mb-1 block font-medium">Selling price *</span>
                <input className="input" type="number" min="0" step="0.01" required value={form.selling_price} onChange={(e) => setForm({ ...form, selling_price: e.target.value })} />
              </label>
              <label className="block text-sm">
                <span className="mb-1 block font-medium">Purchase cost *</span>
                <input className="input" type="number" min="0" step="0.01" required value={form.purchase_price} onChange={(e) => setForm({ ...form, purchase_price: e.target.value })} />
              </label>
              <label className="block text-sm">
                <span className="mb-1 block font-medium">Reorder level</span>
                <input className="input" type="number" min="0" value={form.reorder_level} onChange={(e) => setForm({ ...form, reorder_level: e.target.value })} />
              </label>
              <label className="block text-sm">
                <span className="mb-1 block font-medium">Initial stock</span>
                <input className="input" type="number" min="0" value={form.initial_stock} onChange={(e) => setForm({ ...form, initial_stock: e.target.value })} />
              </label>
              <label className="block text-sm">
                <span className="mb-1 block font-medium">Expiry date (optional)</span>
                <input className="input" type="date" value={form.expiry_date} onChange={(e) => setForm({ ...form, expiry_date: e.target.value })} />
              </label>
            </div>
            <div className="flex gap-2">
              <button type="submit" className="btn btn-primary" disabled={busy}>
                {busy ? "Saving…" : "Create product"}
              </button>
              <button type="button" className="btn btn-ghost" onClick={() => setShowForm(false)} disabled={busy}>
                Cancel
              </button>
            </div>
          </form>
        ) : null}

        <div className="flex flex-wrap items-center gap-2">
          <div className="relative min-w-[220px] flex-1">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" />
            <input
              className="input pl-9"
              placeholder="Search products…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              aria-label="Search products"
            />
          </div>
          {(
            [
              ["all", "All products"],
              ["low", "Needs reorder"],
              ["expiry", "Expiry risk"],
              ["dead", "Slow / dead"],
            ] as const
          ).map(([f, label]) => (
            <button
              key={f}
              type="button"
              className={`btn ${filter === f ? "btn-primary" : "btn-ghost"}`}
              onClick={() => setFilter(f)}
            >
              {label}
            </button>
          ))}
        </div>

        {loading ? <LoadingState /> : null}
        {error && !loading ? <ErrorState message={error} onRetry={() => void load()} /> : null}

        {!loading && !error ? (
          filtered.length === 0 ? (
            <EmptyState
              title="No products found"
              description={
                q
                  ? "Try a different search."
                  : filter === "expiry"
                    ? "No products currently require expiry action."
                    : filter === "dead"
                      ? "No slow-moving or dead stock — healthy inventory."
                      : "Add your first product to start tracking inventory."
              }
              action={
                filter === "all" ? (
                  <button type="button" className="btn btn-primary" onClick={() => setShowForm(true)}>
                    Add product
                  </button>
                ) : undefined
              }
            />
          ) : (
            <div className="card table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Product</th>
                    <th>Stock</th>
                    <th>Days left</th>
                    <th>Expiry</th>
                    <th>Velocity</th>
                    <th>Cost</th>
                    <th>Price</th>
                    <th>Margin</th>
                    <th>Stock value</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((i) => {
                    const stock = STOCK_LABEL[i.stock_status] ?? { label: i.stock_status, tone: "gray" as const };
                    const expiry = EXPIRY_LABEL[i.expiry_status] ?? { label: i.expiry_status, tone: "gray" as const };
                    return (
                      <tr key={i.product_id}>
                        <td>
                          <div className="flex items-center gap-2.5">
                            <ProductImage
                              src={i.image_url}
                              alt={i.name}
                              className="h-10 w-10 shrink-0 rounded-lg border border-border"
                            />
                            <div>
                              <div className="font-semibold">{i.name}</div>
                              <div className="text-[11px] text-text-muted">
                                {i.category}
                                {i.sku ? ` · ${i.sku}` : ""}
                              </div>
                            </div>
                          </div>
                        </td>
                        <td className="font-semibold">
                          {i.sellable_quantity}
                          {i.expired_quantity > 0 ? (
                            <span className="ml-1 text-[11px] font-normal text-text-muted">
                              (+{i.expired_quantity} expired)
                            </span>
                          ) : null}
                        </td>
                        <td className={cn(i.days_of_stock == null && "text-text-muted")}>
                          {daysInStock(i.days_of_stock)}
                        </td>
                        <td>
                          {i.next_expiry ? (
                            <div>
                              <StatusBadge label={expiry.label} tone={expiry.tone} />
                              <div className="mt-0.5 text-[11px] text-text-muted">
                                {new Date(i.next_expiry).toLocaleDateString("en-IN", {
                                  day: "numeric",
                                  month: "short",
                                })}
                              </div>
                            </div>
                          ) : (
                            <span className="text-text-muted">—</span>
                          )}
                        </td>
                        <td className={cn("text-text-secondary", i.velocity == null && "text-text-muted")}>
                          {i.velocity != null ? `${Number(i.velocity).toFixed(1)}/day` : "no data"}
                        </td>
                        <td>{formatINR(i.purchase_price)}</td>
                        <td>{formatINR(i.selling_price)}</td>
                        <td className={cn(i.margin_pct < 10 && "font-semibold text-[#b45309]")}>{i.margin_pct}%</td>
                        <td>{formatINR(i.stock_value_cost)}</td>
                        <td>
                          <StatusBadge label={stock.label} tone={stock.tone} />
                          {i.reorder_required ? (
                            <div className="mt-1 flex items-center gap-1 text-[10px] font-semibold text-primary">
                              <AlertTriangle className="h-3 w-3" />
                              reorder ~{i.reorder_point}
                            </div>
                          ) : null}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )
        ) : null}

        <p className="flex items-center gap-1.5 text-[11px] text-text-muted">
          <Package className="h-3 w-3" />
          Stock value uses batch-weighted purchase cost. Days-left is an estimate at the current
          sales pace; products without recent sales show no estimate.
        </p>
      </div>
    </AppShell>
  );
}
