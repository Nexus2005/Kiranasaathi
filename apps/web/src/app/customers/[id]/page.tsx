"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import {
  ArrowLeft,
  BadgeCheck,
  CalendarClock,
  Megaphone,
  Receipt,
  ShoppingBag,
  Sparkles,
  UserRound,
} from "lucide-react";
import { AppShell, AuthGuard } from "@/components/shell";
import { PageHeader } from "@/components/page-header";
import { MetricCard } from "@/components/metric-card";
import { EmptyState, ErrorState, LoadingState, SuccessBanner } from "@/components/states";
import { StatusBadge } from "@/components/status-badge";
import { api } from "@/lib/api";
import { formatDateTime, formatINR } from "@/lib/types";

type C360 = {
  customer: {
    id: string;
    name: string;
    phone: string | null;
    created_at: string | null;
    marketing_consent: boolean;
    consent_source: string | null;
    consent_timestamp: string | null;
    opt_out_timestamp: string | null;
    notes: string | null;
  };
  rfm: {
    orders: number;
    total_spent: number;
    avg_order_value: number;
    days_since_last_purchase: number | null;
    avg_purchase_interval_days: number | null;
    last_purchase: string | null;
    first_purchase: string | null;
  };
  segments: string[];
  favorite_products: { product_id: string; name: string; category: string; units: number; times_purchased: number; spend: number }[];
  favorite_categories: { category: string; units: number; spend: number }[];
  recent_orders: { id: string; total: number; created_at: string; payment_method: string; items: { name: string; quantity: number }[] }[];
  campaign_history: { campaign_id: string; campaign_name: string; type: string; status: string; sent_at: string | null }[];
  opportunity: { type: string; evidence: string } | null;
};

function segmentTone(seg: string): "success" | "warning" | "info" | "neutral" {
  if (seg.startsWith("INACTIVE") || seg.startsWith("RECENTLY_INACTIVE")) return "warning";
  if (seg.startsWith("HIGH_VALUE") || seg.startsWith("HIGH_FREQUENCY")) return "success";
  if (seg.startsWith("NEW")) return "info";
  return "neutral";
}

function prettySegment(seg: string) {
  if (seg.startsWith("CATEGORY_SPECIFIC:")) return `Loyal to ${seg.split(":", 2)[1]}`;
  return seg.replaceAll("_", " ").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());
}

export default function CustomerDetailPage() {
  return (
    <AuthGuard>
      <CustomerDetailScreen />
    </AuthGuard>
  );
}

function CustomerDetailScreen() {
  const params = useParams<{ id: string }>();
  const id = params?.id;
  const [data, setData] = useState<C360 | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [consentSource, setConsentSource] = useState("store_entry");

  const load = useCallback(async () => {
    if (!id) return;
    setLoading(true);
    setError(null);
    try {
      const res = await api<C360>(`/api/customers/${id}/intelligence`);
      setData(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load customer");
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  async function toggleConsent(grant: boolean) {
    if (!data || !id) return;
    setBusy(true);
    try {
      await api(`/api/customers/${id}/consent`, {
        method: "POST",
        json: { marketing_consent: grant, consent_source: grant ? consentSource : null },
      });
      setSuccess(grant ? "Marketing consent recorded." : "Customer opted out of marketing.");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update consent");
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return (
      <AppShell>
        <LoadingState label="Loading customer" />
      </AppShell>
    );
  }
  if (error && !data) {
    return (
      <AppShell>
        <ErrorState message={error} onRetry={() => void load()} />
      </AppShell>
    );
  }
  if (!data) {
    return (
      <AppShell>
        <EmptyState title="Customer not found" />
      </AppShell>
    );
  }

  const r = data.rfm;

  return (
    <AppShell>
      <div className="space-y-5">
        <Link href="/customers" className="inline-flex items-center gap-1 text-sm font-semibold text-primary">
          <ArrowLeft className="h-4 w-4" />
          All customers
        </Link>

        <PageHeader
          title={data.customer.name}
          description={[
            data.customer.phone || "No phone on record",
            `Customer since ${data.customer.created_at ? data.customer.created_at.slice(0, 10) : "—"}`,
          ].join(" · ")}
          icon={UserRound}
          action={
            <Link href={`/whatsapp?customer=${data.customer.id}`} className="btn btn-primary">
              <Megaphone className="h-4 w-4" />
              Prepare campaign
            </Link>
          }
        />

        {success ? <SuccessBanner message={success} /> : null}

        {/* Summary metrics */}
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <MetricCard icon={Receipt} label="Total orders" value={String(r.orders)} tone="blue" />
          <MetricCard icon={ShoppingBag} label="Lifetime spend" value={formatINR(r.total_spent)} tone="green" />
          <MetricCard icon={BadgeCheck} label="Avg order value" value={formatINR(r.avg_order_value)} tone="cyan" />
          <MetricCard
            icon={CalendarClock}
            label="Last purchase"
            value={r.days_since_last_purchase != null ? `${r.days_since_last_purchase}d ago` : "Never"}
            hint={r.avg_purchase_interval_days != null ? `usually every ~${r.avg_purchase_interval_days}d` : undefined}
            tone="purple"
          />
        </div>

        {/* Segments + consent */}
        <div className="grid gap-4 xl:grid-cols-3">
          <section className="card p-5 xl:col-span-2">
            <h2 className="text-base font-bold">Segments</h2>
            <p className="mb-3 text-xs text-text-muted">Deterministic rules — thresholds shown on the Customers page.</p>
            <div className="flex flex-wrap gap-2">
              {data.segments.map((s) => (
                <StatusBadge key={s} label={prettySegment(s)} tone={segmentTone(s)} />
              ))}
            </div>

            {data.opportunity ? (
              <div className="mt-4 flex items-start gap-3 rounded-xl bg-[#e8f2ff] p-3">
                <Sparkles className="mt-0.5 h-5 w-5 shrink-0 text-primary" />
                <div>
                  <p className="text-sm font-semibold text-foreground">
                    Opportunity: {prettySegment(data.opportunity.type)}
                  </p>
                  <p className="text-xs text-text-secondary">{data.opportunity.evidence}</p>
                </div>
              </div>
            ) : null}
          </section>

          <section className="card p-5">
            <h2 className="text-base font-bold">Marketing consent</h2>
            {data.customer.marketing_consent ? (
              <>
                <div className="mt-2 flex items-center gap-2">
                  <StatusBadge label="Consented" tone="success" />
                  {data.customer.consent_source ? (
                    <span className="text-xs text-text-muted">via {data.customer.consent_source}</span>
                  ) : null}
                </div>
                {data.customer.consent_timestamp ? (
                  <p className="mt-1 text-[11px] text-text-muted">
                    Recorded {formatDateTime(data.customer.consent_timestamp)}
                  </p>
                ) : null}
                <button
                  type="button"
                  className="btn btn-secondary mt-3 w-full"
                  disabled={busy}
                  onClick={() => toggleConsent(false)}
                >
                  Record opt-out
                </button>
              </>
            ) : (
              <>
                <div className="mt-2">
                  <StatusBadge label="No marketing consent" tone="neutral" />
                </div>
                {data.customer.opt_out_timestamp ? (
                  <p className="mt-1 text-[11px] text-text-muted">
                    Opted out {formatDateTime(data.customer.opt_out_timestamp)}
                  </p>
                ) : null}
                <select
                  className="input mt-3"
                  value={consentSource}
                  onChange={(e) => setConsentSource(e.target.value)}
                >
                  <option value="store_entry">In store (verbal)</option>
                  <option value="written">Written</option>
                  <option value="signup">Signup</option>
                </select>
                <button
                  type="button"
                  className="btn btn-primary mt-2 w-full"
                  disabled={busy}
                  onClick={() => toggleConsent(true)}
                >
                  Record consent
                </button>
              </>
            )}
            <p className="mt-2 text-[11px] text-text-muted">
              Campaigns only ever include customers with consent on file. Opted-out customers are excluded everywhere.
            </p>
          </section>
        </div>

        {/* Favorites + categories */}
        <div className="grid gap-4 xl:grid-cols-2">
          <section className="card p-5">
            <h2 className="text-base font-bold">Frequently purchased products</h2>
            {data.favorite_products.length === 0 ? (
              <EmptyState title="No purchases yet" description="Purchase history appears after the first sale." />
            ) : (
              <ul className="mt-3 space-y-2">
                {data.favorite_products.map((p) => (
                  <li key={p.product_id} className="flex items-center justify-between rounded-xl border border-border px-3 py-2.5">
                    <div>
                      <p className="text-sm font-semibold">{p.name}</p>
                      <p className="text-[11px] text-text-muted">{p.units} units · {p.times_purchased} time(s)</p>
                    </div>
                    <span className="text-sm font-semibold">{formatINR(p.spend)}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="card p-5">
            <h2 className="text-base font-bold">Category behavior</h2>
            {data.favorite_categories.length === 0 ? (
              <EmptyState title="No category history yet" />
            ) : (
              <ul className="mt-3 space-y-2">
                {data.favorite_categories.map((c) => {
                  const max = Math.max(...data.favorite_categories.map((x) => Number(x.spend)), 1);
                  return (
                    <li key={c.category}>
                      <div className="mb-1 flex justify-between text-sm">
                        <span>{c.category}</span>
                        <span className="font-semibold">{formatINR(Number(c.spend))}</span>
                      </div>
                      <div className="h-2 rounded-full bg-[#f1f5f9]">
                        <div className="h-2 rounded-full bg-primary" style={{ width: `${(Number(c.spend) / max) * 100}%` }} />
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </section>
        </div>

        {/* Recent orders + campaign history */}
        <div className="grid gap-4 xl:grid-cols-3">
          <section className="card p-5 xl:col-span-2">
            <h2 className="text-base font-bold">Recent orders</h2>
            {data.recent_orders.length === 0 ? (
              <EmptyState title="No orders yet" />
            ) : (
              <ul className="mt-3 divide-y divide-[#f1f5f9]">
                {data.recent_orders.map((o) => (
                  <li key={o.id} className="flex items-start justify-between gap-3 py-3">
                    <div>
                      <p className="text-sm font-semibold">{formatINR(o.total)}</p>
                      <p className="text-[11px] text-text-muted">
                        {formatDateTime(o.created_at)} · {o.payment_method}
                      </p>
                      <p className="mt-0.5 text-xs text-text-secondary">
                        {o.items.map((i) => `${i.name} ×${i.quantity}`).join(", ")}
                      </p>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="card p-5">
            <h2 className="text-base font-bold">Campaign history</h2>
            {data.campaign_history.length === 0 ? (
              <EmptyState title="Never contacted" description="Campaign deliveries to this customer appear here." />
            ) : (
              <ul className="mt-3 space-y-2">
                {data.campaign_history.map((c) => (
                  <li key={c.campaign_id} className="rounded-xl border border-border px-3 py-2.5">
                    <div className="flex items-center justify-between gap-2">
                      <p className="text-sm font-semibold">{c.campaign_name}</p>
                      <StatusBadge label={c.status} tone={c.status === "sent" ? "success" : "neutral"} />
                    </div>
                    {c.sent_at ? <p className="text-[11px] text-text-muted">{formatDateTime(c.sent_at)}</p> : null}
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      </div>
    </AppShell>
  );
}
