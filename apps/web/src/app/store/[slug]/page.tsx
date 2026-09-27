"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import { CheckCircle2, Minus, Plus, Search, ShoppingBag, ShoppingCart, Store, TriangleAlert, X } from "lucide-react";
import { formatINR } from "@/lib/types";
import { cn } from "@/lib/cn";

type CatalogItem = {
  id: string;
  name: string;
  category: string;
  unit: string;
  mrp: string | number;
  price: string | number;
  image_url: string | null;
  in_stock: boolean;
  available?: number;
};

type Catalog = {
  store: { slug: string; name: string; location: string | null; currency: string };
  items: CatalogItem[];
  categories: string[];
  total: number;
  delivery_fee: number;
  min_order_amount: number;
  allow_guest_checkout: boolean;
};

type Placed = {
  order_id: string;
  public_token: string;
  state: string;
  total: number;
  duplicate: boolean;
};

const num = (v: string | number | undefined | null) => Number(v ?? 0);

export default function StorefrontPage({ params }: { params: Promise<{ slug: string }> }) {
  const [slug, setSlug] = useState<string | null>(null);
  useEffect(() => {
    params.then((p) => setSlug(p.slug));
  }, [params]);

  if (!slug) return <div className="min-h-screen bg-background" />;
  return <Storefront slug={slug} />;
}

function Storefront({ slug }: { slug: string }) {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState("");
  const [category, setCategory] = useState<string | null>(null);
  const [cart, setCart] = useState<Record<string, number>>({});
  const [cartOpen, setCartOpen] = useState(false);
  const [placed, setPlaced] = useState<Placed | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const qs = new URLSearchParams({ limit: "100" });
      if (category) qs.set("category", category);
      if (q.trim()) qs.set("q", q.trim());
      const res = await fetch(`/storefront/api/${slug}?${qs.toString()}`);
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.detail?.message || body?.detail || "Store unavailable");
      }
      setCatalog(await res.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Store unavailable");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slug, category]);

  // debounced search
  useEffect(() => {
    const t = setTimeout(() => void load(), 300);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);

  const cartLines = useMemo(() => {
    if (!catalog) return [];
    return Object.entries(cart)
      .map(([pid, qty]) => {
        const item = catalog.items.find((i) => i.id === pid);
        return item ? { item, qty } : null;
      })
      .filter(Boolean) as { item: CatalogItem; qty: number }[];
  }, [cart, catalog]);

  const cartTotal = cartLines.reduce((sum, l) => sum + num(l.item.price) * l.qty, 0);
  const cartCount = cartLines.reduce((sum, l) => sum + l.qty, 0);

  if (placed) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background p-6">
        <div className="card w-full max-w-md p-8 text-center">
          <CheckCircle2 className="mx-auto h-14 w-14 text-[#15803d]" />
          <h1 className="mt-4 text-xl font-bold">Order placed</h1>
          <p className="mt-2 text-sm text-text-secondary">
            {placed.duplicate
              ? "This order was already placed — you're all set."
              : `Total ${formatINR(placed.total)}. The store will confirm your order shortly.`}
          </p>
          <div className="mt-5 rounded-xl bg-[#f8fafc] p-4 text-left text-xs text-text-secondary">
            <p className="font-semibold text-text-primary">What happens next?</p>
            <ol className="mt-1 list-decimal space-y-0.5 pl-4">
              <li>The merchant confirms your order.</li>
              <li>Payment is verified {placed.total > 0 ? "" : "(UPI / cash on pickup)"}.</li>
              <li>You get your order — pickup or delivery.</li>
            </ol>
          </div>
          <a href={`/store/${slug}`} className="btn btn-primary mt-5 w-full justify-center">
            Continue shopping
          </a>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-background">
      {/* Store header */}
      <header className="sticky top-0 z-20 border-b border-border bg-white">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-4 py-3">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary/10">
              <Store className="h-5 w-5 text-primary" />
            </div>
            <div>
              <p className="text-sm font-bold leading-tight">{catalog?.store.name ?? "Store"}</p>
              <p className="text-xs text-text-muted">{catalog?.store.location ?? ""}</p>
            </div>
          </div>
          <button
            type="button"
            className="relative flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-semibold text-white"
            onClick={() => setCartOpen(true)}
          >
            <ShoppingCart className="h-4 w-4" />
            Cart
            {cartCount > 0 ? (
              <span className="absolute -right-1.5 -top-1.5 flex h-5 w-5 items-center justify-center rounded-full bg-[#00baf2] text-[10px] font-bold text-[#0b1f3a]">
                {cartCount}
              </span>
            ) : null}
          </button>
        </div>
      </header>

      <main className="mx-auto max-w-5xl space-y-4 px-4 py-5">
        {/* Search */}
        <div className="relative">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" />
          <input
            className="input pl-9"
            placeholder="Search products…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>

        {/* Categories */}
        {catalog && catalog.categories.length > 0 ? (
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              className={cn(
                "rounded-full border px-3 py-1.5 text-xs font-semibold",
                !category ? "border-primary bg-[#eff6ff] text-primary" : "border-border bg-white text-text-secondary"
              )}
              onClick={() => setCategory(null)}
            >
              All
            </button>
            {catalog.categories.map((c) => (
              <button
                key={c}
                type="button"
                className={cn(
                  "rounded-full border px-3 py-1.5 text-xs font-semibold",
                  category === c ? "border-primary bg-[#eff6ff] text-primary" : "border-border bg-white text-text-secondary"
                )}
                onClick={() => setCategory(c)}
              >
                {c}
              </button>
            ))}
          </div>
        ) : null}

        {loading && !catalog ? (
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            {Array.from({ length: 8 }).map((_, i) => (
              <div key={i} className="card p-3">
                <div className="skeleton mb-2 h-24 w-full" />
                <div className="skeleton mb-1 h-3 w-3/4" />
                <div className="skeleton h-3 w-1/2" />
              </div>
            ))}
          </div>
        ) : null}

        {error ? (
          <div className="card flex items-center gap-2 p-5 text-sm text-[#b91c1c]">
            <TriangleAlert className="h-4 w-4" />
            {error}
          </div>
        ) : null}

        {/* Product grid */}
        {catalog ? (
          catalog.items.length === 0 ? (
            <div className="card p-10 text-center text-sm text-text-muted">
              <ShoppingBag className="mx-auto mb-2 h-8 w-8" />
              No products found{q ? ` for "${q}"` : ""}.
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              {catalog.items.map((item) => {
                const qty = cart[item.id] || 0;
                const mrp = num(item.mrp);
                const price = num(item.price);
                const off = mrp > price ? Math.round(((mrp - price) / mrp) * 100) : 0;
                return (
                  <div key={item.id} className="card overflow-hidden p-0">
                    <div className="flex h-24 items-center justify-center bg-[#f1f5f9]">
                      {item.image_url ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img src={item.image_url} alt={item.name} className="h-full w-full object-cover" />
                      ) : (
                        <ShoppingBag className="h-8 w-8 text-[#cbd5e1]" />
                      )}
                    </div>
                    <div className="space-y-1.5 p-3">
                      <p className="line-clamp-2 min-h-8 text-xs font-semibold leading-4">{item.name}</p>
                      <div className="flex items-baseline gap-1.5">
                        <span className="text-sm font-bold">{formatINR(price)}</span>
                        {off > 0 ? (
                          <>
                            <span className="text-[10px] text-text-muted line-through">{formatINR(mrp)}</span>
                            <span className="text-[10px] font-bold text-[#15803d]">{off}% off</span>
                          </>
                        ) : null}
                      </div>
                      {!item.in_stock ? (
                        <p className="text-[10px] font-semibold text-[#b91c1c]">Out of stock</p>
                      ) : qty === 0 ? (
                        <button
                          type="button"
                          className="btn btn-secondary h-7 w-full justify-center py-0 text-xs"
                          onClick={() => setCart((c) => ({ ...c, [item.id]: 1 }))}
                        >
                          <Plus className="h-3.5 w-3.5" />
                          Add
                        </button>
                      ) : (
                        <div className="flex h-7 items-center justify-between rounded-lg border border-primary px-1">
                          <button type="button" aria-label="Decrease" className="px-1.5 text-primary"
                            onClick={() =>
                              setCart((c) => {
                                const next = { ...c, [item.id]: qty - 1 };
                                if (next[item.id] <= 0) delete next[item.id];
                                return next;
                              })
                            }>
                            <Minus className="h-3.5 w-3.5" />
                          </button>
                          <span className="text-xs font-bold text-primary">{qty}</span>
                          <button type="button" aria-label="Increase" className="px-1.5 text-primary"
                            onClick={() => setCart((c) => ({ ...c, [item.id]: qty + 1 }))}>
                            <Plus className="h-3.5 w-3.5" />
                          </button>
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )
        ) : null}
      </main>

      {/* Cart drawer */}
      {cartOpen ? (
        <div className="fixed inset-0 z-30 flex justify-end bg-black/40" onClick={() => setCartOpen(false)}>
          <div className="flex h-full w-full max-w-sm flex-col bg-white shadow-xl" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between border-b border-border px-4 py-3">
              <p className="text-sm font-bold">Your cart ({cartCount})</p>
              <button type="button" onClick={() => setCartOpen(false)} aria-label="Close cart">
                <X className="h-5 w-5" />
              </button>
            </div>
            <div className="flex-1 space-y-3 overflow-y-auto p-4">
              {cartLines.length === 0 ? (
                <p className="pt-10 text-center text-sm text-text-muted">Your cart is empty.</p>
              ) : (
                cartLines.map(({ item, qty }) => (
                  <div key={item.id} className="flex items-center justify-between rounded-xl border border-border p-3">
                    <div className="min-w-0">
                      <p className="truncate text-xs font-semibold">{item.name}</p>
                      <p className="text-xs text-text-muted">{formatINR(num(item.price))} × {qty}</p>
                    </div>
                    <div className="flex items-center gap-2">
                      <div className="flex items-center rounded-lg border border-border">
                        <button type="button" className="px-2 py-1 text-primary" aria-label="Decrease"
                          onClick={() =>
                            setCart((c) => {
                              const next = { ...c, [item.id]: qty - 1 };
                              if (next[item.id] <= 0) delete next[item.id];
                              return next;
                            })
                          }>
                          <Minus className="h-3.5 w-3.5" />
                        </button>
                        <span className="text-xs font-bold">{qty}</span>
                        <button type="button" className="px-2 py-1 text-primary" aria-label="Increase"
                          onClick={() => setCart((c) => ({ ...c, [item.id]: qty + 1 }))}>
                          <Plus className="h-3.5 w-3.5" />
                        </button>
                      </div>
                      <span className="w-14 text-right text-xs font-bold">{formatINR(num(item.price) * qty)}</span>
                    </div>
                  </div>
                ))
              )}
            </div>
            {cartLines.length > 0 ? (
              <div className="space-y-2 border-t border-border p-4">
                <div className="flex justify-between text-xs text-text-secondary">
                  <span>Subtotal</span>
                  <span>{formatINR(cartTotal)}</span>
                </div>
                <div className="flex justify-between text-xs text-text-secondary">
                  <span>Delivery</span>
                  <span>{num(catalog?.delivery_fee) > 0 ? formatINR(num(catalog?.delivery_fee)) : "Free"}</span>
                </div>
                <div className="flex justify-between text-sm font-bold">
                  <span>Total</span>
                  <span>{formatINR(cartTotal + num(catalog?.delivery_fee))}</span>
                </div>
                {catalog && cartTotal < num(catalog.min_order_amount) ? (
                  <p className="rounded-xl bg-[#fef2f2] px-3 py-2 text-xs text-[#b91c1c]">
                    Minimum order is {formatINR(num(catalog.min_order_amount))} — add {formatINR(num(catalog.min_order_amount) - cartTotal)} more.
                  </p>
                ) : (
                  <a
                    href={`/store/${slug}/checkout`}
                    className="btn btn-primary w-full justify-center"
                    onClick={() => {
                      try {
                        sessionStorage.setItem(`ks-cart-${slug}`, JSON.stringify(cart));
                      } catch {
                        /* ignore */
                      }
                    }}
                  >
                    Checkout
                  </a>
                )}
              </div>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}
