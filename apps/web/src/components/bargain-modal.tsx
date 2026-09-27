"use client";

import { useEffect, useState } from "react";
import { ArrowUpRight, Ban, Check, Loader2, Scale, X } from "lucide-react";
import { api } from "@/lib/api";
import { formatINR, type BargainResult, type PricingInfo } from "@/lib/types";
import { cn } from "@/lib/cn";

type Props = {
  product: { id: string; name: string; selling_price: number; quantity: number };
  onAccept: (unitPrice: number, quantity: number) => void;
  onClose: () => void;
};

/** Negotiation assistant — the pricing engine (not the UI) decides ACCEPT/COUNTER/REJECT. */
export function BargainModal({ product, onAccept, onClose }: Props) {
  const [offer, setOffer] = useState<string>("");
  const [qty, setQty] = useState("1");
  const [pricing, setPricing] = useState<PricingInfo | null>(null);
  const [result, setResult] = useState<BargainResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api<PricingInfo>(`/api/inventory/products/${product.id}/pricing`)
      .then((p) => {
        if (alive) setPricing(p);
      })
      .catch((e) => {
        if (alive) setError(e instanceof Error ? e.message : "Failed to load pricing");
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [product.id]);

  async function check() {
    const offerNum = Number(offer);
    if (!offerNum || offerNum <= 0) {
      setError("Enter the customer's offer.");
      return;
    }
    setError(null);
    setChecking(true);
    setResult(null);
    try {
      const r = await api<BargainResult>(`/api/inventory/products/${product.id}/bargain`, {
        method: "POST",
        json: { offer: offerNum, quantity: Math.max(1, Number(qty) || 1) },
      });
      setResult(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Bargain check failed");
    } finally {
      setChecking(false);
    }
  }

  const decisionTone =
    result?.decision === "ACCEPT"
      ? "border-[#bbf7d0] bg-[#f0fdf4] text-[#15803d]"
      : result?.decision === "COUNTER"
        ? "border-[#fde68a] bg-[#fffbeb] text-[#b45309]"
        : "border-[#fecaca] bg-[#fef2f2] text-[#b91c1c]";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4"
      role="dialog"
      aria-modal="true"
      aria-label={`Negotiate price for ${product.name}`}
    >
      <div className="card w-full max-w-md p-5">
        <div className="flex items-start justify-between gap-2">
          <div>
            <h2 className="flex items-center gap-2 text-base font-bold">
              <Scale className="h-4 w-4 text-primary" />
              Negotiate: {product.name}
            </h2>
            <p className="mt-0.5 text-xs text-text-muted">
              Customer offers a price — the pricing engine decides.
            </p>
          </div>
          <button type="button" className="btn btn-ghost h-8 w-8 p-0" onClick={onClose} aria-label="Close">
            <X className="h-4 w-4" />
          </button>
        </div>

        {loading ? (
          <div className="mt-4 flex items-center gap-2 text-sm text-text-muted">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading pricing policy…
          </div>
        ) : error && !pricing ? (
          <p className="mt-4 rounded-xl border border-[#fecaca] bg-[#fef2f2] px-3 py-2 text-sm text-[#b91c1c]">
            {error}
          </p>
        ) : (
          <>
            <div className="mt-4 grid grid-cols-3 gap-2 rounded-xl border border-border bg-[#f8fafc] p-3 text-center">
              <div>
                <p className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">Your price</p>
                <p className="text-sm font-bold">{formatINR(product.selling_price)}</p>
              </div>
              <div>
                <p className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">Cost</p>
                <p className="text-sm font-bold">{formatINR(pricing?.purchase_cost ?? 0)}</p>
              </div>
              <div>
                <p className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">Min price</p>
                <p className="text-sm font-bold">
                  {pricing?.minimum_acceptable_price != null
                    ? formatINR(pricing.minimum_acceptable_price)
                    : "—"}
                </p>
              </div>
            </div>

            <div className="mt-3 grid grid-cols-2 gap-2">
              <label className="block text-sm">
                <span className="mb-1 block font-medium">Customer offer (₹) *</span>
                <input
                  className="input"
                  type="number"
                  min="0"
                  step="0.5"
                  value={offer}
                  onChange={(e) => setOffer(e.target.value)}
                  placeholder="e.g. 85"
                />
              </label>
              <label className="block text-sm">
                <span className="mb-1 block font-medium">Quantity</span>
                <input
                  className="input"
                  type="number"
                  min="1"
                  max={product.quantity}
                  value={qty}
                  onChange={(e) => setQty(e.target.value)}
                />
              </label>
            </div>

            <button type="button" className="btn btn-secondary mt-3 w-full justify-center" onClick={() => void check()} disabled={checking}>
              {checking ? <Loader2 className="h-4 w-4 animate-spin" /> : <Scale className="h-4 w-4" />}
              Check offer
            </button>

            {result ? (
              <div className={cn("mt-3 rounded-xl border p-3", decisionTone)}>
                <p className="flex items-center gap-1.5 text-sm font-bold">
                  {result.decision === "ACCEPT" ? (
                    <Check className="h-4 w-4" />
                  ) : result.decision === "COUNTER" ? (
                    <ArrowUpRight className="h-4 w-4" />
                  ) : (
                    <Ban className="h-4 w-4" />
                  )}
                  {result.decision}
                  {result.counteroffer ? ` — counter at ${formatINR(result.counteroffer)}` : ""}
                </p>
                <p className="mt-1 text-xs leading-relaxed">{result.reason}</p>
                {result.margin_at_offer_pct != null ? (
                  <p className="mt-1 text-[11px] font-semibold">
                    Margin at offer: {result.margin_at_offer_pct}%
                  </p>
                ) : null}
                {result.decision === "ACCEPT" ? (
                  <button
                    type="button"
                    className="btn btn-primary mt-2 w-full justify-center"
                    onClick={() =>
                      onAccept(result.offer, Math.max(1, Number(qty) || 1))
                    }
                  >
                    Add to bill at {formatINR(result.offer)} × {Math.max(1, Number(qty) || 1)}
                  </button>
                ) : null}
              </div>
            ) : null}

            {error && pricing ? (
              <p className="mt-2 text-xs text-[#b91c1c]">{error}</p>
            ) : null}

            <p className="mt-3 text-[11px] text-text-muted">
              Minimum price follows your pricing policy. Near-expiry stock may allow breakeven clearance.
            </p>
          </>
        )}
      </div>
    </div>
  );
}
