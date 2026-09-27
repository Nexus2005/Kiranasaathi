"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Bot,
  Check,
  ChevronRight,
  Cpu,
  Loader2,
  Send,
  Sparkles,
  Wrench,
} from "lucide-react";
import { AppShell, AuthGuard } from "@/components/shell";
import { PageHeader } from "@/components/page-header";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { StatusBadge } from "@/components/status-badge";
import { RecommendationCard, type Recommendation } from "@/components/recommendation-card";
import { ActionPreviewModal } from "@/components/action-preview-modal";
import { api } from "@/lib/api";
import { formatDateTime, formatINR } from "@/lib/types";
import { cn } from "@/lib/cn";

type AskResponse = {
  answer: string;
  intent: string;
  engine: string;
  tools_used: string[];
  recommendations: Recommendation[];
  brief?: {
    sales_today: number;
    orders_today: number;
    gross_profit_today: number;
    attention_count: number;
    top_concern: string | null;
  };
  recommendations_created?: number;
};

type Turn =
  | { role: "user"; text: string }
  | { role: "assistant"; data: AskResponse; at: string };

const SUGGESTIONS = [
  "What should I do today?",
  "What is selling fastest?",
  "What should I stock for next week?",
  "Diwali is coming. What should I prepare?",
  "Which customers should I contact?",
  "Create a campaign for customers who buy snacks",
  "Show products near expiry",
  "Why are my profits lower this week?",
];

export default function AiAssistantPage() {
  return (
    <AuthGuard>
      <AssistantScreen />
    </AuthGuard>
  );
}

function AssistantScreen() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [thinking, setThinking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [brief, setBrief] = useState<{
    sales: { today_sales: number; today_orders: number; gross_profit_today: number };
    attention: Record<string, number>;
    recommendations: Recommendation[];
    festivals_upcoming: { name: string; date: string; days_away: number; note: string }[];
  } | null>(null);
  const [briefLoading, setBriefLoading] = useState(true);
  const [openActionId, setOpenActionId] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const loadBrief = useCallback(async () => {
    setBriefLoading(true);
    try {
      const b = await api<{
        sales: { today_sales: number; today_orders: number; gross_profit_today: number };
        attention: Record<string, number>;
        recommendations: Recommendation[];
        festivals_upcoming: { name: string; date: string; days_away: number; note: string }[];
      }>("/api/agent/brief");
      setBrief(b);
    } catch {
      // brief is supplementary; chat still works without it
    } finally {
      setBriefLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadBrief();
  }, [loadBrief]);

  const scrollDown = () => {
    requestAnimationFrame(() => bottomRef.current?.scrollIntoView({ behavior: "smooth" }));
  };

  /** Prepare an action from a recommendation, then open the preview modal. */
  async function reviewAction(
    actionType: string,
    payload: Record<string, unknown>,
    recommendationId: string
  ) {
    setError(null);
    try {
      const res = await api<{ action_id: string }>('/api/agent/actions/prepare', {
        method: 'POST',
        json: { action_type: actionType, payload, recommendation_id: recommendationId },
      });
      setOpenActionId(res.action_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not prepare the action.');
    }
  }

  async function ask(question: string) {
    const q = question.trim();
    if (!q || thinking) return;
    setError(null);
    setTurns((prev) => [...prev, { role: "user", text: q }]);
    setThinking(true);
    setInput("");
    scrollDown();
    try {
      const res = await api<AskResponse>("/api/agent/ask", {
        method: "POST",
        json: { question: q },
      });
      setTurns((prev) => [...prev, { role: "assistant", data: res, at: new Date().toISOString() }]);
      void loadBrief();
    } catch (e) {
      setError(e instanceof Error ? e.message : "The assistant could not answer. Try again.");
    } finally {
      setThinking(false);
      scrollDown();
    }
  }

  return (
    <AppShell>
      <div className="space-y-5">
        <PageHeader
          title="AI Assistant"
          description="Business intelligence over your real store data — evidence-backed, action-ready."
          icon={Sparkles}
        />

        {briefLoading ? (
          <LoadingState label="Loading today's brief" />
        ) : brief ? (
          <section className="card p-5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <h2 className="text-base font-bold">Today&apos;s Business Brief</h2>
                <p className="text-xs text-text-muted">Every number comes from your store data.</p>
              </div>
              <StatusBadge
                label={`${brief.recommendations.length} need attention`}
                tone={brief.recommendations.length > 0 ? "warning" : "green"}
              />
            </div>
            <div className="mt-3 grid gap-3 sm:grid-cols-3">
              <div className="rounded-xl border border-border p-3">
                <p className="text-[11px] font-semibold uppercase text-text-muted">Sales today</p>
                <p className="text-lg font-bold">{formatINR(brief.sales.today_sales)}</p>
                <p className="text-[11px] text-text-muted">{brief.sales.today_orders} order(s)</p>
              </div>
              <div className="rounded-xl border border-border p-3">
                <p className="text-[11px] font-semibold uppercase text-text-muted">Gross profit</p>
                <p className="text-lg font-bold">{formatINR(brief.sales.gross_profit_today)}</p>
              </div>
              <div className="rounded-xl border border-border p-3">
                <p className="text-[11px] font-semibold uppercase text-text-muted">Festivals ahead</p>
                {brief.festivals_upcoming.length === 0 ? (
                  <p className="text-sm text-text-secondary">None in the next 30 days</p>
                ) : (
                  <p className="text-sm font-semibold">
                    {brief.festivals_upcoming[0].name} · {brief.festivals_upcoming[0].days_away}d
                  </p>
                )}
              </div>
            </div>
          </section>
        ) : null}

        <div className="grid gap-4 xl:grid-cols-5">
          {/* ---------- chat ---------- */}
          <section className="card flex h-[560px] flex-col p-4 xl:col-span-3">
            <div className="flex-1 space-y-4 overflow-y-auto pr-1">
              {turns.length === 0 ? (
                <div className="flex h-full flex-col items-center justify-center text-center">
                  <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-[#e8f2ff]">
                    <Bot className="h-6 w-6 text-primary" />
                  </span>
                  <p className="mt-3 text-sm font-bold">Ask about your store</p>
                  <p className="mt-1 max-w-sm text-xs text-text-muted">
                    Answers are computed from your actual sales, inventory, expiry and supplier
                    data — with the evidence shown. No invented numbers.
                  </p>
                  <div className="mt-4 flex flex-wrap justify-center gap-2">
                    {SUGGESTIONS.map((s) => (
                      <button
                        key={s}
                        type="button"
                        className="btn btn-ghost text-xs"
                        onClick={() => void ask(s)}
                      >
                        {s}
                      </button>
                    ))}
                  </div>
                </div>
              ) : null}

              {turns.map((t, i) =>
                t.role === "user" ? (
                  <div key={i} className="flex justify-end">
                    <p className="max-w-[85%] rounded-2xl rounded-br-sm bg-[#00baf2] px-3.5 py-2 text-sm font-medium text-white">
                      {t.text}
                    </p>
                  </div>
                ) : (
                  <div key={i} className="flex justify-start">
                    <div className="max-w-[90%] space-y-2">
                      <div className="rounded-2xl rounded-bl-sm border border-border bg-[#f8fafc] px-3.5 py-2.5">
                        <p className="whitespace-pre-wrap text-sm leading-relaxed text-foreground">
                          {t.data.answer}
                        </p>
                        <p className="mt-2 flex flex-wrap items-center gap-1.5 text-[10px] text-text-muted">
                          <Wrench className="h-3 w-3" />
                          {t.data.tools_used.join(" · ")}
                          <span className="rounded bg-[#e8f2ff] px-1 py-0.5 font-bold text-[#2080f0]">
                            {t.data.engine}
                          </span>
                        </p>
                      </div>
                    </div>
                  </div>
                )
              )}

              {thinking ? (
                <div className="flex items-center gap-2 text-sm text-text-muted">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Checking store data…
                </div>
              ) : null}
              {error ? (
                <div className="rounded-xl border border-[#fecaca] bg-[#fef2f2] px-3 py-2 text-sm text-[#b91c1c]">
                  {error}
                  <button
                    type="button"
                    className="ml-2 font-semibold underline"
                    onClick={() => setError(null)}
                  >
                    Dismiss
                  </button>
                </div>
              ) : null}
              <div ref={bottomRef} />
            </div>

            <form
              className="mt-3 flex gap-2 border-t border-border pt-3"
              onSubmit={(e) => {
                e.preventDefault();
                void ask(input);
              }}
            >
              <input
                className="input flex-1"
                placeholder="Ask: What should I do today?"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                disabled={thinking}
                maxLength={500}
              />
              <button type="submit" className="btn btn-primary" disabled={thinking || !input.trim()}>
                <Send className="h-4 w-4" />
              </button>
            </form>
          </section>

          {/* ---------- prioritized recommendations ---------- */}
          <section className="space-y-3 xl:col-span-2">
            <div className="flex items-center justify-between">
              <h2 className="flex items-center gap-2 text-base font-bold">
                <Cpu className="h-4 w-4 text-primary" />
                Recommendations
              </h2>
              <span className="text-[11px] text-text-muted">highest priority first</span>
            </div>
            {brief && brief.recommendations.length === 0 ? (
              <EmptyState
                title="Nothing needs attention"
                description="Ask the assistant anything, or check back after more sales come in."
              />
            ) : null}
            {(brief?.recommendations ?? []).map((r) => (
              <RecommendationCard
                key={r.id}
                reco={r}
                onChanged={() => void loadBrief()}
                onReview={(type, payload, recId) => void reviewAction(type, payload, recId)}
              />
            ))}
          </section>
        </div>

        {openActionId ? (
          <ActionPreviewModal
            actionId={openActionId}
            onClose={() => setOpenActionId(null)}
            onExecuted={() => void loadBrief()}
          />
        ) : null}
      </div>
    </AppShell>
  );
}
