"use client";

import { useState } from "react";
import {
  AlertTriangle,
  ArrowUpRight,
  CalendarClock,
  Check,
  ChevronDown,
  Loader2,
  Package,
  TrendingDown,
  Users,
  X,
} from "lucide-react";
import { api } from "@/lib/api";
import { formatINR } from "@/lib/types";
import { cn } from "@/lib/cn";

export type Recommendation = {
  id: string;
  type: string;
  title: string;
  summary: string | null;
  description?: string | null;
  severity: string;
  priority: number;
  priority_reason: string | null;
  evidence: Record<string, unknown> | { text?: string };
  data_sources?: string[];
  reasoning_summary?: string | null;
  proposed_action: Record<string, unknown> | { text?: string };
  estimated_impact: string | null;
  risk: string | null;
  status: string;
};

const TYPE_ICON: Record<string, typeof AlertTriangle> = {
  EXPIRY_RISK: CalendarClock,
  REORDER_REQUIRED: Package,
  MARGIN_RISK: TrendingDown,
  CUSTOMER_OPPORTUNITY: Users,
};

const TYPE_LABEL: Record<string, string> = {
  EXPIRY_RISK: "Expiry risk",
  REORDER_REQUIRED: "Reorder required",
  MARGIN_RISK: "Margin risk",
  CUSTOMER_OPPORTUNITY: "Customer opportunity",
};

const SEV_TONE: Record<string, string> = {
  critical: "bg-[#fee2e2] text-[#dc2626]",
  warning: "bg-[#ffedd5] text-[#f97316]",
  info: "bg-[#e8f2ff] text-[#2080f0]",
};

const ACTION_LABEL: Record<string, string> = {
  price_change: "Review pricing",
  create_purchase: "Review purchase",
  inventory_adjust: "Review stock change",
};

function EvidenceRows({ evidence }: { evidence: Record<string, unknown> }) {
  const skip = new Set(["text", "product_id", "product_name"]);
  const rows = Object.entries(evidence).filter(([k]) => !skip.has(k));
  return (
    <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-[12px] sm:grid-cols-3">
      {rows.slice(0, 9).map(([k, v]) => (
        <div key={k} className="min-w-0">
          <dt className="truncate text-text-muted">{k.replace(/_/g, " ")}</dt>
          <dd className="truncate font-semibold text-foreground">
            {typeof v === "number" && /value|cost|price|spend|purchase/i.test(k)
              ? formatINR(v)
              : String(v)}
          </dd>
        </div>
      ))}
    </dl>
  );
}

export function RecommendationCard({
  reco,
  onChanged,
  onReview,
}: {
  reco: Recommendation;
  onChanged?: () => void;
  /** Called with the proposed action; the parent prepares it and opens the preview modal. */
  onReview?: (actionType: string, payload: Record<string, unknown>, recommendationId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const Icon = TYPE_ICON[reco.type] ?? AlertTriangle;
  const action: Record<string, unknown> =
    reco.proposed_action && !Array.isArray(reco.proposed_action)
      ? (reco.proposed_action as Record<string, unknown>)
      : {};
  const actionType = typeof action.action_type === "string" ? action.action_type : null;

  async function setStatus(status: string) {
    setBusy(true);
    setError(null);
    try {
      await api(`/api/agent/recommendations/${reco.id}/status`, {
        method: "POST",
        json: { status, note: `From recommendation card (${status.toLowerCase()})` },
      });
      onChanged?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Action failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={cn("card p-4", reco.severity === "critical" && "border-[#fecaca]")}>
      <div className="flex items-start justify-between gap-3">
        <div className="flex gap-3">
          <span
            className={cn(
              "flex h-10 w-10 shrink-0 items-center justify-center rounded-xl",
              SEV_TONE[reco.severity] ?? SEV_TONE.info
            )}
          >
            <Icon className="h-5 w-5" />
          </span>
          <div className="min-w-0">
            <p className="flex flex-wrap items-center gap-2 text-[11px] font-bold uppercase tracking-wide text-text-muted">
              {TYPE_LABEL[reco.type] ?? reco.type.replace(/_/g, " ").toLowerCase()}
              <span className="rounded-full bg-[#f1f5f9] px-1.5 py-0.5 font-bold text-foreground">
                priority {reco.priority}/100
              </span>
            </p>
            <p className="mt-0.5 text-sm font-bold text-foreground">{reco.title}</p>
            <p className="mt-1 text-[13px] leading-relaxed text-text-secondary">
              {reco.summary || reco.description}
            </p>
            {reco.estimated_impact ? (
              <p className="mt-1.5 text-[12px] font-semibold text-foreground">
                Impact: {reco.estimated_impact}
              </p>
            ) : null}
          </div>
        </div>
      </div>

      {reco.priority_reason ? (
        <p className="mt-2 rounded-lg bg-[#f8fafc] px-2.5 py-1.5 text-[11px] text-text-secondary">
          <strong>Why this priority:</strong> {reco.priority_reason}
        </p>
      ) : null}

      <EvidenceRows evidence={reco.evidence} />

      {error ? <p className="mt-2 text-xs text-[#b91c1c]">{error}</p> : null}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        {actionType && onReview ? (
          <button
            type="button"
            className="btn btn-primary text-xs"
            disabled={busy}
            onClick={() => onReview(actionType, action, reco.id)}
          >
            <ArrowUpRight className="h-3.5 w-3.5" />
            {ACTION_LABEL[actionType] ?? "Review action"}
          </button>
        ) : null}
        <button
          type="button"
          className="btn btn-ghost text-xs"
          disabled={busy}
          onClick={() => void setStatus("DISMISSED")}
        >
          <X className="h-3.5 w-3.5" />
          Dismiss
        </button>
        <button
          type="button"
          className="btn btn-ghost text-xs"
          onClick={() => setOpen((v) => !v)}
        >
          <ChevronDown className={cn("h-3.5 w-3.5 transition", open && "rotate-180")} />
          Details
        </button>
      </div>

      {open ? (
        <div className="mt-3 space-y-2 border-t border-border pt-3 text-[12px] text-text-secondary">
          {reco.reasoning_summary ? (
            <p>
              <strong className="text-foreground">Reasoning:</strong> {reco.reasoning_summary}
            </p>
          ) : null}
          {reco.risk ? (
            <p>
              <strong className="text-foreground">Risk:</strong> {reco.risk}
            </p>
          ) : null}
          {reco.data_sources && reco.data_sources.length > 0 ? (
            <p>
              <strong className="text-foreground">Data sources:</strong>{" "}
              {reco.data_sources.join(", ")}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
