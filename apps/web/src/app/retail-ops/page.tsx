"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  ClipboardCheck,
  PackageOpen,
  ScanLine,
  SlidersHorizontal,
  TriangleAlert,
} from "lucide-react";
import { AppShell, AuthGuard } from "@/components/shell";
import { PageHeader } from "@/components/page-header";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { StatusBadge, statusTone } from "@/components/status-badge";
import { api } from "@/lib/api";
import { formatINR, type Product } from "@/lib/types";
import { cn } from "@/lib/cn";

type PoLine = {
  product_id: string;
  name: string;
  ordered: number;
  quantity_received: number;
  quantity_damaged: number;
  quantity_rejected: number;
  remaining: number;
  unit_cost: number;
};
type PoStatus = {
  po: { id: string; status: string; supplier_name: string | null; created_at: string };
  lines: PoLine[];
};
type Batch = {
  id: string;
  batch_no: string | null;
  quantity: number;
  purchase_cost: number;
  expiry_date: string | null;
  manufacturing_date: string | null;
  status: string;
  days_remaining: number | null;
  supplier_name: string | null;
};

type Tab = "receive" | "count" | "adjust";

const TABS: { id: Tab; label: string; icon: typeof PackageOpen }[] = [
  { id: "receive", label: "Receive stock", icon: PackageOpen },
  { id: "count", label: "Cycle count", icon: ClipboardCheck },
  { id: "adjust", label: "Adjust stock", icon: SlidersHorizontal },
];

const REASONS = ["DAMAGE", "WASTAGE", "SHRINKAGE", "COUNTING_ERROR", "EXPIRED", "OTHER"] as const;

export default function RetailOpsPage() {
  return (
    <AuthGuard>
      <RetailOps />
    </AuthGuard>
  );
}

function RetailOps() {
  const [tab, setTab] = useState<Tab>("receive");
  return (
    <AppShell>
      <div className="space-y-5">
        <PageHeader
          title="Retail Operations"
          description="Receive supplier shipments, count stock, and record adjustments — every change is audited."
          icon={PackageOpen}
        />
        <div className="flex gap-2">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              className={cn(
                "flex items-center gap-2 rounded-xl border px-4 py-2 text-sm font-semibold",
                tab === t.id ? "border-primary bg-[#eff6ff] text-primary" : "border-border bg-white text-text-secondary"
              )}
              onClick={() => setTab(t.id)}
            >
              <t.icon className="h-4 w-4" />
              {t.label}
            </button>
          ))}
        </div>
        {tab === "receive" ? <ReceivePanel /> : tab === "count" ? <CountPanel /> : <AdjustPanel />}
      </div>
    </AppShell>
  );
}

/* ================= RECEIVE ================= */

function ReceivePanel() {
  const [poId, setPoId] = useState("");
  const [status, setStatus] = useState<PoStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [banner, setBanner] = useState<{ tone: "ok" | "err" | "info"; text: string } | null>(null);
  // one draft line per PO line
  const [draft, setDraft] = useState<Record<string, { qty: string; level: string; batch: string; expiry: string; mfd: string; slv: string; slu: string; damaged: string }>>({});
  const [scanCode, setScanCode] = useState("");
  const [scanResult, setScanResult] = useState<{ result: string; name?: string; batch?: string | null; expiry?: string | null } | null>(null);

  const loadPo = useCallback(async (id: string) => {
    if (!id.trim()) return;
    setLoading(true);
    setError(null);
    setStatus(null);
    setDraft({});
    try {
      const res = await api<PoStatus>(`/api/retail/purchase-orders/${id.trim()}/receiving`);
      setStatus(res);
      const init: typeof draft = {};
      for (const ln of res.lines) {
        init[ln.product_id] = { qty: "", level: "EACH", batch: "", expiry: "", mfd: "", slv: "", slu: "", damaged: "0" };
      }
      setDraft(init);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Purchase order not found");
    } finally {
      setLoading(false);
    }
  }, []);

  async function scan(e: FormEvent) {
    e.preventDefault();
    setScanResult(null);
    try {
      const res = await api<{ result: string; product?: { name: string }; gs1?: { batch?: string; expiry_date?: string } | null }>(
        "/api/retail/scan",
        { method: "POST", json: { code: scanCode.trim() } }
      );
      if (res.result === "FOUND" && res.product) {
        setScanResult({ result: "FOUND", name: res.product.name, batch: res.gs1?.batch ?? null, expiry: res.gs1?.expiry_date ?? null });
        // prefill the matching PO line if present
        const pid = (res.product as unknown as { product_id?: string }).product_id;
        if (pid && draft[pid]) {
          setDraft((d) => ({
            ...d,
            [pid]: {
              ...d[pid],
              batch: res.gs1?.batch ?? d[pid].batch,
              expiry: res.gs1?.expiry_date?.slice(0, 10) ?? d[pid].expiry,
            },
          }));
        }
        setScanCode("");
      } else {
        setScanResult({ result: res.result });
      }
    } catch {
      setScanResult({ result: "NOT_FOUND" });
    }
  }

  async function receive() {
    if (!status) return;
    const lines = status.lines
      .map((ln) => {
        const d = draft[ln.product_id];
        const qty = Number(d?.qty || 0);
        if (qty <= 0 && Number(d?.damaged || 0) <= 0) return null;
        return {
          product_id: ln.product_id,
          level: d?.level || "EACH",
          qty,
          unit_cost: Number(ln.unit_cost),
          batch_no: d?.batch || null,
          expiry_date: d?.expiry || null,
          manufacturing_date: d?.mfd || null,
          shelf_life_value: d?.slv ? Number(d.slv) : null,
          shelf_life_unit: d?.slv ? d.slu : null,
          damaged: Number(d?.damaged || 0),
        };
      })
      .filter(Boolean) as Record<string, unknown>[];
    if (lines.length === 0) {
      setBanner({ tone: "err", text: "Enter a quantity for at least one line." });
      return;
    }
    setBusy(true);
    setBanner(null);
    try {
      const res = await api<{ units_received: number; status: string; conversions: { cases: number; units: number }[] }>(
        "/api/retail/receive",
        { method: "POST", json: { purchase_order_id: status.po.id, lines, idempotency_key: `ui-recv-${status.po.id}-${Date.now()}` } }
      );
      const conv = res.conversions?.length
        ? ` (${res.conversions.map((c) => `${c.cases} case(s) → ${c.units} units`).join(", ")})`
        : "";
      setBanner({ tone: "ok", text: `Received ${res.units_received} units${conv}. PO is now ${res.status.replace("_", " ")}.` });
      await loadPo(status.po.id);
    } catch (err) {
      setBanner({ tone: "err", text: err instanceof Error ? err.message : "Receiving failed — nothing was changed." });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="card p-5">
        <label className="block text-sm">
          <span className="mb-1 block font-medium">Purchase order ID</span>
          <div className="flex gap-2">
            <input className="input" placeholder="Paste a pending PO id…" value={poId} onChange={(e) => setPoId(e.target.value)} />
            <button type="button" className="btn btn-secondary" onClick={() => void loadPo(poId)} disabled={loading}>
              Load
            </button>
          </div>
        </label>

        <form onSubmit={scan} className="mt-3 flex gap-2">
          <div className="relative flex-1">
            <ScanLine className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" />
            <input
              className="input pl-9"
              placeholder="Scan product barcode or GS1 code (fills batch/expiry)…"
              value={scanCode}
              onChange={(e) => setScanCode(e.target.value)}
            />
          </div>
          <button type="submit" className="btn btn-secondary">
            <ScanLine className="h-4 w-4" />
            Scan
          </button>
        </form>
        {scanResult ? (
          <p className={cn("mt-2 text-xs", scanResult.result === "FOUND" ? "text-[#15803d]" : "text-[#b91c1c]")}>
            {scanResult.result === "FOUND"
              ? `Scanned: ${scanResult.name}${scanResult.batch ? ` · batch ${scanResult.batch}` : ""}${scanResult.expiry ? ` · expiry ${scanResult.expiry}` : ""}`
              : "Barcode not recognised in this store."}
          </p>
        ) : null}
      </div>

      {loading ? <LoadingState /> : null}
      {error && !loading ? <ErrorState message={error} /> : null}

      {status ? (
        <div className="card p-5">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-base font-bold">
              {status.po.supplier_name || "Supplier"} — {new Date(status.po.created_at).toLocaleDateString("en-IN")}
            </h2>
            <StatusBadge label={status.po.status.replace("_", " ")} tone={statusTone(status.po.status === "partially_received" ? "pending" : status.po.status)} />
          </div>
          {banner ? (
            <div
              className={cn(
                "mb-3 rounded-xl border px-3 py-2 text-sm",
                banner.tone === "ok"
                  ? "border-[#bbf7d0] bg-[#f0fdf4] text-[#15803d]"
                  : banner.tone === "err"
                    ? "border-[#fecaca] bg-[#fef2f2] text-[#b91c1c]"
                    : "border-[#bfdbfe] bg-[#eff6ff] text-[#1d4ed8]"
              )}
            >
              {banner.text}
            </div>
          ) : null}
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-text-muted">
                  <th className="py-2">Product</th>
                  <th className="py-2 text-center">Ordered</th>
                  <th className="py-2 text-center">Received</th>
                  <th className="py-2 text-center">Remaining</th>
                  <th className="py-2">Receive now</th>
                </tr>
              </thead>
              <tbody>
                {status.lines.map((ln) => {
                  const d = draft[ln.product_id];
                  const unitsPreview = d && d.level !== "EACH" && d.qty ? `${d.qty} ${d.level.toLowerCase()}(s) — confirm at receive` : null;
                  return (
                    <tr key={ln.product_id} className="border-b border-border/60 align-top">
                      <td className="py-2 font-medium">{ln.name}</td>
                      <td className="py-2 text-center">{ln.ordered}</td>
                      <td className="py-2 text-center">{ln.quantity_received}{ln.quantity_damaged ? ` (+${ln.quantity_damaged} damaged)` : ""}</td>
                      <td className="py-2 text-center font-semibold">{ln.remaining}</td>
                      <td className="py-2">
                        {ln.remaining <= 0 ? (
                          <span className="text-xs text-text-muted">Complete</span>
                        ) : (
                          <div className="grid grid-cols-2 gap-1.5">
                            <input
                              className="input h-8 py-1 text-xs"
                              type="number"
                              min="0"
                              placeholder="Qty"
                              value={d?.qty || ""}
                              onChange={(e) => setDraft((prev) => ({ ...prev, [ln.product_id]: { ...prev[ln.product_id], qty: e.target.value } }))}
                            />
                            <select
                              className="input h-8 py-1 text-xs"
                              value={d?.level || "EACH"}
                              onChange={(e) => setDraft((prev) => ({ ...prev, [ln.product_id]: { ...prev[ln.product_id], level: e.target.value } }))}
                            >
                              <option value="EACH">units</option>
                              <option value="CASE">cases</option>
                              <option value="PACK">packs</option>
                            </select>
                            <input
                              className="input h-8 py-1 text-xs"
                              placeholder="Batch"
                              value={d?.batch || ""}
                              onChange={(e) => setDraft((prev) => ({ ...prev, [ln.product_id]: { ...prev[ln.product_id], batch: e.target.value } }))}
                            />
                            <input
                              className="input h-8 py-1 text-xs"
                              type="date"
                              title="Expiry (or fill MFD + shelf life)"
                              value={d?.expiry || ""}
                              onChange={(e) => setDraft((prev) => ({ ...prev, [ln.product_id]: { ...prev[ln.product_id], expiry: e.target.value } }))}
                            />
                            <input
                              className="input h-8 py-1 text-xs"
                              type="date"
                              title="Manufacturing date (expiry auto-calculated when shelf life is set)"
                              value={d?.mfd || ""}
                              onChange={(e) => setDraft((prev) => ({ ...prev, [ln.product_id]: { ...prev[ln.product_id], mfd: e.target.value } }))}
                            />
                            <div className="flex gap-1">
                              <input
                                className="input h-8 py-1 text-xs"
                                type="number"
                                min="0"
                                placeholder="Shelf life"
                                title="Shelf life value (e.g. 24)"
                                value={d?.slv || ""}
                                onChange={(e) => setDraft((prev) => ({ ...prev, [ln.product_id]: { ...prev[ln.product_id], slv: e.target.value } }))}
                              />
                              <select
                                className="input h-8 w-20 py-1 text-xs"
                                value={d?.slu || "month"}
                                onChange={(e) => setDraft((prev) => ({ ...prev, [ln.product_id]: { ...prev[ln.product_id], slu: e.target.value } }))}
                              >
                                <option value="day">days</option>
                                <option value="week">weeks</option>
                                <option value="month">months</option>
                                <option value="year">years</option>
                              </select>
                            </div>
                            <input
                              className="input h-8 py-1 text-xs"
                              type="number"
                              min="0"
                              placeholder="Damaged"
                              value={d?.damaged || "0"}
                              onChange={(e) => setDraft((prev) => ({ ...prev, [ln.product_id]: { ...prev[ln.product_id], damaged: e.target.value } }))}
                            />
                            {unitsPreview ? <p className="col-span-2 text-[10px] text-text-muted">{unitsPreview}</p> : null}
                          </div>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <button type="button" className="btn btn-primary mt-4 w-full justify-center" disabled={busy} onClick={() => void receive()}>
            {busy ? "Receiving…" : "Confirm receiving"}
          </button>
          <p className="mt-2 text-[11px] text-text-muted">
            Expiry is calculated on the backend (explicit date, or MFD + shelf life). Damaged units are quarantined and never
            sellable. Partial receipts keep the PO open.
          </p>
        </div>
      ) : null}
    </div>
  );
}

/* ================= CYCLE COUNT ================= */

function CountPanel() {
  const [products, setProducts] = useState<Product[]>([]);
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [q, setQ] = useState("");
  const [countId, setCountId] = useState<string | null>(null);
  const [lines, setLines] = useState<{ product_id: string; product_name: string; expected_quantity: number; counted_quantity: number; variance: number; reason: string | null }[]>([]);
  const [counts, setCounts] = useState<Record<string, string>>({});
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [banner, setBanner] = useState<{ tone: "ok" | "err" | "info"; text: string } | null>(null);
  const [loading, setLoading] = useState(true);

  const loadProducts = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api<{ items: Product[] }>("/api/products");
      setProducts(res.items);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadProducts();
  }, [loadProducts]);

  const filtered = useMemo(() => {
    const t = q.trim().toLowerCase();
    if (!t) return products.slice(0, 30);
    return products.filter((p) => p.name.toLowerCase().includes(t)).slice(0, 30);
  }, [products, q]);

  async function startCount() {
    const ids = Object.entries(selected).filter(([, v]) => v).map(([k]) => k);
    if (ids.length === 0) {
      setBanner({ tone: "err", text: "Select at least one product to count." });
      return;
    }
    setBusy(true);
    setBanner(null);
    try {
      const res = await api<{ cycle_count_id: string }>("/api/retail/cycle-counts", {
        method: "POST",
        json: { product_ids: ids, scope_note: "Counter session" },
      });
      const detail = await api<{ lines: typeof lines }>(`/api/retail/cycle-counts/${res.cycle_count_id}`);
      setCountId(res.cycle_count_id);
      setLines(detail.lines);
      setBanner({ tone: "info", text: "Count started. Enter physical quantities and a reason for every difference." });
    } catch (err) {
      setBanner({ tone: "err", text: err instanceof Error ? err.message : "Could not start count" });
    } finally {
      setBusy(false);
    }
  }

  async function completeCount() {
    if (!countId) return;
    setBusy(true);
    setBanner(null);
    try {
      for (const ln of lines) {
        const counted = counts[ln.product_id];
        if (counted === undefined || counted === "") continue;
        await api(`/api/retail/cycle-counts/${countId}/lines`, {
          method: "POST",
          json: { product_id: ln.product_id, counted_quantity: Number(counted), reason: reasons[ln.product_id] || null },
        });
      }
      const res = await api<{ applied_adjustments: number }>(`/api/retail/cycle-counts/${countId}/complete`, { method: "POST", json: {} });
      setBanner({ tone: "ok", text: `Count completed — ${res.applied_adjustments} adjustment(s) recorded with reasons and audit events.` });
      setCountId(null);
      setLines([]);
      setCounts({});
      setReasons({});
      await loadProducts();
    } catch (err) {
      setBanner({ tone: "err", text: err instanceof Error ? err.message : "Completion failed" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      {banner ? (
        <div
          className={cn(
            "rounded-xl border px-4 py-3 text-sm",
            banner.tone === "ok"
              ? "border-[#bbf7d0] bg-[#f0fdf4] text-[#15803d]"
              : banner.tone === "err"
                ? "border-[#fecaca] bg-[#fef2f2] text-[#b91c1c]"
                : "border-[#bfdbfe] bg-[#eff6ff] text-[#1d4ed8]"
          )}
        >
          {banner.text}
        </div>
      ) : null}

      {!countId ? (
        <div className="card p-5">
          <h2 className="mb-3 text-base font-bold">Select products to count</h2>
          <input className="input mb-3" placeholder="Search products…" value={q} onChange={(e) => setQ(e.target.value)} />
          {loading ? (
            <LoadingState />
          ) : (
            <div className="max-h-80 space-y-1.5 overflow-y-auto">
              {filtered.map((p) => (
                <label key={p.id} className="flex cursor-pointer items-center justify-between rounded-xl border border-border px-3 py-2 text-sm">
                  <span className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={!!selected[p.id]}
                      onChange={(e) => setSelected((s) => ({ ...s, [p.id]: e.target.checked }))}
                    />
                    {p.name}
                  </span>
                  <span className="text-xs text-text-muted">expected {p.quantity}</span>
                </label>
              ))}
            </div>
          )}
          <button type="button" className="btn btn-primary mt-3 w-full justify-center" disabled={busy} onClick={() => void startCount()}>
            Start count
          </button>
        </div>
      ) : (
        <div className="card p-5">
          <h2 className="mb-3 text-base font-bold">Count in progress</h2>
          <div className="space-y-2">
            {lines.map((ln) => {
              const counted = counts[ln.product_id] ?? "";
              const variance = counted !== "" ? Number(counted) - ln.expected_quantity : null;
              return (
                <div key={ln.product_id} className="rounded-xl border border-border p-3">
                  <div className="flex items-center justify-between">
                    <p className="text-sm font-semibold">{ln.product_name}</p>
                    <p className="text-xs text-text-muted">expected {ln.expected_quantity}</p>
                  </div>
                  <div className="mt-2 grid grid-cols-3 gap-2">
                    <input
                      className="input h-9 py-1 text-sm"
                      type="number"
                      min="0"
                      placeholder="Physical count"
                      value={counted}
                      onChange={(e) => setCounts((c) => ({ ...c, [ln.product_id]: e.target.value }))}
                    />
                    <select
                      className="input h-9 py-1 text-sm"
                      value={reasons[ln.product_id] || ""}
                      onChange={(e) => setReasons((r) => ({ ...r, [ln.product_id]: e.target.value }))}
                    >
                      <option value="">Reason (if variance)…</option>
                      {REASONS.map((r) => (
                        <option key={r} value={r}>
                          {r.replace("_", " ").toLowerCase()}
                        </option>
                      ))}
                    </select>
                    <p className={cn("flex items-center text-sm font-semibold", variance != null && variance !== 0 ? "text-[#b45309]" : "text-text-muted")}>
                      {variance == null ? "—" : variance === 0 ? "matches" : `${variance > 0 ? "+" : ""}${variance}`}
                    </p>
                  </div>
                </div>
              );
            })}
          </div>
          <button type="button" className="btn btn-primary mt-4 w-full justify-center" disabled={busy} onClick={() => void completeCount()}>
            {busy ? "Applying adjustments…" : "Complete count & apply adjustments"}
          </button>
        </div>
      )}
    </div>
  );
}

/* ================= ADJUST ================= */

function AdjustPanel() {
  const [products, setProducts] = useState<Product[]>([]);
  const [q, setQ] = useState("");
  const [picked, setPicked] = useState<Product | null>(null);
  const [change, setChange] = useState("");
  const [reason, setReason] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [banner, setBanner] = useState<{ tone: "ok" | "err"; text: string } | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api<{ items: Product[] }>("/api/products")
      .then((r) => setProducts(r.items))
      .finally(() => setLoading(false));
  }, []);

  const filtered = useMemo(() => {
    const t = q.trim().toLowerCase();
    if (!t) return [];
    return products.filter((p) => p.name.toLowerCase().includes(t)).slice(0, 8);
  }, [products, q]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!picked) return;
    setBusy(true);
    setBanner(null);
    try {
      const res = await api<{ quantity_after: number }>("/api/retail/adjustments", {
        method: "POST",
        json: { product_id: picked.id, change: Number(change), reason, note: note || null },
      });
      setBanner({ tone: "ok", text: `Adjusted — stock for ${picked.name} is now ${res.quantity_after}. Movement and audit recorded.` });
      setPicked(null);
      setChange("");
      setReason("");
      setNote("");
    } catch (err) {
      setBanner({ tone: "err", text: err instanceof Error ? err.message : "Adjustment failed" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card max-w-xl p-5">
      <h2 className="mb-3 text-base font-bold">Record a stock adjustment</h2>
      {banner ? (
        <div
          className={cn(
            "mb-3 rounded-xl border px-3 py-2 text-sm",
            banner.tone === "ok" ? "border-[#bbf7d0] bg-[#f0fdf4] text-[#15803d]" : "border-[#fecaca] bg-[#fef2f2] text-[#b91c1c]"
          )}
        >
          {banner.text}
        </div>
      ) : null}
      {loading ? (
        <LoadingState />
      ) : (
        <form onSubmit={submit} className="space-y-3">
          <div className="relative">
            <input
              className="input"
              placeholder="Find product to adjust…"
              value={picked?.name ?? q}
              onChange={(e) => {
                setPicked(null);
                setQ(e.target.value);
              }}
            />
            {!picked && filtered.length > 0 ? (
              <div className="absolute z-10 mt-1 w-full rounded-xl border border-border bg-white shadow-lg">
                {filtered.map((p) => (
                  <button
                    key={p.id}
                    type="button"
                    className="flex w-full items-center justify-between px-3 py-2 text-left text-sm hover:bg-[#f8fafc]"
                    onClick={() => {
                      setPicked(p);
                      setQ("");
                    }}
                  >
                    <span>{p.name}</span>
                    <span className="text-xs text-text-muted">stock {p.quantity}</span>
                  </button>
                ))}
              </div>
            ) : null}
          </div>

          {picked ? (
            <>
              <div className="rounded-xl bg-[#f8fafc] p-3 text-xs text-text-secondary">
                {picked.name} — current stock <span className="font-bold">{picked.quantity}</span>
              </div>
              <label className="block text-sm">
                <span className="mb-1 block font-medium">Change (+ add / − remove)</span>
                <input className="input" type="number" required value={change} onChange={(e) => setChange(e.target.value)} />
              </label>
              <label className="block text-sm">
                <span className="mb-1 block font-medium">Reason *</span>
                <select className="input" required value={reason} onChange={(e) => setReason(e.target.value)}>
                  <option value="">Select a reason…</option>
                  {REASONS.map((r) => (
                    <option key={r} value={r}>
                      {r.replace("_", " ").toLowerCase()}
                    </option>
                  ))}
                </select>
              </label>
              <label className="block text-sm">
                <span className="mb-1 block font-medium">Note (optional)</span>
                <input className="input" value={note} onChange={(e) => setNote(e.target.value)} />
              </label>
              {Number(change) < 0 && picked.quantity + Number(change) < 0 ? (
                <p className="flex items-center gap-1.5 text-xs text-[#b91c1c]">
                  <TriangleAlert className="h-3.5 w-3.5" />
                  This would take stock below zero — the backend will refuse it.
                </p>
              ) : null}
              <button type="submit" className="btn btn-primary w-full justify-center" disabled={busy || !reason || !change}>
                {busy ? "Applying…" : "Confirm adjustment"}
              </button>
            </>
          ) : null}
        </form>
      )}
    </div>
  );
}
