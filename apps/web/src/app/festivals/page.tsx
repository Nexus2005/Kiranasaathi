"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  CalendarDays,
  ChevronRight,
  Clock,
  Info,
  PackageSearch,
  Sparkles,
  Users,
} from "lucide-react";
import { AppShell, AuthGuard } from "@/components/shell";
import { PageHeader } from "@/components/page-header";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { StatusBadge } from "@/components/status-badge";
import { api } from "@/lib/api";

type Festival = {
  id: string;
  name: string;
  start_date: string;
  end_date: string;
  days_away: number;
  region: string;
  category: string;
  description: string;
  relevance: { category: string; note?: string }[];
  matched_store_categories: string[];
  has_store_relevance: boolean;
  source: string;
  data_provenance: string;
};

type OppProduct = {
  product_id: string;
  name: string;
  category: string;
  current_sellable_stock: number;
  estimated_demand_range: string | null;
  potential_gap_units: string | null;
  gap_status: string;
  forecast_data_quality: string;
};

type ExtSignal = {
  id: string;
  type: string;
  title: string;
  statement: string;
  category: string | null;
  region: string | null;
  confidence: string;
};

type Opportunity = {
  festival: { id: string; name: string; start_date: string; days_away: number; description: string };
  status: string;
  relevant_products: OppProduct[];
  summary: {
    relevant_product_count: number;
    products_with_gap: number;
    total_gap_units_range: string;
    historical_evidence: boolean;
    historical_evidence_note: string | null;
    customers_with_relevant_history: number;
    consented_customers_with_phone: number;
    supplier_count: number;
  };
  supplier_options: { supplier_id: string; name: string; lead_time_days: number | null; has_relevant_history: boolean }[];
};

export default function FestivalsPage() {
  return (
    <AuthGuard>
      <FestivalsScreen />
    </AuthGuard>
  );
}

function FestivalsScreen() {
  const [upcoming, setUpcoming] = useState<Festival[]>([]);
  const [past, setPast] = useState<{ id: string; name: string; end_date: string }[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [opp, setOpp] = useState<Opportunity | null>(null);
  const [oppLoading, setOppLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api<{ upcoming: Festival[]; past: { id: string; name: string; end_date: string }[] }>("/api/festivals");
      setUpcoming(res.upcoming);
      setPast(res.past);
      if (res.upcoming.length) setSelected(res.upcoming[0].id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load festivals");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!selected) return;
    setOppLoading(true);
    api<Opportunity>(`/api/festivals/${selected}/opportunity`)
      .then(setOpp)
      .catch(() => setOpp(null))
      .finally(() => setOppLoading(false));
  }, [selected]);

  return (
    <AppShell>
      <div className="space-y-5">
        <PageHeader
          title="Festival Intelligence"
          description="Configured festival calendar connected to your demand, stock, suppliers and customers."
          icon={CalendarDays}
        />

        {loading ? <LoadingState label="Loading festival calendar" /> : null}
        {error && !loading ? <ErrorState message={error} onRetry={() => void load()} /> : null}

        {!loading && !error ? (
          upcoming.length === 0 ? (
            <EmptyState
              title="No upcoming configured events"
              description="Configured festivals within the next 120 days will appear here."
            />
          ) : (
            <>
              <div className="grid gap-4 lg:grid-cols-5">
                {/* Festival list */}
                <section className="card p-4 lg:col-span-2">
                  <h2 className="mb-3 text-sm font-bold uppercase tracking-wide text-text-secondary">Upcoming</h2>
                  <ul className="space-y-2">
                    {upcoming.map((f) => (
                      <li key={f.id}>
                        <button
                          type="button"
                          onClick={() => setSelected(f.id)}
                          className={`w-full rounded-xl border p-3 text-left transition ${selected === f.id ? "border-primary bg-[#e8f2ff]" : "border-border hover:bg-[#f8fafc]"}`}
                        >
                          <div className="flex items-center justify-between gap-2">
                            <p className="font-semibold text-foreground">{f.name}</p>
                            <span className="flex items-center gap-1 text-xs font-semibold text-primary">
                              <Clock className="h-3.5 w-3.5" />
                              {f.days_away === 0 ? "today" : `${f.days_away}d`}
                            </span>
                          </div>
                          <p className="mt-0.5 text-[11px] text-text-muted">
                            {f.start_date} · {f.region}
                          </p>
                          <div className="mt-2 flex flex-wrap gap-1">
                            {f.matched_store_categories.length ? (
                              f.matched_store_categories.slice(0, 3).map((c) => (
                                <span key={c} className="rounded-full bg-white px-2 py-0.5 text-[10px] font-medium text-text-secondary ring-1 ring-border">
                                  {c}
                                </span>
                              ))
                            ) : (
                              <span className="text-[10px] text-text-muted">No matching categories in your catalog</span>
                            )}
                          </div>
                        </button>
                      </li>
                    ))}
                  </ul>

                  {past.length ? (
                    <>
                      <h2 className="mb-2 mt-5 text-sm font-bold uppercase tracking-wide text-text-secondary">Past</h2>
                      <ul className="space-y-1.5">
                        {past.map((p) => (
                          <li key={p.id} className="flex items-center justify-between rounded-lg bg-[#f8fafc] px-3 py-2 text-sm">
                            <span className="text-text-secondary">{p.name}</span>
                            <span className="text-[11px] text-text-muted">{p.end_date}</span>
                          </li>
                        ))}
                      </ul>
                    </>
                  ) : null}
                </section>

                {/* Opportunity panel */}
                <section className="space-y-4 lg:col-span-3">
                  {oppLoading || !opp ? (
                    <LoadingState label="Analyzing festival opportunity" />
                  ) : opp.status !== "ok" ? (
                    <EmptyState title="Festival passed" description={opp.status === "PASSED" ? "No upcoming occurrence is configured." : undefined} />
                  ) : (
                    <>
                      <div className="card bg-gradient-to-br from-[#fff7ed] to-white p-5">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <div>
                            <h2 className="text-lg font-bold text-foreground">{opp.festival.name}</h2>
                            <p className="text-xs text-text-secondary">
                              {opp.festival.start_date} · {opp.festival.days_away} day(s) away
                            </p>
                          </div>
                          <StatusBadge
                            label={opp.summary.products_with_gap > 0 ? `${opp.summary.products_with_gap} stock gap` : "no gap yet"}
                            tone={opp.summary.products_with_gap > 0 ? "warning" : "success"}
                          />
                        </div>
                        <p className="mt-2 text-sm text-text-secondary">{opp.festival.description}</p>
                        <p className="mt-3 flex items-start gap-1.5 rounded-lg bg-white/80 px-3 py-2 text-[11px] text-text-muted">
                          <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                          Festival dates are configured application data (source: {opp.summary ? "seeded panchang calendar" : "n/a"}). Not live external intelligence.
                        </p>
                      </div>

                      {/* Demand evidence */}
                      <div className="card p-5">
                        <h3 className="text-sm font-bold">Historical evidence (your store only)</h3>
                        <p className="mt-1 text-sm text-text-secondary">
                          {opp.summary.historical_evidence
                            ? "Your sales history covers a past occurrence of this festival — compare below."
                            : opp.summary.historical_evidence_note || "No store-specific historical evidence available."}
                        </p>
                        <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
                          <MiniStat label="Relevant products" value={String(opp.summary.relevant_product_count)} />
                          <MiniStat label="Stock gaps" value={String(opp.summary.products_with_gap)} />
                          <MiniStat label="Customers w/ history" value={String(opp.summary.customers_with_relevant_history)} />
                          <MiniStat label="Eligible (consent)" value={String(opp.summary.consented_customers_with_phone)} />
                        </div>
                      </div>

                      {/* External signals (Phase 5) — clearly labelled */}
                      <ExternalSignalsPanel festivalCategories={opp.relevant_products.map((p) => p.category)} />

                      {/* Product-level gap table */}
                      {opp.relevant_products.length ? (
                        <div className="card overflow-hidden p-0">
                          <div className="border-b border-border px-5 py-4">
                            <h3 className="flex items-center gap-2 text-sm font-bold">
                              <PackageSearch className="h-4 w-4 text-primary" />
                              Relevant products & stock gap
                            </h3>
                          </div>
                          <div className="overflow-x-auto">
                            <table className="w-full text-sm">
                              <thead>
                                <tr className="border-b border-border bg-[#f8fafc] text-left text-xs text-text-secondary">
                                  <th className="px-5 py-2.5 font-semibold">Product</th>
                                  <th className="px-3 py-2.5 font-semibold">Stock</th>
                                  <th className="px-3 py-2.5 font-semibold">Est. demand</th>
                                  <th className="px-3 py-2.5 font-semibold">Gap</th>
                                  <th className="px-5 py-2.5 font-semibold">Quality</th>
                                </tr>
                              </thead>
                              <tbody>
                                {opp.relevant_products.map((p) => (
                                  <tr key={p.product_id} className="border-b border-[#f1f5f9] last:border-0">
                                    <td className="px-5 py-3 font-medium">{p.name}</td>
                                    <td className="px-3 py-3">{p.current_sellable_stock}</td>
                                    <td className="px-3 py-3">{p.estimated_demand_range || "—"}</td>
                                    <td className="px-3 py-3">
                                      {p.gap_status === "GAP" ? (
                                        <span className="font-semibold text-danger">{p.potential_gap_units}</span>
                                      ) : p.gap_status === "OK" ? (
                                        <span className="text-success">covered</span>
                                      ) : (
                                        <span className="text-text-muted">insufficient data</span>
                                      )}
                                    </td>
                                    <td className="px-5 py-3 text-xs text-text-muted">{p.forecast_data_quality.toLowerCase()}</td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        </div>
                      ) : (
                        <EmptyState
                          title="No relevant products in your catalog"
                          description="Add products in the festival's categories to see inventory opportunities."
                        />
                      )}

                      {/* Actions */}
                      <div className="card flex flex-wrap items-center justify-between gap-3 p-5">
                        <div className="flex items-start gap-3">
                          <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-[#e8f2ff] text-primary">
                            <Sparkles className="h-5 w-5" />
                          </span>
                          <div>
                            <p className="font-semibold">Take action</p>
                            <p className="text-xs text-text-secondary">
                              Review stock on Demand & Trends, or prepare a festival campaign for eligible customers.
                            </p>
                          </div>
                        </div>
                        <div className="flex gap-2">
                          <Link href="/demand" className="btn btn-secondary">Review stock plan</Link>
                          <Link
                            href={`/whatsapp?type=festival&festival=${opp.festival.id}`}
                            className="btn btn-primary"
                          >
                            Prepare campaign
                            <ChevronRight className="h-4 w-4" />
                          </Link>
                        </div>
                      </div>
                    </>
                  )}
                </section>
              </div>
            </>
          )
        ) : null}
      </div>
    </AppShell>
  );
}

function ExternalSignalsPanel({ festivalCategories }: { festivalCategories: string[] }) {
  const [signals, setSignals] = useState<ExtSignal[] | null>(null);

  useEffect(() => {
    api<{ signals: ExtSignal[] }>('/api/external/context')
      .then((res) => setSignals(res.signals))
      .catch(() => setSignals(null));
  }, []);

  if (signals === null || signals.length === 0) return null;
  const relevant = signals.filter(
    (s) => s.category && festivalCategories.includes(s.category)
  );
  const shown = (relevant.length ? relevant : signals).slice(0, 3);
  if (!shown.length) return null;

  return (
    <div className="card p-5">
      <h3 className="text-sm font-bold">External reports</h3>
      <p className="mb-3 text-xs text-text-muted">
        From listed external sources — not your store data.
      </p>
      <ul className="space-y-2">
        {shown.map((s) => (
          <li key={s.id} className="rounded-xl bg-[#f8fafc] px-3 py-2.5">
            <p className="text-sm">{s.statement}</p>
            <p className="mt-1 text-[11px] text-text-muted">
              {s.confidence.toLowerCase()} confidence{s.category ? ` · ${s.category}` : ""} · external source
            </p>
          </li>
        ))}
      </ul>
    </div>
  );
}

function MiniStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl bg-[#f8fafc] px-3 py-2.5">
      <p className="text-lg font-bold text-foreground">{value}</p>
      <p className="text-[11px] text-text-muted">{label}</p>
    </div>
  );
}
