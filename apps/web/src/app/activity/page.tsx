"use client";

import { useCallback, useEffect, useState } from "react";
import { History } from "lucide-react";
import { AppShell, AuthGuard } from "@/components/shell";
import { PageHeader } from "@/components/page-header";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { api } from "@/lib/api";
import { formatDateTime } from "@/lib/types";

type Activity = {
  id: string;
  event_type: string;
  entity_type: string | null;
  message: string | null;
  created_at: string;
};

export default function ActivityPage() {
  return (
    <AuthGuard>
      <ActivityScreen />
    </AuthGuard>
  );
}

function ActivityScreen() {
  const [items, setItems] = useState<Activity[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api<{ items: Activity[] }>("/api/activity?limit=100");
      setItems(res.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load activity");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <AppShell>
      <div className="space-y-5">
        <PageHeader
          title="Activity"
          description="Audit log of important store mutations."
          icon={History}
        />
        {loading ? <LoadingState /> : null}
        {error && !loading ? <ErrorState message={error} onRetry={() => void load()} /> : null}
        {!loading && !error ? (
          items.length === 0 ? (
            <EmptyState title="No activity yet" description="Actions will appear here after you use the app." />
          ) : (
            <div className="card divide-y divide-[#f1f5f9]">
              {items.map((a) => (
                <div key={a.id} className="flex items-start justify-between gap-3 px-4 py-3">
                  <div>
                    <p className="text-sm font-medium">{a.message || a.event_type}</p>
                    <p className="text-[11px] text-text-muted">
                      {a.event_type}
                      {a.entity_type ? ` · ${a.entity_type}` : ""}
                    </p>
                  </div>
                  <span className="shrink-0 text-[11px] text-text-muted">
                    {formatDateTime(a.created_at)}
                  </span>
                </div>
              ))}
            </div>
          )
        ) : null}
      </div>
    </AppShell>
  );
}
