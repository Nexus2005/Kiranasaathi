"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import {
  BadgeCheck,
  Check,
  ChevronRight,
  CircleAlert,
  Megaphone,
  MessageSquareText,
  Send,
  ShieldCheck,
  Users,
} from "lucide-react";
import { AppShell, AuthGuard } from "@/components/shell";
import { PageHeader } from "@/components/page-header";
import { EmptyState, ErrorState, LoadingState, SuccessBanner } from "@/components/states";
import { StatusBadge } from "@/components/status-badge";
import { api } from "@/lib/api";
import { formatDateTime, formatINR } from "@/lib/types";

type Campaign = {
  id: string;
  name: string;
  campaign_type: string;
  status: string;
  recipient_count: number;
  sent_count: number;
  failed_count: number;
  created_at: string;
  sent_at: string | null;
};

type Product = { id: string; name: string; selling_price: string | number; quantity: number; category: string };

type AudiencePreview = {
  count: number;
  evidence: string;
  recipients: { customer_id: string; name: string; phone: string }[];
};

type CampaignDetail = {
  id: string;
  name: string;
  campaign_type: string;
  status: string;
  audience: { segment?: string; category?: string; product_id?: string; count_snapshot?: number; evidence?: string };
  products: { product_id: string; name: string; price: number; offer_text?: string }[];
  message_text: string | null;
  message_template: string | null;
  recipient_status_counts: Record<string, number>;
  provider: { provider_id: string; mode: string; note: string };
};

const STEPS = ["Audience", "Products", "Message", "Review", "Send"] as const;

const TYPE_LABELS: Record<string, string> = {
  festival: "Festival",
  new_product: "New product",
  discount: "Discount",
  inventory_clearance: "Clearance",
  re_engagement: "Re-engagement",
  product_recommendation: "Recommendation",
  general: "Announcement",
};

const SEGMENTS = [
  { value: "", label: "All consented customers" },
  { value: "INACTIVE_CUSTOMER", label: "Inactive (30+ days)" },
  { value: "HIGH_VALUE", label: "High value (₹5000+ lifetime)" },
  { value: "HIGH_FREQUENCY", label: "High frequency (8+ orders)" },
  { value: "NEW_CUSTOMER", label: "New customers (7 days)" },
];

export default function WhatsAppPage() {
  return (
    <AuthGuard>
      <WhatsAppScreen />
    </AuthGuard>
  );
}

function WhatsAppScreen() {
  const search = useSearchParams();
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [provider, setProvider] = useState<{ mode: string; provider_id: string; note: string } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showBuilder, setShowBuilder] = useState(!!search.get("type") || !!search.get("customer"));

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [c, p] = await Promise.all([
        api<{ items: Campaign[] }>("/api/campaigns"),
        api<{ mode: string; provider_id: string; note: string }>("/api/campaigns/provider"),
      ]);
      setCampaigns(c.items);
      setProvider(p);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load marketing data");
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
          title="WhatsApp & Marketing"
          description="Consent-based campaigns through an authorized channel — built from your real customer data."
          icon={Megaphone}
          action={
            <button type="button" className="btn btn-primary" onClick={() => setShowBuilder((v) => !v)}>
              <MessageSquareText className="h-4 w-4" />
              New campaign
            </button>
          }
        />

        {provider ? (
          <div className={`card flex items-start gap-3 p-4 ${provider.mode === "development" ? "bg-[#fffbeb]" : ""}`}>
            <ShieldCheck className={`mt-0.5 h-5 w-5 shrink-0 ${provider.mode === "development" ? "text-[#b45309]" : "text-success"}`} />
            <div>
              <p className="text-sm font-semibold">
                {provider.mode === "development"
                  ? "Development mode — no real messages are delivered"
                  : `Provider: ${provider.provider_id}`}
              </p>
              <p className="text-xs text-text-secondary">{provider.note}</p>
            </div>
          </div>
        ) : null}

        {showBuilder ? (
          <CampaignBuilder
            onDone={() => {
              setShowBuilder(false);
              void load();
            }}
            initialType={search.get("type") || undefined}
          />
        ) : null}

        {loading ? <LoadingState label="Loading campaigns" /> : null}
        {error && !loading ? <ErrorState message={error} onRetry={() => void load()} /> : null}

        {!loading && !error ? (
          <section className="card p-5">
            <h2 className="text-base font-bold">Campaigns</h2>
            {campaigns.length === 0 ? (
              <EmptyState
                title="No campaigns yet"
                description="Create a campaign to reach consented customers through WhatsApp."
              />
            ) : (
              <div className="mt-3 overflow-x-auto">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Campaign</th>
                      <th>Type</th>
                      <th>Status</th>
                      <th>Recipients</th>
                      <th>Sent / Failed</th>
                      <th>Created</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {campaigns.map((c) => (
                      <tr key={c.id}>
                        <td className="font-semibold">{c.name}</td>
                        <td className="text-text-secondary">{TYPE_LABELS[c.campaign_type] || c.campaign_type}</td>
                        <td>
                          <StatusBadge
                            label={c.status.replaceAll("_", " ").toLowerCase()}
                            tone={c.status === "SENT" ? "success" : c.status === "FAILED" ? "danger" : c.status === "PARTIALLY_SENT" ? "warning" : "info"}
                          />
                        </td>
                        <td>{c.recipient_count}</td>
                        <td>{c.sent_count} / {c.failed_count}</td>
                        <td className="text-text-secondary">{formatDateTime(c.created_at)}</td>
                        <td className="text-right">
                          <CampaignDetailLink id={c.id} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <p className="mt-3 text-[11px] text-text-muted">
              Delivery/read receipts and revenue attribution are not available from the current integration.
            </p>
          </section>
        ) : null}
      </div>
    </AppShell>
  );
}

function CampaignDetailLink({ id }: { id: string }) {
  return <span className="text-xs font-semibold text-primary">Details below after send →</span>;
}

// ================================================================ builder

function CampaignBuilder({
  onDone,
  initialType,
}: {
  onDone: () => void;
  initialType?: string;
}) {
  const search = useSearchParams();
  const [step, setStep] = useState(0);
  const [name, setName] = useState(
    initialType === "festival" ? "Festival campaign" : "Customer campaign"
  );
  const [campaignType, setCampaignType] = useState(initialType || "general");
  const [segment, setSegment] = useState("");
  const [category, setCategory] = useState("");
  const [audience, setAudience] = useState<AudiencePreview | null>(null);
  const [audienceLoading, setAudienceLoading] = useState(false);
  const [products, setProducts] = useState<Product[]>([]);
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [offers, setOffers] = useState<Record<string, string>>({});
  const [template, setTemplate] = useState(
    "Hello! We have new arrivals and offers waiting for you at our store."
  );
  const [detail, setDetail] = useState<CampaignDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [stepError, setStepError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);
  const [sendResult, setSendResult] = useState<Record<string, unknown> | null>(null);

  useEffect(() => {
    api<{ items: Product[] }>("/api/products")
      .then((res) => setProducts(res.items))
      .catch(() => setProducts([]));
  }, []);

  const categories = useMemo(
    () => Array.from(new Set(products.map((p) => p.category))).sort(),
    [products]
  );

  const previewAudience = useCallback(async () => {
    setAudienceLoading(true);
    setStepError(null);
    try {
      const params = new URLSearchParams();
      if (segment) params.set("segment", segment);
      if (category) params.set("category", category);
      const res = await api<AudiencePreview>(`/api/campaigns/audience-preview?${params.toString()}`);
      setAudience(res);
    } catch (err) {
      setAudience(null);
      setStepError(err instanceof Error ? err.message : "Audience check failed");
    } finally {
      setAudienceLoading(false);
    }
  }, [segment, category]);

  useEffect(() => {
    void previewAudience();
  }, [previewAudience]);

  const selectedProducts = products.filter((p) => selected[p.id]);

  async function createAndSubmit() {
    setBusy(true);
    setStepError(null);
    try {
      const created = await api<CampaignDetail>("/api/campaigns", {
        method: "POST",
        json: {
          name,
          campaign_type: campaignType,
          audience: {
            ...(segment ? { segment } : {}),
            ...(category ? { category } : {}),
          },
          products: selectedProducts.map((p) => ({
            product_id: p.id,
            offer_text: offers[p.id] || undefined,
          })),
          message_template: template,
        },
      });
      const submitted = await api<CampaignDetail>(`/api/campaigns/${created.id}/submit`, { method: "POST" });
      setDetail(submitted);
      setStep(3); // Review
    } catch (err) {
      setStepError(err instanceof Error ? err.message : "Could not create campaign");
    } finally {
      setBusy(false);
    }
  }

  async function approve() {
    if (!detail) return;
    setBusy(true);
    try {
      const approved = await api<CampaignDetail>(`/api/campaigns/${detail.id}/approve`, { method: "POST" });
      setDetail(approved);
      setStep(4);
    } catch (err) {
      setStepError(err instanceof Error ? err.message : "Approval failed");
    } finally {
      setBusy(false);
    }
  }

  async function send() {
    if (!detail) return;
    setBusy(true);
    setStepError(null);
    try {
      const result = await api<Record<string, unknown>>(`/api/campaigns/${detail.id}/send`, { method: "POST" });
      setSendResult(result);
      setSuccessMsg(String(result.delivery_note || "Campaign processed."));
    } catch (err) {
      setStepError(err instanceof Error ? err.message : "Send failed");
    } finally {
      setBusy(false);
    }
  }

  const canNext =
    step === 0 ? !!audience && audience.count > 0 :
    step === 1 ? selectedProducts.length > 0 :
    step === 2 ? template.trim().length > 0 : true;

  return (
    <section className="card p-5">
      <div className="mb-5 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-base font-bold">Campaign builder</h2>
        {/* stepper */}
        <div className="flex items-center gap-1">
          {STEPS.map((s, i) => (
            <div key={s} className="flex items-center">
              <span
                className={`flex h-7 w-7 items-center justify-center rounded-full text-xs font-bold ${
                  i < step ? "bg-primary text-white" : i === step ? "border-2 border-primary text-primary" : "border border-border text-text-muted"
                }`}
              >
                {i < step ? <Check className="h-3.5 w-3.5" /> : i + 1}
              </span>
              {i < STEPS.length - 1 ? <span className={`mx-1 h-0.5 w-4 ${i < step ? "bg-primary" : "bg-border"}`} /> : null}
            </div>
          ))}
        </div>
      </div>

      {stepError ? (
        <div className="mb-4 flex items-start gap-2 rounded-xl border border-[#fecaca] bg-[#fef2f2] px-3 py-2 text-sm text-[#b91c1c]">
          <CircleAlert className="mt-0.5 h-4 w-4 shrink-0" />
          {stepError}
        </div>
      ) : null}
      {successMsg ? <div className="mb-4"><SuccessBanner message={successMsg} /></div> : null}

      {/* STEP 0: audience */}
      {step === 0 ? (
        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block text-sm">
              <span className="mb-1 block font-medium">Campaign name *</span>
              <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
            </label>
            <label className="block text-sm">
              <span className="mb-1 block font-medium">Campaign type</span>
              <select className="input" value={campaignType} onChange={(e) => setCampaignType(e.target.value)}>
                {Object.entries(TYPE_LABELS).map(([v, l]) => (
                  <option key={v} value={v}>{l}</option>
                ))}
              </select>
            </label>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block text-sm">
              <span className="mb-1 block font-medium">Audience segment</span>
              <select className="input" value={segment} onChange={(e) => setSegment(e.target.value)}>
                {SEGMENTS.map((s) => (
                  <option key={s.value} value={s.value}>{s.label}</option>
                ))}
              </select>
            </label>
            <label className="block text-sm">
              <span className="mb-1 block font-medium">…or customers who bought category</span>
              <select className="input" value={category} onChange={(e) => setCategory(e.target.value)}>
                <option value="">Any category</option>
                {categories.map((c) => (
                  <option key={c} value={c}>{c}</option>
                ))}
              </select>
            </label>
          </div>
          <div className="rounded-xl bg-[#f8fafc] p-4">
            {audienceLoading ? (
              <p className="text-sm text-text-muted">Checking eligible customers…</p>
            ) : audience ? (
              <>
                <p className="flex items-center gap-2 text-sm font-semibold">
                  <Users className="h-4 w-4 text-primary" />
                  {audience.count} eligible recipient(s)
                </p>
                <p className="mt-1 text-xs text-text-secondary">{audience.evidence}</p>
                <p className="mt-2 text-[11px] text-text-muted">
                  Eligibility = phone on file + marketing consent + not opted out. Re-verified at send time.
                </p>
              </>
            ) : (
              <p className="text-sm text-text-muted">Select an audience to see the eligible count.</p>
            )}
          </div>
          {audience && audience.count === 0 ? (
            <p className="text-sm text-[#b45309]">
              No eligible customers for this campaign. Customers need a phone number and marketing consent on record.
            </p>
          ) : null}
        </div>
      ) : null}

      {/* STEP 1: products */}
      {step === 1 ? (
        <div className="space-y-3">
          <p className="text-sm text-text-secondary">
            Select products to feature. Prices always come from your database — the message cannot invent them.
          </p>
          {products.length === 0 ? (
            <EmptyState title="No products" description="Add products in Inventory first." />
          ) : (
            <ul className="max-h-72 space-y-2 overflow-y-auto pr-1">
              {products.map((p) => {
                const out = Number(p.quantity) <= 0;
                return (
                  <li
                    key={p.id}
                    className={`flex items-center justify-between rounded-xl border px-3 py-2.5 ${selected[p.id] ? "border-primary bg-[#e8f2ff]" : "border-border"} ${out ? "opacity-50" : ""}`}
                  >
                    <label className="flex flex-1 items-center gap-3">
                      <input
                        type="checkbox"
                        checked={!!selected[p.id]}
                        disabled={out}
                        onChange={(e) => setSelected((s) => ({ ...s, [p.id]: e.target.checked }))}
                      />
                      <span>
                        <span className="block text-sm font-semibold">{p.name}</span>
                        <span className="block text-[11px] text-text-muted">
                          {formatINR(Number(p.selling_price))} · stock {p.quantity}
                          {out ? " — unavailable, cannot be promoted" : ""}
                        </span>
                      </span>
                    </label>
                    {selected[p.id] ? (
                      <input
                        className="input h-8 w-44 text-xs"
                        placeholder="Offer text (optional)"
                        value={offers[p.id] || ""}
                        onChange={(e) => setOffers((o) => ({ ...o, [p.id]: e.target.value }))}
                      />
                    ) : null}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      ) : null}

      {/* STEP 2: message */}
      {step === 2 ? (
        <div className="space-y-3">
          <label className="block text-sm">
            <span className="mb-1 block font-medium">Message</span>
            <textarea
              className="input min-h-28"
              maxLength={800}
              value={template}
              onChange={(e) => setTemplate(e.target.value)}
            />
          </label>
          <div className="rounded-xl border border-border p-4">
            <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-text-muted">Preview (WhatsApp)</p>
            <div className="max-w-sm rounded-2xl bg-[#dcf8c6] p-3 text-sm shadow-sm">
              {template}
              {selectedProducts.length ? (
                <ul className="mt-2 border-t border-black/10 pt-2">
                  {selectedProducts.map((p) => (
                    <li key={p.id}>
                      • {p.name} ₹{Number(p.selling_price)}
                      {offers[p.id] ? ` (${offers[p.id]})` : ""}
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
            <p className="mt-2 text-[11px] text-text-muted">
              Product names and prices are inserted from your database at send time — the message can never show a price that is not real.
            </p>
          </div>
        </div>
      ) : null}

      {/* STEP 3: review */}
      {step === 3 && detail ? (
        <div className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-3">
            <ReviewStat label="Audience" value={`${detail.audience.count_snapshot ?? audience?.count ?? 0} recipient(s)`} />
            <ReviewStat label="Products" value={detail.products.map((p) => p.name).join(", ") || "none"} />
            <ReviewStat label="Channel" value={`WhatsApp (${detail.provider.mode})`} />
          </div>
          <div className="rounded-xl bg-[#f8fafc] p-4">
            <p className="whitespace-pre-line text-sm">{detail.message_text}</p>
          </div>
          <p className="text-xs text-text-secondary">{detail.audience.evidence}</p>
        </div>
      ) : null}

      {/* STEP 4: send/result */}
      {step === 4 ? (
        <div className="space-y-3">
          {sendResult ? (
            <>
              <div className="grid gap-3 sm:grid-cols-3">
                <ReviewStat label="Sent" value={String(sendResult.sent)} />
                <ReviewStat label="Failed" value={String(sendResult.failed)} />
                <ReviewStat label="Mode" value={String(sendResult.mode)} />
              </div>
              <p className="text-sm text-text-secondary">{String(sendResult.delivery_note)}</p>
              <p className="text-xs text-text-muted">
                Delivery receipts: not available from current integration. Revenue attribution: unavailable — the channel cannot reliably link sales to this campaign.
              </p>
            </>
          ) : detail ? (
            <p className="text-sm">Campaign approved and ready to send to {detail.audience.count_snapshot} recipient(s).</p>
          ) : null}
        </div>
      ) : null}

      {/* nav buttons */}
      <div className="mt-5 flex items-center justify-between">
        <button
          type="button"
          className="btn btn-ghost"
          disabled={step === 0 || busy}
          onClick={() => setStep((s) => Math.max(0, s - 1))}
        >
          Back
        </button>
        <div className="flex gap-2">
          {step < 2 ? (
            <button
              type="button"
              className="btn btn-primary"
              disabled={!canNext || busy}
              onClick={() => setStep((s) => s + 1)}
            >
              Next
              <ChevronRight className="h-4 w-4" />
            </button>
          ) : null}
          {step === 2 ? (
            <button type="button" className="btn btn-primary" disabled={!canNext || busy} onClick={createAndSubmit}>
              <BadgeCheck className="h-4 w-4" />
              Create & review
            </button>
          ) : null}
          {step === 3 ? (
            <button type="button" className="btn btn-primary" disabled={busy} onClick={approve}>
              <ShieldCheck className="h-4 w-4" />
              Approve campaign
            </button>
          ) : null}
          {step === 4 && !sendResult ? (
            <button type="button" className="btn btn-primary" disabled={busy} onClick={send}>
              <Send className="h-4 w-4" />
              Send now
            </button>
          ) : null}
          {step === 4 && sendResult ? (
            <button type="button" className="btn btn-secondary" onClick={onDone}>
              Done
            </button>
          ) : null}
        </div>
      </div>
    </section>
  );
}

function ReviewStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl bg-[#f8fafc] px-3 py-2.5">
      <p className="text-[11px] text-text-muted">{label}</p>
      <p className="truncate text-sm font-semibold">{value}</p>
    </div>
  );
}
