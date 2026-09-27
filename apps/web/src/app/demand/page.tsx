"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  ArrowRight,
  BarChart3,
  CalendarDays,
  Minus,
  ShieldAlert,
  TrendingDown,
  TrendingUp,
} from "lucide-react";
import { AppShell, AuthGuard } from "@/components/shell";
import { PageHeader } from "@/components/page-header";
import { MetricCard } from "@/components/metric-card";
import { DailyBarChart, TrendListBars } from "@/components/charts";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { StatusBadge } from "@/components/status-badge";
import { api } from "@/lib/api";
import { formatINR } from "@/lib/types";

type ProductTrend = {
  product_id: string;
  name: string;
  category: string;
  current_stock: number;
  recent_7d_units: number;
  previous_7d_units: number;
  change_7d_pct: number | null;
  trend: string;
  data_quality: string;
  availability_ratio_30d: number | null;
  stockout_aware: boolean;
};

type CategoryTrend = {
  category: string;
  recent_14d_units: number;
  previous_14d_units: number;
  change_14d_pct: number | null;
  revenue_14d: number;
  trend: string;
  data_quality: string;
};

type Overview = {
  overview: {
    sales_recent_7d: number;
    sales_previous_7d: number;
    change_7d_pct: number | null;
    products_analyzed: number;
    products_insufficient_data: number;
    rising_count: number;
    falling_count: number;
    method_note: string;
  };
  rising_products: ProductTrend[];
  falling_products: ProductTrend[];
  category_trends: CategoryTrend[];
  watch_products: ProductTrend[];
};

type DailyPoint = { day: string; sales: number; orders: number; units: number };

type ForecastItem = {
  product_id: string;
  name: string;
  horizon_days: number;
  status: string;
  data_quality: string;
  baseline_daily_units?: number;
  forecast_range?: { low: number; high: number; unit: string; period: string } | null;
  current_stock?: number;
  explanation: string;
};

function trendBadge(trend: string) {
  switch (trend) {
    case "RISING":
      return <StatusBadge label="Rising" tone="success" />;
    case "FALLING":
    case "WATCH":
      return <StatusBadge label={trend === "WATCH" ? "Watch" : "Falling"} tone="warning" />;
    case "STABLE":
      return <StatusBadge label="Stable" tone="info" />;
    default:
      return <StatusBadge label="Not enough data" tone="neutral" />;
  }
}

function changePct(pct: number | null) {
  if (pct === null) return <span className="text-[11px] text-text-muted">no baseline</span>;
  const cls = pct >= 0 ? "text-success" : "text-danger";
  const Icon = pct >= 0 ? TrendingUp : TrendingDown;
  return (
    <span className={`inline-flex items-center gap-1 text-xs font-semibold ${cls}`}>
      <Icon className="h-3.5 w-3.5" />
      {pct >= 0 ? "+" : ""}{pct}%
    </span>
  );
}

export default function DemandPage() {
  return (
    <AuthGuard>
      <DemandScreen />
    </AuthGuard>
  );
}

function DemandScreen() {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [daily, setDaily] = useState<DailyPoint[]>([]);
  const [dailyDays, setDailyDays] = useState(30);
  const [dow, setDow] = useState<{ sufficient_data: boolean; note: string | null; pattern: { weekday: string; avg_daily_sales: number }[] } | null>(null);
  const [forecasts, setForecasts] = useState<ForecastItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [ov, ds, dw] = await Promise.all([
        api<Overview>("/api/demand/overview"),
        api<{ series: DailyPoint[] }>(`/api/demand/daily-sales?days=${dailyDays}`),
        api<{ sufficient_data: boolean; note: string | null; pattern: { weekday: string; avg_daily_sales: number }[] }>("/api/demand/day-of-week"),
      ]);
      setOverview(ov);
      setDaily(ds.series);
      setDow(dw.sufficient_data ? dw : { ...dw, pattern: dw.pattern });

      // Forecast the top rising + top watch products (bounded, real products only)
      const targets = [...ov.rising_products.slice(0, 4), ...ov.falling_products.slice(0, 2)]
        .map((t) => t.product_id);
      if (targets.length) {
        const fc = await api<{ items: ForecastItem[] }>("/api/demand/forecasts", {
          method: "POST",
          json: { product_ids: targets, horizon_days: 7 },
        });
        setForecasts(fc.items);
      } else {
        setForecasts([]);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load demand data");
    } finally {
      setLoading(false);
    }
  }, [dailyDays]);

  useEffect(() => {
    void load();
  }, [load]);

  const k = overview?.overview;

  return (
    <AppShell>
      <div className="space-y-5">
        <PageHeader
          title="Demand & Trends"
          description="What is selling, what is changing, and what to prepare for — from your actual sales."
          icon={BarChart3}
          action={
            <Link href="/festivals" className="btn btn-secondary">
              <CalendarDays className="h-4 w-4" />
              Festival Calendar
            </Link>
          }
        />

        {loading ? <LoadingState label="Analyzing sales history" /> : null}
        {error && !loading ? <ErrorState message={error} onRetry={() => void load()} /> : null}

        {!loading && !error && overview ? (
          <>
            {/* 1. Demand overview */}
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
              <MetricCard
                icon={TrendingUp}
                label="Sales — last 7 days"
                value={formatINR(k!.sales_recent_7d)}
                delta={k!.change_7d_pct}
                deltaLabel="vs previous 7 days"
                tone="blue"
              />
              <MetricCard
                icon={TrendingUp}
                label="Rising products"
                value={String(k!.rising_count)}
                hint="clear upward movement"
                tone="green"
              />
              <MetricCard
                icon={TrendingDown}
                label="Declining products"
                value={String(k!.falling_count)}
                hint="7-day windows"
                tone="orange"
              />
              <MetricCard
                icon={ShieldAlert}
                label="Insufficient data"
                value={String(k!.products_insufficient_data)}
                hint={`${k!.products_analyzed} products analyzed`}
                tone="purple"
              />
            </div>
            <p className="text-xs text-text-muted">{k!.method_note}</p>

            {/* 2. Daily sales chart */}
            <section className="card p-5">
              <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
                <div>
                  <h2 className="text-base font-bold">Daily sales</h2>
                  <p className="text-xs text-text-muted">Total store sales per day</p>
                </div>
                <div className="flex gap-1">
                  {[14, 30, 60].map((d) => (
                    <button
                      key={d}
                      type="button"
                      onClick={() => setDailyDays(d)}
                      className={`rounded-lg px-3 py-1.5 text-xs font-semibold ${dailyDays === d ? "bg-[#e8f2ff] text-primary" : "text-text-secondary hover:bg-[#f8fafc]"}`}
                    >
                      {d}d
                    </button>
                  ))}
                </div>
              </div>
              <DailyBarChart
                points={daily.map((p) => ({ label: p.day.slice(5), value: Number(p.sales) }))}
                height={190}
              />
            </section>

            {/* 3 & 4. Rising / Falling */}
            <div className="grid gap-4 xl:grid-cols-2">
              <TrendSection
                title="Rising products"
                subtitle="Units sold: last 7 days vs the 7 days before"
                items={overview.rising_products}
                emptyTitle="No clear risers right now"
                emptyDesc="When a product's recent week clearly beats its previous week, it appears here."
              />
              <TrendSection
                title="Falling products"
                subtitle="Declines with visible evidence — stockouts are accounted for"
                items={overview.falling_products}
                emptyTitle="No clear declines right now"
                emptyDesc="Products whose recent week clearly trails their previous week appear here."
              />
            </div>

            {/* 5. Category trends */}
            <section className="card p-5">
              <h2 className="text-base font-bold">Category trends</h2>
              <p className="mb-4 text-xs text-text-muted">Last 14 days vs the previous 14 days, by revenue</p>
              <TrendListBars
                items={overview.category_trends.slice(0, 8).map((c) => ({
                  label: c.category,
                  value: Number(c.revenue_14d),
                  deltaPct: c.change_14d_pct,
                  caption: `${c.recent_14d_units} units vs ${c.previous_14d_units} previous · ${c.data_quality === "INSUFFICIENT_DATA" ? "not enough history" : `${c.data_quality.toLowerCase()} data quality`}`,
                }))}
              />
            </section>

            {/* 6. Forecasts */}
            <section className="card p-5">
              <h2 className="text-base font-bold">Forecasts — next 7 days</h2>
              <p className="mb-4 text-xs text-text-muted">
                Estimates as ranges, never exact numbers. Quality reflects available history and stockouts.
              </p>
              {forecasts.length === 0 ? (
                <EmptyState
                  title="No forecasts available"
                  description="Forecasts appear for products with recent sales movement."
                />
              ) : (
                <ul className="space-y-3">
                  {forecasts.map((f) => (
                    <li key={f.product_id} className="rounded-xl border border-border p-3">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <p className="font-semibold">{f.name}</p>
                        <span className="flex items-center gap-2">
                          <StatusBadge
                            label={f.data_quality === "INSUFFICIENT_DATA" ? "insufficient data" : `${f.data_quality.toLowerCase()} data quality`}
                            tone={f.data_quality === "HIGH" ? "success" : f.data_quality === "MEDIUM" ? "info" : "warning"}
                          />
                          {f.forecast_range ? (
                            <span className="text-sm font-bold">
                              {f.forecast_range.low}–{f.forecast_range.high} units
                            </span>
                          ) : null}
                        </span>
                      </div>
                      <p className="mt-1 text-xs text-text-secondary">{f.explanation}</p>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            {/* 7. Weekday pattern */}
            <section className="card p-5">
              <div className="mb-4 flex items-center justify-between">
                <div>
                  <h2 className="text-base font-bold">Day-of-week pattern</h2>
                  <p className="text-xs text-text-muted">Average sales by weekday (last 8 weeks)</p>
                </div>
                {dow && !dow.sufficient_data ? (
                  <StatusBadge label="low confidence" tone="warning" />
                ) : null}
              </div>
              {dow && dow.pattern.length ? (
                <>
                  {dow.note ? <p className="mb-3 text-xs text-warning">{dow.note}</p> : null}
                  <DualBarChartLite pattern={dow.pattern} />
                </>
              ) : (
                <EmptyState title="Not enough sales history" description="Weekday patterns need at least 4 weeks of sales." />
              )}
            </section>

            {/* 8. Upcoming opportunities link */}
            <section className="card flex flex-wrap items-center justify-between gap-3 p-5">
              <div>
                <h2 className="text-base font-bold">Upcoming demand opportunities</h2>
                <p className="text-xs text-text-muted">Festival demand, stock gaps and customer connections</p>
              </div>
              <Link href="/festivals" className="btn btn-primary">
                Open Festival Intelligence
                <ArrowRight className="h-4 w-4" />
              </Link>
            </section>
          </>
        ) : null}
      </div>
    </AppShell>
  );
}

function TrendSection({
  title,
  subtitle,
  items,
  emptyTitle,
  emptyDesc,
}: {
  title: string;
  subtitle: string;
  items: ProductTrend[];
  emptyTitle: string;
  emptyDesc: string;
}) {
  return (
    <section className="card p-5">
      <h2 className="text-base font-bold">{title}</h2>
      <p className="mb-4 text-xs text-text-muted">{subtitle}</p>
      {items.length === 0 ? (
        <EmptyState title={emptyTitle} description={emptyDesc} />
      ) : (
        <ul className="space-y-3">
          {items.slice(0, 6).map((t) => (
            <li key={t.product_id} className="rounded-xl border border-border p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="min-w-0">
                  <p className="truncate font-semibold">{t.name}</p>
                  <p className="text-[11px] text-text-muted">
                    {t.recent_7d_units} units vs {t.previous_7d_units} previous · stock {t.current_stock}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  {changePct(t.change_7d_pct)}
                  {trendBadge(t.trend)}
                </div>
              </div>
              {t.stockout_aware ? (
                <p className="mt-2 flex items-start gap-1.5 rounded-lg bg-[#fffbeb] px-2 py-1.5 text-[11px] text-[#92400e]">
                  <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                  Out of stock ~{t.availability_ratio_30d != null ? Math.round((1 - t.availability_ratio_30d) * 100) : "?"}% of the last 30 days — recent sales understate real demand.
                </p>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function DualBarChartLite({
  pattern,
}: {
  pattern: { weekday: string; avg_daily_sales: number }[];
}) {
  const max = Math.max(...pattern.map((p) => p.avg_daily_sales), 1);
  return (
    <div className="flex h-40 items-end gap-2">
      {pattern.map((p) => (
        <div key={p.weekday} className="flex flex-1 flex-col items-center justify-end gap-1">
          <span className="text-[10px] font-semibold text-text-secondary">{formatINR(p.avg_daily_sales)}</span>
          <div
            className="w-full rounded-t bg-primary/85"
            style={{ height: `${Math.max((p.avg_daily_sales / max) * 110, 3)}px` }}
            title={`${p.weekday}: ${formatINR(p.avg_daily_sales)}`}
          />
          <span className="text-[10px] text-text-muted">{p.weekday.slice(0, 3)}</span>
        </div>
      ))}
    </div>
  );
}
