"use client";

import { useEffect, useState } from "react";
import {
  Barcode,
  CalendarClock,
  Hash,
  Layers,
  Loader2,
  Scale,
  Tag,
  TrendingUp,
  X,
} from "lucide-react";
import { api } from "@/lib/api";
import { formatINR, type Product } from "@/lib/types";
import { ProductImage } from "@/components/product-image";

/** Upload/replace the product photo; reloads on success. */
function ImageUploadRow({ productId, currentUrl }: { productId: string; currentUrl: string | null }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  async function onPick(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setBusy(true);
    setError(null);
    setDone(null);
    try {
      const fd = new FormData();
      fd.append("image", file);
      const res = await fetch(`/api/products/${productId}/image`, {
        method: "POST",
        headers: { Authorization: `Bearer ${localStorage.getItem("ks_token") ?? ""}` },
        body: fd,
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body?.message ?? "Upload failed");
      setDone(body.enrolled ? "Saved — recognition enrolled." : "Saved (visual enrollment pending vision service).");
      setTimeout(() => window.location.reload(), 900);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-3 flex items-center justify-between gap-2 rounded-xl border border-dashed border-border px-3 py-2">
      <div className="text-[11px] text-text-secondary">
        {currentUrl ? "Replace product photo" : "Add a product photo"}
        <span className="block text-[10px] text-text-muted">
          {done ?? "Photo also enrolls this product for camera recognition."}
        </span>
      </div>
      <label className="btn btn-secondary cursor-pointer px-3 py-1.5 text-xs">
        {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
        {busy ? "Uploading…" : "Choose image"}
        <input type="file" accept="image/jpeg,image/png,image/webp" className="hidden" onChange={onPick} disabled={busy} />
      </label>
      {error ? <span className="text-[10px] text-red-600">{error}</span> : null}
    </div>
  );
}

type PricingInfo = {
  purchase_cost: number;
  minimum_acceptable_price: number | null;
  margin_amount?: number;
  margin_pct?: number;
};

type VelocityInfo = {
  status?: string;
  avg_daily_units?: number | null;
  reason?: string;
};

type Props = {
  product: Product;
  onAddToCart: (product: Product, quantity: number) => void;
  onBargain: (product: Product) => void;
  onClose: () => void;
};

/** Product details per reference image: identity, price, inventory, sales, actions. */
export function ProductDetailsModal({ product, onAddToCart, onBargain, onClose }: Props) {
  const [pricing, setPricing] = useState<PricingInfo | null>(null);
  const [velocity, setVelocity] = useState<VelocityInfo | null>(null);
  const [qty, setQty] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    Promise.all([
      api<PricingInfo>(`/api/inventory/products/${product.id}/pricing`),
      api<VelocityInfo>(`/api/inventory/products/${product.id}/reorder`).catch(() => null),
    ])
      .then(([p, r]) => {
        if (!alive) return;
        setPricing(p);
        const v = (r as { velocity?: VelocityInfo } | null)?.velocity;
        setVelocity(v ?? null);
      })
      .catch((e) => {
        if (alive) setError(e instanceof Error ? e.message : "Failed to load product details");
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [product.id]);

  const marginPct = pricing?.margin_pct ?? product.margin_pct;
  const outOfStock = product.quantity <= 0;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4"
      role="dialog"
      aria-modal="true"
      aria-label={`Product details: ${product.name}`}
    >
      <div className="card max-h-[90vh] w-full max-w-lg overflow-y-auto p-5">
        <div className="flex items-start gap-3">
          <ProductImage
            src={product.image_url}
            alt={product.name}
            className="h-16 w-16 shrink-0 rounded-xl border border-border"
          />
          <div className="min-w-0 flex-1">
            <h2 className="text-base font-bold">{product.name}</h2>
            <p className="mt-0.5 text-xs text-text-muted">
              {product.brand ? `${product.brand} · ` : ""}{product.category}
            </p>
            {product.source === "openfoodfacts" ? (
              <p className="mt-0.5 text-[10px] text-text-muted">
                Data: Open Food Facts contributors (ODbL)
              </p>
            ) : null}
          </div>
          <button type="button" className="btn btn-ghost h-8 w-8 shrink-0 p-0" onClick={onClose} aria-label="Close">
            <X className="h-4 w-4" />
          </button>
        </div>

        {product.description ? (
          <p className="mt-3 rounded-xl border border-border bg-[#f8fafc] px-3 py-2 text-[11px] leading-relaxed text-text-secondary">
            {product.description}
          </p>
        ) : null}

        <ImageUploadRow productId={product.id} currentUrl={product.image_url ?? null} />

        {loading ? (
          <div className="mt-4 flex items-center gap-2 text-sm text-text-muted">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading details…
          </div>
        ) : error && !pricing ? (
          <p className="mt-4 rounded-xl border border-[#fecaca] bg-[#fef2f2] px-3 py-2 text-sm text-[#b91c1c]">
            {error}
          </p>
        ) : (
          <>
            {/* Identity */}
            <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-2 rounded-xl border border-border bg-[#f8fafc] p-3 text-xs">
              <div className="flex items-center gap-1.5">
                <Hash className="h-3.5 w-3.5 text-text-muted" />
                <dt className="text-text-muted">SKU:</dt>
                <dd className="font-semibold">{product.sku || "—"}</dd>
              </div>
              <div className="flex items-center gap-1.5">
                <Barcode className="h-3.5 w-3.5 text-text-muted" />
                <dt className="text-text-muted">Barcode:</dt>
                <dd className="font-semibold">{product.barcode || "—"}</dd>
              </div>
              <div className="flex items-center gap-1.5">
                <Layers className="h-3.5 w-3.5 text-text-muted" />
                <dt className="text-text-muted">Unit:</dt>
                <dd className="font-semibold">{product.unit}</dd>
              </div>
              <div className="flex items-center gap-1.5">
                <Tag className="h-3.5 w-3.5 text-text-muted" />
                <dt className="text-text-muted">MRP:</dt>
                <dd className="font-semibold">{formatINR(product.mrp)}</dd>
              </div>
            </dl>

            {/* Price */}
            <h3 className="mt-4 text-xs font-bold uppercase tracking-wide text-text-muted">Price</h3>
            <div className="mt-1.5 grid grid-cols-3 gap-2">
              <div className="rounded-xl border border-border p-2.5 text-center">
                <p className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">Selling</p>
                <p className="text-sm font-bold">{formatINR(product.selling_price)}</p>
              </div>
              <div className="rounded-xl border border-border p-2.5 text-center">
                <p className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">Cost</p>
                <p className="text-sm font-bold">{formatINR(pricing?.purchase_cost ?? product.purchase_price)}</p>
              </div>
              <div className="rounded-xl border border-border p-2.5 text-center">
                <p className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">Min price</p>
                <p className="text-sm font-bold">
                  {pricing?.minimum_acceptable_price != null
                    ? formatINR(pricing.minimum_acceptable_price)
                    : "—"}
                </p>
              </div>
            </div>
            <p className="mt-1.5 text-xs text-text-secondary">
              Gross margin: <span className="font-semibold">{marginPct ?? "—"}%</span>
            </p>

            {/* Inventory */}
            <h3 className="mt-4 text-xs font-bold uppercase tracking-wide text-text-muted">Inventory</h3>
            <div className="mt-1.5 flex items-center gap-3 rounded-xl border border-border p-3 text-sm">
              <span className="font-bold">{product.quantity} {product.unit}</span>
              <span className={outOfStock ? "text-[#b91c1c]" : "text-text-secondary"}>
                {outOfStock ? "Out of stock" : "Available"}
              </span>
            </div>

            {/* Sales */}
            <h3 className="mt-4 text-xs font-bold uppercase tracking-wide text-text-muted">Sales</h3>
            <div className="mt-1.5 flex items-center gap-2 rounded-xl border border-border p-3 text-sm">
              <TrendingUp className="h-4 w-4 text-primary" />
              {velocity?.avg_daily_units != null ? (
                <span>
                  ~{velocity.avg_daily_units} {product.unit}/day recent velocity
                  {velocity.status ? ` (${velocity.status.replace("_", " ").toLowerCase()})` : ""}
                </span>
              ) : (
                <span className="text-text-muted">Not enough sales history for a velocity estimate.</span>
              )}
            </div>

            {/* Quantity + actions */}
            <div className="mt-4 flex items-end gap-2">
              <label className="block text-sm">
                <span className="mb-1 block font-medium">Quantity</span>
                <input
                  className="input w-24"
                  type="number"
                  min="1"
                  max={product.quantity}
                  value={qty}
                  onChange={(e) => setQty(Math.max(1, Math.min(product.quantity, Number(e.target.value) || 1)))}
                  disabled={outOfStock}
                />
              </label>
              <button
                type="button"
                className="btn btn-primary flex-1 justify-center"
                disabled={outOfStock}
                onClick={() => {
                  onAddToCart(product, qty);
                  onClose();
                }}
              >
                {outOfStock ? "Out of stock" : `Add to cart — ${formatINR(product.selling_price * qty)}`}
              </button>
            </div>
            <button
              type="button"
              className="btn btn-secondary mt-2 w-full justify-center"
              disabled={outOfStock}
              onClick={() => {
                onBargain(product);
                onClose();
              }}
            >
              <Scale className="h-4 w-4" />
              Make an offer
            </button>
            <p className="mt-3 flex items-center gap-1.5 text-[11px] text-text-muted">
              <CalendarClock className="h-3.5 w-3.5" />
              Batch/expiry details live in Inventory. Negotiated prices follow your pricing policy.
            </p>
          </>
        )}
      </div>
    </div>
  );
}
