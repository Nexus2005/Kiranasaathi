"use client";

import { useCallback, useEffect, useState } from "react";
import { Bell, BellOff, Check, Eye, RefreshCw } from "lucide-react";
import { AppShell, AuthGuard } from "@/components/shell";
import { PageHeader } from "@/components/page-header";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { StatusBadge } from "@/components/status-badge";
import { api } from "@/lib/api";
import { formatDateTime, type Alert } from "@/lib/types";
import { cn } from "@/lib/cn";

type Tab = "open" | "all";

const SEV_TONE: Record<string, "red" | "amber" | "blue" | "gray"> = {
  critical: "red",
  warning: "amber",
  info: "blue",
  opportunity: "blue",
};

export default function AlertsPage() {
  return (
    <AuthGuard>
      <AlertsScreen />
    </AuthGuard>
  );
}

function AlertsScreen() {
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [openCount, setOpenCount] = useState(0);
  const [unreadCount, setUnreadCount] = useState(0);
  const [tab, setTab] = useState<Tab>("open");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api<{ items: Alert[]; open_count: number; unread_count: number }>(
        `/api/alerts${tab === "open" ? "?status=open" : ""}`
      );
      setAlerts(data.items);
      setOpenCount(data.open_count);
      setUnreadCount(data.unread_count);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load alerts");
    } finally {
      setLoading(false);
    }
  }, [tab]);

  useEffect(() => {
    void load();
  }, [load]);

  async function refresh() {
    setRefreshing(true);
    try {
      // State-based: re-evaluates conditions; duplicates are prevented server-side.
      await api("/api/alerts/refresh", { method: "POST" });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Refresh failed");
    } finally {
      setRefreshing(false);
    }
  }

  async function markRead(a: Alert) {
    if (a.is_read || busyId) return;
    setBusyId(a.id);
    try {
      await api(`/api/alerts/${a.id}/read`, { method: "POST" });
      await load();
    } finally {
      setBusyId(null);
    }
  }

  async function acknowledge(a: Alert) {
    if (busyId) return;
    setBusyId(a.id);
    try {
      await api(`/api/alerts/${a.id}/acknowledge`, {
        method: "POST",
        json: { note: "Acknowledged from Alerts page" },
      });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to acknowledge");
    } finally {
      setBusyId(null);
    }
  }

  async function dismiss(a: Alert) {
    if (busyId) return;
    setBusyId(a.id);
    try {
      await api(`/api/alerts/${a.id}/dismiss`, {
        method: "POST",
        json: { note: "Dismissed from Alerts page" },
      });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to dismiss");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <AppShell>
      <div className="space-y-5">
        <PageHeader
          title="Alerts"
          description="Inventory, expiry and reorder signals generated from real store state."
          icon={Bell}
          action={
            <button type="button" className="btn btn-primary" onClick={() => void refresh()} disabled={refreshing}>
              <RefreshCw className={cn("h-4 w-4", refreshing && "animate-spin")} />
              Re-check store
            </button>
          }
        />

        <div className="grid gap-4 sm:grid-cols-3">
          <div className="card p-4">
            <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">Open alerts</p>
            <p className="mt-1 text-2xl font-bold">{openCount}</p>
          </div>
          <div className="card p-4">
            <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">Unread</p>
            <p className="mt-1 text-2xl font-bold">{unreadCount}</p>
          </div>
          <div className="card p-4">
            <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">Note</p>
            <p className="mt-1 text-[12px] leading-snug text-text-secondary">
              Alerts are state-based — the same issue is never duplicated on every refresh.
            </p>
          </div>
        </div>

        <div className="flex gap-2">
          {(
            [
              ["open", "Open"],
              ["all", "All"],
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

        {loading ? <LoadingState /> : null}
        {error && !loading ? <ErrorState message={error} onRetry={() => void load()} /> : null}

        {!loading && !error ? (
          alerts.length === 0 ? (
            <EmptyState
              title={tab === "open" ? "No open alerts" : "No alerts recorded"}
              description={
                tab === "open"
                  ? "Nothing needs your attention right now. The system re-checks stock, expiry and reorder conditions automatically."
                  : "Alerts appear here when stock runs low, batches near expiry, or reorders are required."
              }
            />
          ) : (
            <div className="space-y-3">
              {alerts.map((a) => (
                <div
                  key={a.id}
                  className={cn(
                    "card p-4",
                    a.severity === "critical" && "border-[#fecaca]",
                    !a.is_read && "bg-[#f8fbff]"
                  )}
                >
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="flex flex-wrap items-center gap-2 text-sm font-bold">
                        <StatusBadge
                          label={a.severity}
                          tone={SEV_TONE[a.severity] ?? "gray"}
                        />
                        <StatusBadge label={a.type.replace(/_/g, " ").toLowerCase()} tone="gray" />
                        {!a.is_read ? (
                          <span className="rounded-full bg-[#e8f2ff] px-1.5 py-0.5 text-[9px] font-bold text-primary">
                            NEW
                          </span>
                        ) : null}
                      </p>
                      <p className="mt-1.5 text-sm font-semibold">{a.title}</p>
                      {a.description ? (
                        <p className="mt-0.5 text-[12px] leading-relaxed text-text-secondary">
                          {a.description}
                        </p>
                      ) : null}
                      <p className="mt-1 text-[11px] text-text-muted">
                        {formatDateTime(a.created_at)}
                        {a.status !== "open" ? ` · ${a.status}` : ""}
                      </p>
                    </div>
                    {a.status === "open" ? (
                      <div className="flex shrink-0 gap-1.5">
                        {!a.is_read ? (
                          <button
                            type="button"
                            className="btn btn-ghost text-xs"
                            onClick={() => void markRead(a)}
                            disabled={busyId === a.id}
                            title="Mark as read"
                          >
                            <Eye className="h-3.5 w-3.5" />
                            Read
                          </button>
                        ) : null}
                        <button
                          type="button"
                          className="btn btn-secondary text-xs"
                          onClick={() => void acknowledge(a)}
                          disabled={busyId === a.id}
                        >
                          <Check className="h-3.5 w-3.5" />
                          Acknowledge
                        </button>
                        <button
                          type="button"
                          className="btn btn-ghost text-xs"
                          onClick={() => void dismiss(a)}
                          disabled={busyId === a.id}
                        >
                          <BellOff className="h-3.5 w-3.5" />
                          Dismiss
                        </button>
                      </div>
                    ) : null}
                  </div>
                </div>
              ))}
            </div>
          )
        ) : null}
      </div>
    </AppShell>
  );
}
