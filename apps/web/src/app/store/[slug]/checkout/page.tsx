"use client";

import { useEffect, useMemo, useState } from "react";
import { CheckCircle2, MapPin, ShoppingBag, TriangleAlert, User } from "lucide-react";
import { formatINR } from "@/lib/types";
import { cn } from "@/lib/cn";

type CatalogItem = {
  id: string;
  name: string;
  price: string | number;
  in_stock: boolean;
};

type Placed = {
  order_id: string;
  public_token: string;
  state: string;
  total: number;
  duplicate: boolean;
};

const num = (v: string | number | undefined | null) => Number(v ?? 0);

export default function StoreCheckoutPage({ params }: { params: Promise<{ slug: string }> }) {
  const [slug, setSlug] = useState<string | null>(null);
  useEffect(() => {
    params.then((p) => setSlug(p.slug));
  }, [params]);
  if (!slug) return <div className="min-h-screen bg-background" />;
  return <Checkout slug={slug} />;
}

function Checkout({ slug }: { slug: string }) {
  const [cart, setCart] = useState<Record<string, number>>({});
  const [items, setItems] = useState<CatalogItem[]>([]);
  const [deliveryFee, setDeliveryFee] = useState(0);
  const [minOrder, setMinOrder] = useState(0);
  const [allowGuest, setAllowGuest] = useState(true);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [placed, setPlaced] = useState<Placed | null>(null);
  const [paid, setPaid] = useState(false);

  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [deliveryMethod, setDeliveryMethod] = useState("PICKUP");
  const [address, setAddress] = useState("");
  const [paymentMethod, setPaymentMethod] = useState("upi");

  // restore cart from sessionStorage (set by the storefront page)
  useEffect(() => {
    try {
      const saved = sessionStorage.getItem(`ks-cart-${slug}`);
      if (saved) setCart(JSON.parse(saved));
    } catch {
      /* ignore */
    }
    fetch(`/storefront/api/${slug}`)
      .then(async (res) => {
        if (!res.ok) throw new Error((await res.json().catch(() => ({})))?.detail?.message || "Store unavailable");
        return res.json();
      })
      .then((cat) => {
        setItems(cat.items || []);
        setDeliveryFee(num(cat.delivery_fee));
        setMinOrder(num(cat.min_order_amount));
        setAllowGuest(cat.allow_guest_checkout !== false);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Store unavailable"))
      .finally(() => setLoading(false));
  }, [slug]);

  const lines = useMemo(
    () =>
      Object.entries(cart)
        .map(([pid, qty]) => {
          const item = items.find((i) => i.id === pid);
          return item ? { item, qty } : null;
        })
        .filter(Boolean) as { item: CatalogItem; qty: number }[],
    [cart, items]
  );
  const subtotal = lines.reduce((s, l) => s + num(l.item.price) * l.qty, 0);
  const total = subtotal + (deliveryMethod === "PICKUP" ? 0 : deliveryFee);

  async function placeOrder() {
    setBusy(true);
    setError(null);
    try {
      const idemKey = `web-${crypto.randomUUID()}`;
      const res = await fetch(`/storefront/api/${slug}/checkout`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          items: lines.map((l) => ({ product_id: l.item.id, quantity: l.qty })),
          customer_name: name,
          customer_phone: phone,
          delivery_method: deliveryMethod,
          delivery_address: deliveryMethod === "PICKUP" ? null : address,
          payment_method: paymentMethod,
          idempotency_key: idemKey,
        }),
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body?.detail?.message || "Checkout failed");
      setPlaced(body);
      sessionStorage.removeItem(`ks-cart-${slug}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Checkout failed");
    } finally {
      setBusy(false);
    }
  }

  async function payNow() {
    if (!placed) return;
    setBusy(true);
    try {
      const res = await fetch(`/storefront/api/${slug}/orders/${placed.public_token}/pay`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ method: paymentMethod, idempotency_key: `pay-${crypto.randomUUID()}` }),
      });
      if (res.ok) setPaid(true);
    } finally {
      setBusy(false);
    }
  }

  if (placed) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background p-6">
        <div className="card w-full max-w-md p-8 text-center">
          <CheckCircle2 className="mx-auto h-14 w-14 text-[#15803d]" />
          <h1 className="mt-4 text-xl font-bold">Order placed</h1>
          <p className="mt-1 text-sm text-text-secondary">Order total {formatINR(placed.total)}</p>
          {!paid ? (
            <>
              <p className="mt-4 rounded-xl bg-[#eff6ff] px-4 py-3 text-xs text-[#1d4ed8]">
                Payment is verified by the store before your order is prepared. {paymentMethod === "cash" ? "Pay cash at pickup." : "The merchant confirms UPI receipt."}
              </p>
              <button type="button" className="btn btn-primary mt-3 w-full justify-center" disabled={busy} onClick={() => void payNow()}>
                {busy ? "Requesting…" : paymentMethod === "cash" ? "I'll pay at pickup" : "I've sent the payment"}
              </button>
            </>
          ) : (
            <p className="mt-4 rounded-xl bg-[#f0fdf4] px-4 py-3 text-xs text-[#15803d]">
              Payment requested — the store verifies receipt before preparing your order.
            </p>
          )}
          <a href={`/store/${slug}`} className="btn btn-secondary mt-3 w-full justify-center">
            Continue shopping
          </a>
        </div>
      </div>
    );
  }

  if (loading) return <div className="min-h-screen bg-background p-6"><div className="skeleton mx-auto h-64 max-w-md" /></div>;
  if (lines.length === 0) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background p-6">
        <div className="card max-w-sm p-8 text-center">
          <ShoppingBag className="mx-auto h-10 w-10 text-text-muted" />
          <p className="mt-3 text-sm text-text-secondary">Your cart is empty.</p>
          <a href={`/store/${slug}`} className="btn btn-primary mt-4 w-full justify-center">Browse products</a>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b border-border bg-white px-4 py-3">
        <p className="mx-auto max-w-lg text-sm font-bold">Checkout</p>
      </header>
      <main className="mx-auto max-w-lg space-y-4 p-4 pb-28">
        {error ? (
          <div className="flex items-center gap-2 rounded-xl border border-[#fecaca] bg-[#fef2f2] px-4 py-3 text-sm text-[#b91c1c]">
            <TriangleAlert className="h-4 w-4" />
            {error}
          </div>
        ) : null}

        <section className="card space-y-3 p-4">
          <h2 className="flex items-center gap-2 text-sm font-bold">
            <User className="h-4 w-4 text-primary" /> Your details
          </h2>
          <label className="block text-xs">
            <span className="mb-1 block font-medium">Name *</span>
            <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <label className="block text-xs">
            <span className="mb-1 block font-medium">Phone *</span>
            <input className="input" inputMode="numeric" maxLength={10} value={phone}
              onChange={(e) => setPhone(e.target.value.replace(/\D/g, ""))} />
          </label>
        </section>

        <section className="card space-y-3 p-4">
          <h2 className="flex items-center gap-2 text-sm font-bold">
            <MapPin className="h-4 w-4 text-primary" /> Delivery
          </h2>
          <div className="grid grid-cols-2 gap-2">
            {(["PICKUP", "MERCHANT_DELIVERY"] as const).map((m) => (
              <button key={m} type="button"
                className={cn("rounded-xl border px-3 py-2 text-xs font-semibold",
                  deliveryMethod === m ? "border-primary bg-[#eff6ff] text-primary" : "border-border text-text-secondary")}
                onClick={() => setDeliveryMethod(m)}>
                {m === "PICKUP" ? "Store pickup" : "Home delivery"}
              </button>
            ))}
          </div>
          {deliveryMethod !== "PICKUP" ? (
            <label className="block text-xs">
              <span className="mb-1 block font-medium">Delivery address *</span>
              <textarea className="input min-h-20" value={address} onChange={(e) => setAddress(e.target.value)} />
            </label>
          ) : null}
        </section>

        <section className="card space-y-2 p-4">
          <h2 className="text-sm font-bold">Order summary</h2>
          {lines.map(({ item, qty }) => (
            <div key={item.id} className="flex justify-between text-xs">
              <span className="text-text-secondary">{item.name} × {qty}</span>
              <span className="font-semibold">{formatINR(num(item.price) * qty)}</span>
            </div>
          ))}
          <div className="flex justify-between border-t border-border pt-2 text-xs text-text-secondary">
            <span>Subtotal</span><span>{formatINR(subtotal)}</span>
          </div>
          <div className="flex justify-between text-xs text-text-secondary">
            <span>Delivery</span>
            <span>{deliveryMethod === "PICKUP" || deliveryFee === 0 ? "Free" : formatINR(deliveryFee)}</span>
          </div>
          <div className="flex justify-between text-sm font-bold">
            <span>Total</span><span>{formatINR(total)}</span>
          </div>
        </section>

        <section className="card space-y-2 p-4">
          <h2 className="text-sm font-bold">Payment</h2>
          {(["upi", "cash"] as const).map((m) => (
            <label key={m} className={cn("flex cursor-pointer items-center gap-2 rounded-xl border px-3 py-2 text-xs",
              paymentMethod === m ? "border-primary bg-[#eff6ff]" : "border-border")}>
              <input type="radio" name="pay" checked={paymentMethod === m} onChange={() => setPaymentMethod(m)} />
              <span className="font-semibold uppercase">{m}</span>
              <span className="text-text-muted">{m === "upi" ? "— verified by the store" : "— pay at pickup"}</span>
            </label>
          ))}
          <p className="text-[10px] text-text-muted">
            Payments are verified by the merchant before preparation — your total is confirmed by the store, not calculated here.
          </p>
        </section>
      </main>

      <div className="fixed inset-x-0 bottom-0 border-t border-border bg-white p-4">
        <div className="mx-auto flex max-w-lg items-center justify-between gap-4">
          <div>
            <p className="text-xs text-text-muted">Total</p>
            <p className="text-base font-bold">{formatINR(total)}</p>
          </div>
          <button type="button" className="btn btn-primary flex-1 justify-center"
            disabled={busy || !name.trim() || phone.length < 10 || (deliveryMethod !== "PICKUP" && !address.trim())
              || (minOrder > 0 && subtotal < minOrder)}
            onClick={() => void placeOrder()}>
            {busy ? "Placing order…" : "Place order"}
          </button>
        </div>
      </div>
    </div>
  );
}
