"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Check, Loader2, X } from "lucide-react";
import { api } from "@/lib/api";
import { formatINR } from "@/lib/types";
import { cn } from "@/lib/cn";

type Preview = Record<string, unknown>;

export function ActionPreviewModal({
  actionId,
  onExecuted,
  onClose,
}: {
  actionId: string;
  onExecuted?: () => void;
  onClose: () => void;
}) {
  const [action, setAction] = useState<{
    id: string;
    action_type: string;
    status: string;
    preview: Preview;
  } | null>(null);
  const [loading, setLoading] = useState(true);
  const [approving, setApproving] = useState(false);
  const [stale, setStale] = useState<{ message: string; updated_preview: Preview } | null>(null);
  const [executed, setExecuted] = useState<{ message: string; duplicate: boolean } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const a = await api<{ id: string; action_type: string; status: string; preview: Preview }>(
        `/api/agent/actions/${actionId}`
      );
      setAction(a);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load action");
    } finally {
      setLoading(false);
    }
  }, [actionId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function approve(force: boolean) {
    if (approving) return;
    setApproving(true);
    setError(null);
    try {
      const res = await api<{
        status: string;
        message?: string;
        stale_fields?: unknown;
        updated_preview?: Preview;
        duplicate?: boolean;
        result?: Record<string, unknown>;
      }>(`/api/agent/actions/${actionId}/approve`, { method: "POST", json: { force } });
      if (res.status === "stale") {
        setStale({
          message: res.message || "State changed since this action was prepared.",
          updated_preview: res.updated_preview ?? {},
        });
      } else if (res.status === "executed") {
        setExecuted({
          message: res.duplicate
            ? "Already executed earlier — nothing was done twice."
            : "Action executed successfully.",
          duplicate: Boolean(res.duplicate),
        });
        onExecuted?.();
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Approval failed — nothing was changed.");
    } finally {
      setApproving(false);
    }
  }

  async function cancel() {
    setApproving(true);
    try {
      await api(`/api/agent/actions/${actionId}/cancel`, { method: "POST" });
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Cancel failed");
    } finally {
      setApproving(false);
    }
  }

  const preview = stale?.updated_preview ?? action?.preview ?? {};
  const rows = Object.entries(preview).filter(
    ([k]) => !["action_type", "note"].includes(k)
  );

  function renderValue(k: string, v: unknown) {
    if (v == null) return "—";
    if (typeof v === "object") return JSON.stringify(v);
    if (typeof v === "number" && /total|cost|price|value/i.test(k)) return formatINR(v);
    if (typeof v === "boolean") return v ? "yes" : "no";
    return String(v);
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4"
      role="dialog"
      aria-modal="true"
      aria-label="Action preview"
    >
      <div className="card w-full max-w-lg p-5">
        <div className="flex items-start justify-between gap-2">
          <h2 className="text-base font-bold">Action preview</h2>
          <button type="button" className="btn btn-ghost h-8 w-8 p-0" onClick={onClose} aria-label="Close">
            <X className="h-4 w-4" />
          </button>
        </div>

        {loading ? (
          <div className="mt-4 flex items-center gap-2 text-sm text-text-muted">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading action…
          </div>
        ) : (
          <>
            {executed ? (
              <div className="mt-3 flex items-center gap-2 rounded-xl border border-[#bbf7d0] bg-[#f0fdf4] px-3 py-2 text-sm text-[#15803d]">
                <Check className="h-4 w-4" />
                {executed.message}
              </div>
            ) : null}

            {stale ? (
              <div className="mt-3 rounded-xl border border-[#fde68a] bg-[#fffbeb] px-3 py-2 text-sm text-[#b45309]">
                <p className="flex items-center gap-1.5 font-semibold">
                  <AlertTriangle className="h-4 w-4" />
                  Recommendation no longer current
                </p>
                <p className="mt-1 text-[12px]">{stale.message}</p>
                <p className="mt-1 text-[11px]">
                  The preview below shows the current state. Approve again to proceed with the
                  updated numbers.
                </p>
              </div>
            ) : null}

            <dl className="mt-3 divide-y divide-[#f1f5f9] rounded-xl border border-border">
              {rows.map(([k, v]) => (
                <div key={k} className="flex items-center justify-between gap-3 px-3 py-2 text-sm">
                  <dt className="text-text-secondary">{k.replace(/_/g, " ")}</dt>
                  <dd className={cn("text-right font-semibold", k === "note" && "text-[11px] font-normal text-text-muted")}>
                    {k === "note" ? String(v) : renderValue(k, v)}
                  </dd>
                </div>
              ))}
            </dl>

            {error ? (
              <p className="mt-3 rounded-xl border border-[#fecaca] bg-[#fef2f2] px-3 py-2 text-sm text-[#b91c1c]">
                {error}
              </p>
            ) : null}

            <div className="mt-4 flex flex-wrap gap-2">
              {!executed ? (
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={() => void approve(stale ? true : false)}
                  disabled={approving}
                >
                  {approving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
                  {stale ? "Approve updated numbers" : "Approve & execute"}
                </button>
              ) : null}
              {!executed && action?.status === "prepared" ? (
                <button type="button" className="btn btn-ghost" onClick={() => void cancel()} disabled={approving}>
                  Cancel action
                </button>
              ) : null}
              <button type="button" className="btn btn-ghost" onClick={onClose}>
                Close
              </button>
            </div>

            <p className="mt-3 text-[11px] text-text-muted">
              The backend re-validates current state at approval time. If anything changed since
              preparation, you will see the updated numbers before anything executes.
            </p>
          </>
        )}
      </div>
    </div>
  );
}
