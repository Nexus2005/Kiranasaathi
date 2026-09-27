"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  Bot,
  CalendarClock,
  CircleAlert,
  IndianRupee,
  Package,
  PackageCheck,
  Receipt,
  Sparkles,
  TrendingUp,
  Users,
  type LucideIcon,
} from "lucide-react";
import { AppShell, AuthGuard } from "@/components/shell";
import { PageHeader } from "@/components/page-header";
import { MetricCard } from "@/components/metric-card";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { StatusBadge, statusTone } from "@/components/status-badge";
import { api } from "@/lib/api";
import { formatDateTime, formatINR, type DashboardSummary } from "@/lib/types";

const severityIcon = {
  critical: CircleAlert,
  warning: AlertTriangle,
  opportunity: Sparkles,
  info: Receipt,
};

const severityTone: Record<string, string> = {
  critical: "bg-[#fee2e2] text-[#dc2626]",
  warning: "bg-[#ffedd5] text-[#f97316]",
  opportunity: "bg-[#dcfce7] text-[#16a34a]",
  info: "bg-[#e8f2ff] text-[#2080f0]",
};

export default function HomePage() {
  return (
    <AuthGuard>
      <Dashboard />
    </AuthGuard>
  );
}

function Dashboard() {
  const [data, setData] = useState<DashboardSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api<DashboardSummary>("/api/dashboard/summary");
      setData(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load dashboard");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const k = data?.kpis;

  return (
    <AppShell>
      <div className="space-y-5">
        <PageHeader
          title={
            data
              ? `Good morning, ${data.user.name.split(" ")[0]} ji!`
              : "Good morning!"
          }
          description={
            data
              ? "Let's make today a more profitable day for your shop."
              : "Loading your store…"
          }
          icon={HomeIcon}
          action={
            <>
              <Link href="/smart-counter" className="btn btn-primary">
                <Receipt className="h-4 w-4" />
                New sale
              </Link>
              <Link href="/inventory" className="btn btn-secondary">
                <Package className="h-4 w-4" />
                Inventory
              </Link>
            </>
          }
        />

        {loading ? <LoadingState label="Loading dashboard" /> : null}
        {error && !loading ? <ErrorState message={error} onRetry={() => void load()} /> : null}

        {!loading && !error && data ? (
          <>
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
              <MetricCard
                icon={IndianRupee}
                label="Today's Sales"
                value={formatINR(k!.today_sales)}
                delta={k!.sales_delta_pct}
                deltaLabel="vs yesterday"
                tone="blue"
              />
              <MetricCard
                icon={Receipt}
                label="Total Orders"
                value={String(k!.today_orders)}
                deltaLabel="today"
                tone="cyan"
              />
              <MetricCard
                icon={TrendingUp}
                label="Profit (Est.)"
                value={formatINR(k!.today_profit)}
                deltaLabel="gross margin today"
                tone="green"
              />
              <MetricCard
                icon={PackageCheck}
                label="Inventory Value"
                value={formatINR(k!.inventory_value)}
                hint={`${k!.total_products} products`}
                tone="purple"
              />
              <MetricCard
                icon={AlertTriangle}
                label="Low Stock Items"
                value={String(k!.low_stock)}
                badge={k!.low_stock > 0 ? { label: `${k!.out_of_stock} out`, tone: "danger" } : undefined}
                tone="orange"
              />
              <MetricCard
                icon={CalendarClock}
                label="Expiring Soon"
                value={String(k!.expiring_soon)}
                hint="within 30 days"
                tone="red"
              />
              <MetricCard
                icon={Sparkles}
                label="AI Recommendations"
                value={String(k!.recommendations)}
                deltaLabel="pending review"
                tone="blue"
              />
              <MetricCard
                icon={Users}
                label="Open Alerts"
                value={String(k!.open_alerts)}
                deltaLabel="need attention"
                tone="orange"
              />
            </div>

            <div className="grid gap-4 xl:grid-cols-3">
              <section className="card p-5 xl:col-span-2">
                <div className="mb-4 flex items-center justify-between">
                  <div>
                    <h2 className="text-base font-bold">Today&apos;s Priorities</h2>
                    <p className="text-xs text-text-muted">From real inventory &amp; alert data</p>
                  </div>
                  <StatusBadge
                    label={`${data.priorities.length} open`}
                    tone={data.priorities.some((p) => p.severity === "critical") ? "danger" : "info"}
                  />
                </div>
                {data.priorities.length === 0 ? (
                  <EmptyState
                    title="No open priorities"
                    description="Your store has no open alerts right now."
                  />
                ) : (
                  <ul className="space-y-3">
                    {data.priorities.map((p) => {
                      const Icon = severityIcon[p.severity as keyof typeof severityIcon] || Receipt;
                      return (
                        <li
                          key={p.id}
                          className="flex flex-col gap-3 rounded-xl border border-border p-3 sm:flex-row sm:items-center sm:justify-between"
                        >
                          <div className="flex gap-3">
                            <span
                              className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl ${severityTone[p.severity] || severityTone.info}`}
                            >
                              <Icon className="h-5 w-5" />
                            </span>
                            <div>
                              <p className="font-semibold text-foreground">{p.title}</p>
                              {p.description ? (
                                <p className="text-sm text-text-secondary">{p.description}</p>
                              ) : null}
                              <p className="mt-0.5 text-[11px] text-text-muted">
                                {formatDateTime(p.created_at)} · {p.type}
                              </p>
                            </div>
                          </div>
                          <StatusBadge label={p.severity} tone={statusTone(p.severity)} />
                        </li>
                      );
                    })}
                  </ul>
                )}
              </section>

              <section className="card p-5">
                <div className="mb-4 flex items-center gap-2">
                  <Bot className="h-5 w-5 text-primary" />
                  <h2 className="text-base font-bold">AI Recommendations</h2>
                </div>
                {data.recommendations.length === 0 ? (
                  <EmptyState
                    title="No recommendations yet"
                    description="Recommendations appear once inventory and sales data create risks or opportunities."
                  />
                ) : (
                  <ul className="space-y-3">
                    {data.recommendations.map((r) => (
                      <li key={r.id} className="rounded-xl bg-[#f8fafc] p-3">
                        <div className="flex items-start justify-between gap-2">
                          <p className="font-semibold text-foreground">{r.title}</p>
                          <StatusBadge label={r.status} tone={statusTone(r.status)} />
                        </div>
                        <p className="mt-1 text-sm text-text-secondary">{r.description}</p>
                        {r.proposed_action ? (
                          <p className="mt-2 rounded-lg bg-[#eff6ff] px-2 py-1.5 text-xs text-[#1d4ed8]">
                            Action: {r.proposed_action}
                          </p>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            </div>

            <div className="grid gap-4 xl:grid-cols-3">
              <section className="card p-5 xl:col-span-2">
                <div className="mb-4 flex items-center justify-between">
                  <h2 className="text-base font-bold">Sales — last 7 days</h2>
                  <span className="text-xs text-text-muted">Live from database</span>
                </div>
                <WeekChart points={data.week_sales} />
              </section>

              <section className="card p-5">
                <h2 className="mb-4 text-base font-bold">Top products (30 days)</h2>
                {data.top_products.length === 0 ? (
                  <EmptyState title="No sales yet" description="Complete a sale in Smart Counter." />
                ) : (
                  <ul className="space-y-3">
                    {data.top_products.map((p) => (
                      <li key={p.name} className="flex items-center justify-between gap-2">
                        <div>
                          <p className="text-sm font-semibold">{p.name}</p>
                          <p className="text-[11px] text-text-muted">
                            {p.units} units · profit {formatINR(p.profit)}
                          </p>
                        </div>
                        <span className="text-sm font-bold text-foreground">{formatINR(p.revenue)}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            </div>

            <div className="grid gap-4 xl:grid-cols-2">
              <section className="card p-5">
                <div className="mb-3 flex items-center justify-between">
                  <h2 className="text-base font-bold">Recent activity</h2>
                  <Link href="/activity" className="text-sm font-semibold text-primary">
                    View all
                  </Link>
                </div>
                {data.activity.length === 0 ? (
                  <EmptyState title="No activity yet" />
                ) : (
                  <ul className="space-y-2">
                    {data.activity.map((a) => (
                      <li key={a.id} className="flex items-start justify-between gap-3 border-b border-[#f1f5f9] pb-2 last:border-0">
                        <div>
                          <p className="text-sm">{a.message || a.event_type}</p>
                          <p className="text-[11px] text-text-muted">{a.event_type}</p>
                        </div>
                        <span className="shrink-0 text-[11px] text-text-muted">
                          {formatDateTime(a.created_at)}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              <section className="card p-5">
                <h2 className="mb-3 text-base font-bold">Category mix (this month)</h2>
                {data.categories.length === 0 ? (
                  <EmptyState title="No category sales yet" />
                ) : (
                  <ul className="space-y-3">
                    {data.categories.map((c) => {
                      const max = Math.max(...data.categories.map((x) => Number(x.revenue)), 1);
                      const pct = Math.round((Number(c.revenue) / max) * 100);
                      return (
                        <li key={c.category}>
                          <div className="mb-1 flex justify-between text-sm">
                            <span>{c.category}</span>
                            <span className="font-semibold">{formatINR(Number(c.revenue))}</span>
                          </div>
                          <div className="h-2 rounded-full bg-[#f1f5f9]">
                            <div
                              className="h-2 rounded-full bg-primary"
                              style={{ width: `${pct}%` }}
                            />
                          </div>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </section>
            </div>
          </>
        ) : null}
      </div>
    </AppShell>
  );
}

const HomeIcon: LucideIcon = Package;

function WeekChart({ points }: { points: { day: string; total: number }[] }) {
  const days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const byDay = new Map(points.map((p) => [p.day.toUpperCase().slice(0, 3), Number(p.total)]));
  const series = days.map((d) => ({ day: d, total: byDay.get(d) ?? 0 }));
  const max = Math.max(...series.map((s) => s.total), 1);

  if (series.every((s) => s.total === 0)) {
    return <EmptyState title="No sales this week" description="Complete a sale to populate this chart." />;
  }

  return (
    <div className="flex h-48 items-end gap-3">
      {series.map((s) => (
        <div key={s.day} className="flex flex-1 flex-col items-center gap-2">
          <span className="text-[11px] font-semibold text-text-secondary">{formatINR(s.total)}</span>
          <div
            className="w-full rounded-t-md bg-primary/90"
            style={{ height: `${Math.max((s.total / max) * 140, s.total > 0 ? 8 : 4)}px` }}
            title={formatINR(s.total)}
          />
          <span className="text-[11px] text-text-muted">{s.day}</span>
        </div>
      ))}
    </div>
  );
}
