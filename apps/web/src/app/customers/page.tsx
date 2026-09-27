"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Search, UserPlus, Users } from "lucide-react";
import { AppShell, AuthGuard } from "@/components/shell";
import { PageHeader } from "@/components/page-header";
import { EmptyState, ErrorState, LoadingState, SuccessBanner } from "@/components/states";
import { api } from "@/lib/api";
import { formatDateTime, formatINR, type Customer } from "@/lib/types";

type SegmentsOverview = {
  total_customers: number;
  segment_counts: Record<string, number>;
  customers: {
    customer_id: string;
    segments: string[];
    days_since_last_purchase: number | null;
  }[];
};

function SegmentPill({
  label, count, tone, onClick, active,
}: {
  label: string; count: number; tone: string; onClick: () => void; active: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex items-center justify-between rounded-xl border px-4 py-3 text-left transition ${active ? "border-primary bg-[#e8f2ff]" : "border-border bg-white hover:bg-[#f8fafc]"}`}
    >
      <span className="text-sm font-medium text-text-secondary">{label}</span>
      <span className={`rounded-full px-2.5 py-0.5 text-sm font-bold ${tone}`}>{count}</span>
    </button>
  );
}

export default function CustomersPage() {
  return (
    <AuthGuard>
      <CustomersScreen />
    </AuthGuard>
  );
}

function CustomersScreen() {
  const router = useRouter();
  const [items, setItems] = useState<Customer[]>([]);
  const [segments, setSegments] = useState<SegmentsOverview | null>(null);
  const [segFilter, setSegFilter] = useState<string | null>(null);
  const [segInfo, setSegInfo] = useState<Record<string, { segments: string[] }>>({});
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [form, setForm] = useState({ name: "", phone: "" });

  useEffect(() => {
    api<SegmentsOverview>("/api/customers/intelligence/overview")
      .then((res) => {
        setSegments(res);
        const map: Record<string, { segments: string[] }> = {};
        for (const c of res.customers) map[c.customer_id] = { segments: c.segments };
        setSegInfo(map);
      })
      .catch(() => setSegments(null));
  }, [success]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const seg = segFilter ? `&segment=${encodeURIComponent(segFilter)}` : "";
      const res = await api<{ items: Customer[] }>(
        `/api/customers?q=${encodeURIComponent(q)}${seg}`
      );
      setItems(res.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load customers");
    } finally {
      setLoading(false);
    }
  }, [q, segFilter]);

  useEffect(() => {
    const t = setTimeout(() => void load(), 250);
    return () => clearTimeout(t);
  }, [load]);

  async function onCreate(e: FormEvent) {
    e.preventDefault();
    setFormError(null);
    setBusy(true);
    try {
      await api("/api/customers", {
        method: "POST",
        json: { name: form.name.trim(), phone: form.phone.trim() || null },
      });
      setSuccess(`Customer ${form.name.trim()} added.`);
      setForm({ name: "", phone: "" });
      setShowForm(false);
      await load();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Failed to add customer");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AppShell>
      <div className="space-y-5">
        <PageHeader
          title="Customers"
          description="Know your customers and purchase history."
          icon={Users}
          action={
            <button type="button" className="btn btn-primary" onClick={() => setShowForm((v) => !v)}>
              <UserPlus className="h-4 w-4" />
              Add customer
            </button>
          }
        />

        {success ? <SuccessBanner message={success} /> : null}

        {showForm ? (
          <form onSubmit={onCreate} className="card space-y-3 p-5">
            {formError ? (
              <div className="rounded-xl border border-[#fecaca] bg-[#fef2f2] px-3 py-2 text-sm text-[#b91c1c]">
                {formError}
              </div>
            ) : null}
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="block text-sm">
                <span className="mb-1 block font-medium">Name *</span>
                <input
                  className="input"
                  required
                  minLength={2}
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                />
              </label>
              <label className="block text-sm">
                <span className="mb-1 block font-medium">Phone</span>
                <input
                  className="input"
                  value={form.phone}
                  onChange={(e) => setForm({ ...form, phone: e.target.value })}
                  placeholder="+91…"
                />
              </label>
            </div>
            <div className="flex gap-2">
              <button type="submit" className="btn btn-primary" disabled={busy}>
                {busy ? "Saving…" : "Add customer"}
              </button>
              <button type="button" className="btn btn-ghost" onClick={() => setShowForm(false)} disabled={busy}>
                Cancel
              </button>
            </div>
          </form>
        ) : null}

        <div className="relative max-w-md">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" />
          <input
            className="input pl-9"
            placeholder="Search by name or phone…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            aria-label="Search customers"
          />
        </div>

        {loading ? <LoadingState /> : null}
        {error && !loading ? <ErrorState message={error} onRetry={() => void load()} /> : null}

        {!loading && !error && segments ? (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
            <SegmentPill label="Active" count={segments.segment_counts["ACTIVE_CUSTOMER"] || 0} tone="bg-[#dcfce7] text-[#16a34a]" onClick={() => setSegFilter("ACTIVE_CUSTOMER")} active={segFilter === "ACTIVE_CUSTOMER"} />
            <SegmentPill label="Repeat" count={segments.segment_counts["REPEAT_CUSTOMER"] || 0} tone="bg-[#e8f2ff] text-[#2080f0]" onClick={() => setSegFilter("REPEAT_CUSTOMER")} active={segFilter === "REPEAT_CUSTOMER"} />
            <SegmentPill label="Inactive 30d+" count={segments.segment_counts["INACTIVE_CUSTOMER"] || 0} tone="bg-[#ffedd5] text-[#f97316]" onClick={() => setSegFilter("INACTIVE_CUSTOMER")} active={segFilter === "INACTIVE_CUSTOMER"} />
            <SegmentPill label="High value" count={segments.segment_counts["HIGH_VALUE"] || 0} tone="bg-[#fef9c3] text-[#a16207]" onClick={() => setSegFilter("HIGH_VALUE")} active={segFilter === "HIGH_VALUE"} />
            <SegmentPill label="New (7d)" count={segments.segment_counts["NEW_CUSTOMER"] || 0} tone="bg-[#cffafe] text-[#0891b2]" onClick={() => setSegFilter("NEW_CUSTOMER")} active={segFilter === "NEW_CUSTOMER"} />
          </div>
        ) : null}

        {!loading && !error ? (
          items.length === 0 ? (
            <EmptyState
              title="No customers yet"
              description="Customer insights will appear after enough purchases are recorded."
              action={
                <button type="button" className="btn btn-primary" onClick={() => setShowForm(true)}>
                  Add customer
                </button>
              }
            />
          ) : (
            <div className="card table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Customer</th>
                    <th>Phone</th>
                    <th>Orders</th>
                    <th>Total spent</th>
                    <th>Last purchase</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((c) => {
                    const info = segInfo[c.id];
                    return (
                      <tr
                        key={c.id}
                        className="cursor-pointer transition hover:bg-[#f8fafc]"
                        onClick={() => router.push(`/customers/${c.id}`)}
                      >
                        <td className="font-semibold">
                          {c.name}
                          {info?.segments?.length ? (
                            <span className="ml-2 inline-flex flex-wrap gap-1 align-middle">
                              {info.segments.slice(0, 2).map((s: string) => (
                                <span key={s} className="rounded-full bg-[#f1f5f9] px-1.5 py-0.5 text-[9px] font-bold text-text-secondary">
                                  {s.replaceAll("_", " ").toLowerCase()}
                                </span>
                              ))}
                            </span>
                          ) : null}
                        </td>
                        <td className="text-text-secondary">{c.phone || "—"}</td>
                        <td>{c.orders}</td>
                        <td className="font-semibold">{formatINR(Number(c.total_spent))}</td>
                        <td className="text-text-secondary">
                          {c.last_purchase ? formatDateTime(c.last_purchase) : "—"}
                        </td>
                        <td className="text-right">
                          <span className="text-xs font-semibold text-primary">View →</span>
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
