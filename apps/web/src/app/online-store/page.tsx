"use client";

import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, Copy, Globe, Link2, Plug, RefreshCw, TriangleAlert } from "lucide-react";
import { AppShell, AuthGuard } from "@/components/shell";
import { PageHeader } from "@/components/page-header";
import { LoadingState } from "@/components/states";
import { StatusBadge } from "@/components/status-badge";
import { api } from "@/lib/api";
import { formatINR } from "@/lib/types";
import { cn } from "@/lib/cn";

type Settings = {
  store_id: string;
  slug: string | null;
  is_published: boolean;
  show_stock: boolean;
  allow_guest_checkout: boolean;
  delivery_fee: number;
  min_order_amount: number;
};

type QcProvider = {
  provider: string;
  status: string;
  mode: string;
  connected_account: string | null;
  last_sync_at: string | null;
  last_error: string | null;
  note?: string;
};

const STATUS_TONE: Record<string, string> = {
  CONNECTED: "ok",
  DEMO: "info",
  NOT_CONFIGURED: "muted",
  AUTH_REQUIRED: "warn",
  ERROR: "danger",
  SYNCING: "info",
};

export default function OnlineStorePage() {
  return (
    <AuthGuard>
      <OnlineStore />
    </AuthGuard>
  );
}

function OnlineStore() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [providers, setProviders] = useState<QcProvider[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [busyProvider, setBusyProvider] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ tone: "ok" | "err" | "info"; text: string } | null>(null);
  const [slugDraft, setSlugDraft] = useState("");
  const [copied, setCopied] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [s, p] = await Promise.all([
        api<Settings>("/api/commerce/catalog-settings"),
        api<{ items: QcProvider[] }>("/api/qc/providers"),
      ]);
      setSettings(s);
      setSlugDraft(s.slug || "");
      setProviders(p.items);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function saveSlug() {
    setSaving(true);
    setNotice(null);
    try {
      await api("/api/commerce/slug", { method: "POST", json: { slug: slugDraft.trim() } });
      setNotice({ tone: "ok", text: "Store link saved." });
      await load();
    } catch (err) {
      setNotice({ tone: "err", text: err instanceof Error ? err.message : "Could not save link" });
    } finally {
      setSaving(false);
    }
  }

  async function patchSettings(patch: Partial<Settings>) {
    setSaving(true);
    try {
      const s = await api<Settings>("/api/commerce/catalog-settings", { method: "PATCH", json: patch });
      setSettings(s);
      setNotice({ tone: "ok", text: "Settings saved." });
    } catch (err) {
      setNotice({ tone: "err", text: err instanceof Error ? err.message : "Could not save" });
    } finally {
      setSaving(false);
    }
  }

  async function configure(provider: string, mode: "demo" | "unconfigured") {
    setBusyProvider(provider);
    setNotice(null);
    try {
      const res = await api<QcProvider>("/api/qc/providers/configure", {
        method: "POST",
        json: { provider, mode },
      });
      setNotice({
        tone: res.mode === "demo" ? "info" : "ok",
        text:
          res.mode === "demo"
            ? `${provider} is now in DEMO / SIMULATED mode — it exercises your real order pipeline and is clearly labeled, never shown as live.`
            : `${provider} disabled.`,
      });
      await load();
    } catch (err) {
      setNotice({ tone: "err", text: err instanceof Error ? err.message : "Configuration failed" });
    } finally {
      setBusyProvider(null);
    }
  }

  async function sync(provider: string) {
    setBusyProvider(provider);
    setNotice(null);
    try {
      const res = await api<{ synced: number; errors: number; mode: string }>(`/api/qc/${provider}/sync`, {
        method: "POST",
        json: {},
      });
      setNotice({
        tone: res.errors > 0 ? "info" : "ok",
        text: `${provider} catalog sync (${res.mode}): ${res.synced} synced, ${res.errors} skipped/blocked by price rules.`,
      });
      await load();
    } catch (err) {
      setNotice({ tone: "err", text: err instanceof Error ? err.message : "Sync failed" });
    } finally {
      setBusyProvider(null);
    }
  }

  const storeUrl = settings?.slug ? `${typeof window !== "undefined" ? window.location.origin : ""}/store/${settings.slug}` : null;

  if (loading) {
    return (
      <AppShell>
        <LoadingState />
      </AppShell>
    );
  }

  return (
    <AppShell>
      <div className="space-y-5">
        <PageHeader
          title="Online Store & Integrations"
          description="Share a customer ordering link, publish your catalog, and see the truthful status of every external integration."
          icon={Globe}
        />

        {notice ? (
          <div
            className={cn(
              "rounded-xl border px-4 py-3 text-sm",
              notice.tone === "ok"
                ? "border-[#bbf7d0] bg-[#f0fdf4] text-[#15803d]"
                : notice.tone === "err"
                  ? "border-[#fecaca] bg-[#fef2f2] text-[#b91c1c]"
                  : "border-[#bfdbfe] bg-[#eff6ff] text-[#1d4ed8]"
            )}
          >
            {notice.text}
          </div>
        ) : null}

        {/* Store link */}
        <section className="card p-5">
          <h2 className="mb-3 flex items-center gap-2 text-base font-bold">
            <Link2 className="h-4 w-4 text-primary" /> Your store link
          </h2>
          <div className="flex gap-2">
            <input
              className="input"
              placeholder="choose-a-name"
              value={slugDraft}
              onChange={(e) => setSlugDraft(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ""))}
            />
            <button type="button" className="btn btn-secondary" disabled={saving} onClick={() => void saveSlug()}>
              Save
            </button>
          </div>
          {storeUrl && settings?.slug ? (
            <div className="mt-3 flex items-center justify-between rounded-xl bg-[#f8fafc] px-3 py-2.5">
              <a href={storeUrl} target="_blank" rel="noreferrer" className="truncate text-xs font-semibold text-primary underline">
                {storeUrl}
              </a>
              <button
                type="button"
                className="flex items-center gap-1 text-xs text-text-secondary"
                onClick={() => {
                  void navigator.clipboard.writeText(storeUrl);
                  setCopied(true);
                  setTimeout(() => setCopied(false), 1500);
                }}
              >
                {copied ? <CheckCircle2 className="h-3.5 w-3.5 text-[#15803d]" /> : <Copy className="h-3.5 w-3.5" />}
                {copied ? "Copied" : "Copy"}
              </button>
            </div>
          ) : null}
        </section>

        {/* Catalog settings */}
        <section className="card p-5">
          <h2 className="mb-3 text-base font-bold">Customer catalog</h2>
          <div className="space-y-3">
            <Toggle
              label="Publish store for online orders"
              hint="Customers can browse and order at your store link."
              checked={!!settings?.is_published}
              onChange={(v) => void patchSettings({ is_published: v })}
              disabled={saving}
            />
            <Toggle
              label="Show stock availability"
              hint="Customers see 'in stock / out of stock' instead of exact counts."
              checked={!!settings?.show_stock}
              onChange={(v) => void patchSettings({ show_stock: v })}
              disabled={saving}
            />
            <Toggle
              label="Allow guest checkout"
              hint="Order with just name and phone — no account needed."
              checked={!!settings?.allow_guest_checkout}
              onChange={(v) => void patchSettings({ allow_guest_checkout: v })}
              disabled={saving}
            />
            <div className="grid grid-cols-2 gap-3 pt-1">
              <label className="block text-xs">
                <span className="mb-1 block font-medium">Delivery fee (₹)</span>
                <input
                  className="input"
                  type="number"
                  min="0"
                  defaultValue={settings?.delivery_fee ?? 0}
                  onBlur={(e) => void patchSettings({ delivery_fee: Number(e.target.value) })}
                />
              </label>
              <label className="block text-xs">
                <span className="mb-1 block font-medium">Minimum order (₹)</span>
                <input
                  className="input"
                  type="number"
                  min="0"
                  defaultValue={settings?.min_order_amount ?? 0}
                  onBlur={(e) => void patchSettings({ min_order_amount: Number(e.target.value) })}
                />
              </label>
            </div>
          </div>
        </section>

        {/* Integrations health center */}
        <section className="card p-5">
          <h2 className="mb-1 flex items-center gap-2 text-base font-bold">
            <Plug className="h-4 w-4 text-primary" /> Integrations
          </h2>
          <p className="mb-3 text-xs text-text-muted">
            Statuses are always truthful. Nothing is shown as connected or live unless it really is.
          </p>
          <div className="space-y-2">
            {providers.map((p) => (
              <div key={p.provider} className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-border px-3 py-2.5">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <p className="text-sm font-bold">{p.provider}</p>
                    <StatusBadge
                      label={p.status === "DEMO" ? "demo / simulated" : p.status.replace(/_/g, " ").toLowerCase()}
                      tone={(STATUS_TONE[p.status] ?? "muted") as "ok" | "info" | "muted" | "warn" | "danger"}
                    />
                  </div>
                  <p className="mt-0.5 text-[11px] text-text-muted">
                    {p.status === "DEMO"
                      ? p.note || "Simulated integration — exercises your real internal order pipeline."
                      : p.status === "NOT_CONFIGURED"
                        ? "No credentials configured yet."
                        : p.last_error || `Last sync: ${p.last_sync_at ? new Date(p.last_sync_at).toLocaleString("en-IN") : "never"}`}
                  </p>
                </div>
                <div className="flex gap-1.5">
                  {p.mode === "demo" ? (
                    <>
                      <button type="button" className="btn btn-secondary px-2.5 py-1 text-xs" disabled={busyProvider === p.provider} onClick={() => void sync(p.provider)}>
                        <RefreshCw className={cn("mr-1 h-3 w-3", busyProvider === p.provider && "animate-spin")} />
                        Sync catalog
                      </button>
                      <button type="button" className="btn btn-secondary px-2.5 py-1 text-xs" disabled={busyProvider === p.provider} onClick={() => void configure(p.provider, "unconfigured")}>
                        Disable
                      </button>
                    </>
                  ) : (
                    <button type="button" className="btn btn-secondary px-2.5 py-1 text-xs" disabled={busyProvider === p.provider} onClick={() => void configure(p.provider, "demo")}>
                      Enable demo mode
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
          <div className="mt-3 rounded-xl bg-[#f8fafc] px-3 py-2.5 text-[11px] leading-4 text-text-muted">
            <p className="font-semibold text-text-secondary">Other integrations</p>
            <p>Payments: manual (in-person) — Razorpay/Paytm activate only when real credentials are configured.</p>
            <p>WhatsApp: official sending not configured — campaigns stay in prepared state, nothing is sent.</p>
          </div>
        </section>
      </div>
    </AppShell>
  );
}

function Toggle({ label, hint, checked, onChange, disabled }: {
  label: string;
  hint: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <label className="flex cursor-pointer items-start justify-between gap-3">
      <span>
        <span className="block text-sm font-semibold">{label}</span>
        <span className="block text-xs text-text-muted">{hint}</span>
      </span>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        className={cn(
          "relative mt-0.5 h-5 w-9 shrink-0 rounded-full transition-colors",
          checked ? "bg-primary" : "bg-[#cbd5e1]"
        )}
        onClick={() => onChange(!checked)}
      >
        <span
          className={cn(
            "absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all",
            checked ? "left-[18px]" : "left-0.5"
          )}
        />
      </button>
    </label>
  );
}
