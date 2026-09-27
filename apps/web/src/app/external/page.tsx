"use client";

import { useCallback, useEffect, useState } from "react";
import {
  CheckCircle2,
  ExternalLink,
  Globe,
  Info,
  Plus,
  Rss,
  ShieldCheck,
  XCircle,
} from "lucide-react";
import { AppShell, AuthGuard } from "@/components/shell";
import { PageHeader } from "@/components/page-header";
import { EmptyState, ErrorState, LoadingState, SuccessBanner } from "@/components/states";
import { StatusBadge } from "@/components/status-badge";
import { api } from "@/lib/api";
import { formatDateTime } from "@/lib/types";

type Signal = {
  id: string;
  type: string;
  title: string;
  statement: string;
  category: string | null;
  region: string | null;
  confidence: "HIGH" | "MEDIUM" | "LOW";
};

type EvidenceItem = {
  id: string;
  source_kind: string;
  source_name: string;
  source_url: string | null;
  title: string;
  summary: string | null;
  published_at: string | null;
  retrieved_at: string;
  region: string | null;
  category: string | null;
  verification_status: string;
  trust_tier: string;
};

type Source = {
  id: string;
  source_key: string;
  name: string;
  kind: string;
  base_url: string;
  trust_tier: string;
  region: string;
  enabled: boolean;
  last_fetched_at: string | null;
  evidence_count: number;
};

export default function ExternalIntelPage() {
  return (
    <AuthGuard>
      <ExternalScreen />
    </AuthGuard>
  );
}

function ExternalScreen() {
  const [signals, setSignals] = useState<Signal[]>([]);
  const [evidence, setEvidence] = useState<EvidenceItem[]>([]);
  const [sources, setSources] = useState<Source[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ title: "", summary: "", category: "", source_url: "" });

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [ctx, ev, src] = await Promise.all([
        api<{ signals: Signal[] }>("/api/external/context"),
        api<{ items: EvidenceItem[] }>("/api/external/evidence"),
        api<{ items: Source[] }>("/api/external/sources"),
      ]);
      setSignals(ctx.signals);
      setEvidence(ev.items);
      setSources(src.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load external intelligence");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function refreshSources() {
    setBusy(true);
    setSuccess(null);
    try {
      const res = await api<{ sources: { source_key: string; candidates: number; stored: number; error: string | null }[] }>(
        "/api/external/refresh",
        { method: "POST" }
      );
      const ok = res.sources.filter((s) => !s.error);
      const failed = res.sources.filter((s) => s.error);
      setSuccess(
        `Refreshed ${ok.length} source(s): ${ok.reduce((a, s) => a + s.stored, 0)} new item(s) stored` +
          (failed.length ? `. ${failed.length} failed (see sources below).` : ".")
      );
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Refresh failed");
    } finally {
      setBusy(false);
    }
  }

  async function toggleSource(s: Source) {
    setBusy(true);
    try {
      await api(`/api/external/sources/${s.id}/enable`, {
        method: "POST",
        json: { enabled: !s.enabled },
      });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not update source");
    } finally {
      setBusy(false);
    }
  }

  async function verify(id: string, decision: "VERIFIED" | "REJECTED") {
    setBusy(true);
    try {
      await api(`/api/external/evidence/${id}/verify`, { method: "POST", json: { decision } });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Verification failed");
    } finally {
      setBusy(false);
    }
  }

  async function addManual(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await api("/api/external/evidence", {
        method: "POST",
        json: {
          title: form.title,
          summary: form.summary || null,
          category: form.category || null,
          source_url: form.source_url || null,
        },
      });
      setSuccess("Observation recorded — it counts as merchant-verified external context.");
      setForm({ title: "", summary: "", category: "", source_url: "" });
      setShowForm(false);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save observation");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AppShell>
      <div className="space-y-5">
        <PageHeader
          title="External Intelligence"
          description="Verified outside signals — always labelled, never mixed with your store data."
          icon={Globe}
          action={
            <>
              <button type="button" className="btn btn-secondary" onClick={() => setShowForm((v) => !v)}>
                <Plus className="h-4 w-4" />
                Add observation
              </button>
              <button type="button" className="btn btn-primary" disabled={busy} onClick={refreshSources}>
                <Rss className="h-4 w-4" />
                Refresh sources
              </button>
            </>
          }
        />

        <div className="card flex items-start gap-3 bg-[#eff6ff] p-4">
          <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-primary" />
          <p className="text-sm text-text-secondary">
            <span className="font-semibold text-foreground">Separation rule:</span> everything on this page
            comes from listed external sources with full provenance (source, URL, dates, region). It is
            never treated as your store&apos;s truth and never feeds pricing or reorder math.
          </p>
        </div>

        {success ? <SuccessBanner message={success} /> : null}

        {showForm ? (
          <form onSubmit={addManual} className="card space-y-3 p-5">
            <p className="text-sm font-semibold">Record an observation (counts as merchant-verified)</p>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="block text-sm">
                <span className="mb-1 block font-medium">Title *</span>
                <input className="input" required minLength={4} value={form.title}
                  onChange={(e) => setForm({ ...form, title: e.target.value })} />
              </label>
              <label className="block text-sm">
                <span className="mb-1 block font-medium">Category</span>
                <input className="input" value={form.category}
                  onChange={(e) => setForm({ ...form, category: e.target.value })} placeholder="e.g. Snacks" />
              </label>
            </div>
            <label className="block text-sm">
              <span className="mb-1 block font-medium">What did you observe / learn?</span>
              <textarea className="input min-h-20" value={form.summary}
                onChange={(e) => setForm({ ...form, summary: e.target.value })} />
            </label>
            <label className="block text-sm">
              <span className="mb-1 block font-medium">Source URL (if any)</span>
              <input className="input" value={form.source_url}
                onChange={(e) => setForm({ ...form, source_url: e.target.value })} placeholder="https://…" />
            </label>
            <button type="submit" className="btn btn-primary" disabled={busy}>Save observation</button>
          </form>
        ) : null}

        {loading ? <LoadingState label="Loading external intelligence" /> : null}
        {error && !loading ? <ErrorState message={error} onRetry={() => void load()} /> : null}

        {!loading && !error ? (
          <>
            {/* Verified signals */}
            <section className="card p-5">
              <h2 className="text-base font-bold">Verified signals</h2>
              <p className="mb-4 text-xs text-text-muted">Derived only from VERIFIED evidence; confidence reflects source trust and corroboration.</p>
              {signals.length === 0 ? (
                <EmptyState
                  title="No external intelligence available"
                  description="Nothing verified is on record. Enable a source and refresh, or add your own observation — no market claims will be shown without evidence."
                />
              ) : (
                <ul className="space-y-3">
                  {signals.map((s) => (
                    <li key={s.id} className="rounded-xl border border-border p-3">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <p className="text-sm font-semibold">{s.title}</p>
                        <StatusBadge
                          label={`${s.confidence.toLowerCase()} confidence`}
                          tone={s.confidence === "HIGH" ? "success" : s.confidence === "MEDIUM" ? "info" : "warning"}
                        />
                      </div>
                      <p className="mt-1 text-sm text-text-secondary">{s.statement}</p>
                      <p className="mt-1 text-[11px] text-text-muted">
                        {s.category ? `${s.category} · ` : ""}{s.region || "all-India"} · external source, not store data
                      </p>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            {/* Evidence review queue */}
            <section className="card p-5">
              <h2 className="text-base font-bold">Evidence</h2>
              <p className="mb-4 text-xs text-text-muted">
                Every stored item with provenance. UNVERIFIED items stay out of signals until you verify them.
              </p>
              {evidence.length === 0 ? (
                <EmptyState
                  title="No evidence collected yet"
                  description="Refresh an enabled source or record your own observation."
                />
              ) : (
                <ul className="space-y-3">
                  {evidence.map((e) => (
                    <li key={e.id} className="rounded-xl border border-border p-3">
                      <div className="flex flex-wrap items-start justify-between gap-2">
                        <div className="min-w-0">
                          <p className="text-sm font-semibold">{e.title}</p>
                          {e.summary ? <p className="mt-0.5 text-xs text-text-secondary">{e.summary}</p> : null}
                          <p className="mt-1 text-[11px] text-text-muted">
                            {e.source_name}
                            {e.source_url ? (
                              <> · <a href={e.source_url} target="_blank" rel="noopener noreferrer" className="text-primary underline">source <ExternalLink className="inline h-3 w-3" /></a></>
                            ) : null}
                            {" "}· retrieved {formatDateTime(e.retrieved_at)}
                            {e.published_at ? ` · published ${formatDateTime(e.published_at)}` : ""}
                            {e.region ? ` · ${e.region}` : ""}
                          </p>
                        </div>
                        <div className="flex shrink-0 items-center gap-2">
                          <StatusBadge label={e.trust_tier.toLowerCase()} tone={e.trust_tier === "OFFICIAL" ? "success" : "neutral"} />
                          <StatusBadge
                            label={e.verification_status.toLowerCase()}
                            tone={e.verification_status === "VERIFIED" ? "success" : e.verification_status === "UNVERIFIED" ? "warning" : "neutral"}
                          />
                        </div>
                      </div>
                      {e.verification_status === "UNVERIFIED" ? (
                        <div className="mt-2 flex gap-2">
                          <button type="button" className="btn btn-secondary h-8 text-xs" disabled={busy} onClick={() => verify(e.id, "VERIFIED")}>
                            <CheckCircle2 className="h-3.5 w-3.5" />
                            Verify
                          </button>
                          <button type="button" className="btn btn-ghost h-8 text-xs" disabled={busy} onClick={() => verify(e.id, "REJECTED")}>
                            <XCircle className="h-3.5 w-3.5" />
                            Reject
                          </button>
                        </div>
                      ) : null}
                    </li>
                  ))}
                </ul>
              )}
            </section>

            {/* Source registry */}
            <section className="card p-5">
              <h2 className="text-base font-bold">Source registry</h2>
              <p className="mb-4 text-xs text-text-muted">
                Opt-in only. RSS sources respect robots.txt; nothing is fetched unless enabled and refreshed.
              </p>
              <ul className="space-y-2">
                {sources.map((s) => (
                  <li key={s.id} className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-border px-3 py-2.5">
                    <div>
                      <p className="text-sm font-semibold">{s.name}</p>
                      <p className="text-[11px] text-text-muted">
                        {s.kind} · {s.trust_tier} · {s.region}
                        {s.last_fetched_at ? ` · last fetch ${formatDateTime(s.last_fetched_at)}` : ""}
                        {` · ${s.evidence_count} item(s)`}
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      <StatusBadge label={s.enabled ? "enabled" : "disabled"} tone={s.enabled ? "success" : "neutral"} />
                      <button
                        type="button"
                        className="btn btn-secondary h-8 text-xs"
                        disabled={busy || s.kind === "manual"}
                        onClick={() => toggleSource(s)}
                      >
                        {s.enabled ? "Disable" : "Enable"}
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
              <p className="mt-3 flex items-start gap-1.5 text-[11px] text-text-muted">
                <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                OFFICIAL sources (government) auto-verify; all others require your review before their
                statements appear as signals.
              </p>
            </section>
          </>
        ) : null}
      </div>
    </AppShell>
  );
}
