"use client";

import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Camera,
  CheckCircle2,
  CreditCard,
  HandCoins,
  Info,
  Minus,
  Plus,
  ScanLine,
  Search,
  Trash2,
  UserPlus,
  X,
} from "lucide-react";
import { BargainModal } from "@/components/bargain-modal";
import { ProductDetailsModal } from "@/components/product-details-modal";
import { CameraWorkspace, type CameraOverlay } from "@/components/camera-workspace";
import { BarcodeScannerPanel } from "@/components/barcode-scanner-panel";
import { ProductImage } from "@/components/product-image";
import { AppShell, AuthGuard } from "@/components/shell";
import { PageHeader } from "@/components/page-header";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { StatusBadge, statusTone } from "@/components/status-badge";
import { api } from "@/lib/api";
import { useBarcodeScanner, type ScanHit } from "@/lib/use-barcode-scanner";
import { useCamera } from "@/lib/use-camera";
import { useVisionHealth } from "@/lib/use-vision-health";
import { useVisionRecognize, type VisionHit } from "@/lib/use-vision-recognize";
import { formatINR, type Customer, type Product } from "@/lib/types";
import { cn } from "@/lib/cn";

type CartLine = {
  product: Product;
  quantity: number;
  /** Negotiated per-unit price (bargaining). Undefined = list price. */
  unitPrice?: number;
};

type PaymentMethod = "cash" | "upi" | "card" | "split" | "credit";

type InputMode = "camera" | "barcode" | "search";

const CONFIDENCE_PCT = (n: number) => `${Math.round(n * 100)}%`;

/**
 * VisionCandidateCard — the merchant confirmation gate (spec §16, master-prompt
 * §20). A recognition hit is a CANDIDATE, never a bill line: the merchant
 * confirms, corrects, or rejects — and the add goes through the same
 * stock-guarded addToCart path as every other input. Every action is recorded
 * as recognition feedback (STRONG_POSITIVE / STRONG_NEGATIVE / UNCERTAIN);
 * checkout success alone is never treated as ground truth (§71).
 */
function VisionCandidateCard({
  hit,
  candidates,
  onConfirm,
  onCorrect,
  onReject,
  onDismiss,
}: {
  hit: VisionHit;
  candidates: Product[];
  onConfirm: (p: Product) => void;
  onCorrect: (hit: VisionHit, p: Product) => void;
  onReject: (hit: VisionHit) => void;
  onDismiss: () => void;
}) {
  const [mode, setMode] = useState<"view" | "correct">("view");
  const [corrQ, setCorrQ] = useState("");
  const tone =
    hit.status === "IDENTIFIED"
      ? "border-[#15803d] bg-[#f0fdf4] text-[#15803d]"
      : hit.status === "REVIEW_REQUIRED"
        ? "border-[#b45309] bg-[#fffbeb] text-[#b45309]"
        : "border-border bg-white text-text-secondary";
  const methodLabel =
    hit.match.method === "COMBINED"
      ? "Visual + OCR"
      : hit.match.method === "BARCODE"
        ? "Barcode"
        : "Visual match";
  const ev = hit.match.evidence;
  const evidenceBits = ev
    ? [
        ev.visual_similarity != null ? `visual ${Math.round(ev.visual_similarity * 100)}%` : null,
        ev.ocr_similarity ? `OCR ${Math.round(ev.ocr_similarity * 100)}%` : null,
        ev.brand_match ? "brand ✓" : null,
        ev.pack_size_match ? "pack ✓" : null,
        ev.barcode_match ? "barcode ✓" : null,
      ].filter(Boolean)
    : [];

  if (mode === "correct") {
    const pool = corrQ.trim()
      ? candidates.filter((p) => p.name.toLowerCase().includes(corrQ.trim().toLowerCase()))
      : candidates;
    return (
      <div className="card border border-[#b45309] bg-[#fffbeb] px-4 py-3" role="status">
        <div className="flex items-center justify-between gap-2">
          <p className="text-sm font-semibold text-[#b45309]">Wrong product — pick the actual one</p>
          <button type="button" className="btn btn-ghost px-2 py-1 text-xs" onClick={() => setMode("view")}>
            Back
          </button>
        </div>
        <input
          className="input mt-2"
          placeholder="Search your catalog…"
          value={corrQ}
          onChange={(e) => setCorrQ(e.target.value)}
          aria-label="Search catalog to correct recognition"
        />
        <div className="mt-2 max-h-44 space-y-1.5 overflow-y-auto">
          {pool.map((p) => (
            <button
              key={p.id}
              type="button"
              className="flex w-full items-center justify-between rounded-lg border border-border bg-white px-3 py-2 text-left text-[13px] hover:border-primary"
              onClick={() => {
                setMode("view");
                onCorrect(hit, p);
              }}
            >
              <span className="truncate font-medium">{p.name}</span>
              <span className="text-[11px] text-text-muted">{formatINR(p.selling_price)}</span>
            </button>
          ))}
          {pool.length === 0 ? (
            <p className="py-2 text-xs text-text-muted">No matches — use Search mode to add it first.</p>
          ) : null}
        </div>
      </div>
    );
  }

  return (
    <div className={cn("card border px-4 py-3", tone)} role="status">
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm font-semibold">
          {hit.status === "IDENTIFIED"
            ? `${methodLabel} — confirm to add`
            : hit.status === "REVIEW_REQUIRED"
              ? `${methodLabel} — please review`
              : "Product not confidently identified"}
        </p>
        <button type="button" className="btn btn-ghost px-2 py-1 text-xs" onClick={onDismiss}>
          <X className="h-3.5 w-3.5" />
          Dismiss
        </button>
      </div>
      {hit.status === "UNRESOLVED" ? (
        <p className="mt-2 text-xs opacity-80">
          Not in your catalog (or not confident). Scan the barcode, search manually, or add the product in
          Inventory.
        </p>
      ) : candidates.length > 0 ? (
        <div className="mt-2 grid gap-2 sm:grid-cols-2">
          {candidates.map((p) => (
            <div key={p.id} className="flex items-center justify-between gap-2 rounded-lg border border-border bg-white px-3 py-2">
              <div className="min-w-0">
                <p className="truncate text-[13px] font-semibold text-[#0b1f3a]">{p.name}</p>
                <p className="text-[11px] text-text-muted">
                  {formatINR(p.selling_price)} · {p.quantity} in stock
                </p>
              </div>
              <button
                type="button"
                className="btn btn-primary justify-center px-3 py-1.5 text-xs"
                onClick={() => onConfirm(p)}
                disabled={p.quantity <= 0}
              >
                {p.quantity <= 0 ? "No stock" : "Confirm & add"}
              </button>
            </div>
          ))}
        </div>
      ) : (
        <p className="mt-2 text-xs opacity-80">Resolving catalog candidates…</p>
      )}
      {hit.status !== "UNRESOLVED" ? (
        <div className="mt-2 flex items-center gap-2">
          <button
            type="button"
            className="btn btn-ghost px-2 py-1 text-xs text-[#b91c1c]"
            onClick={() => onReject(hit)}
          >
            <X className="h-3.5 w-3.5" />
            Wrong product
          </button>
          <span className="text-[11px] opacity-70">
            {methodLabel} · confidence {CONFIDENCE_PCT(hit.match.confidence)}
            {evidenceBits.length ? ` · ${evidenceBits.join(" · ")}` : ""}
          </span>
        </div>
      ) : null}
    </div>
  );
}

export default function SmartCounterPage() {
  return (
    <AuthGuard>
      <SmartCounter />
    </AuthGuard>
  );
}

function SmartCounter() {
  const router = useRouter();
  const [products, setProducts] = useState<Product[]>([]);
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [q, setQ] = useState("");
  const [barcode, setBarcode] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [cart, setCart] = useState<CartLine[]>([]);
  const [discount, setDiscount] = useState("0");
  const [payment, setPayment] = useState<PaymentMethod>("cash");
  const [customerId, setCustomerId] = useState("");
  const [newCustomer, setNewCustomer] = useState<{ name: string; phone: string } | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [bargainProduct, setBargainProduct] = useState<Product | null>(null);
  const [detailsProduct, setDetailsProduct] = useState<Product | null>(null);
  const [lastCreated, setLastCreated] = useState<string | null>(null);

  // ---- Input modes: Camera | Barcode | Search -------------------------------
  const [mode, setMode] = useState<InputMode>("search");
  const [cameraOverlay, setCameraOverlay] = useState<CameraOverlay | null>(null);
  const [resolvingBarcode, setResolvingBarcode] = useState(false);
  const [showBarcodeManual, setShowBarcodeManual] = useState(false);
  const overlayTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ---- Visual recognition (server-side; candidates need merchant confirm) --
  const [visionHit, setVisionHit] = useState<VisionHit | null>(null);
  const [visionCandidates, setVisionCandidates] = useState<Product[]>([]);
  const visionHitAtRef = useRef(0);

  const cameraEnabled = mode === "camera";
  const {
    videoRef,
    status: cameraStatus,
    devices: cameraDevices,
    activeDeviceId,
    switchDevice,
    restart: restartCamera,
  } = useCamera(cameraEnabled);
  const { health: visionHealth } = useVisionHealth(cameraEnabled);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [p, c] = await Promise.all([
        api<{ items: Product[] }>("/api/products"),
        api<{ items: Customer[] }>("/api/customers"),
      ]);
      setProducts(p.items);
      setCustomers(c.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load counter data");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  /**
   * Shared barcode resolution — used by the typed input, a hardware scanner
   * wedge, AND the camera scanner. One code path = one set of business rules.
   * Camera hits additionally surface per-scan feedback in the camera overlay.
   */
  const resolveBarcode = useCallback(
    async (code: string, from: "camera" | "input") => {
      const trimmed = code.trim();
      if (!trimmed) return;
      setFormError(null);
      setNotice(null);
      if (from === "camera") {
        setResolvingBarcode(true);
        setCameraOverlay({
          tone: "muted",
          title: `Code ${trimmed}`,
          description: "Looking up in your catalog…",
        });
      }
      const showOverlay = (o: CameraOverlay, ms = 4000) => {
        if (from !== "camera") return;
        if (overlayTimerRef.current) clearTimeout(overlayTimerRef.current);
        setCameraOverlay(o);
        overlayTimerRef.current = setTimeout(() => setCameraOverlay(null), ms);
      };
      try {
        const res = await api<{
          result: string;
          product?: Product & { status: string };
          matches?: Product[];
        }>(`/api/products/barcode/${encodeURIComponent(trimmed)}`);
        if (res.result === "FOUND" && res.product) {
          const product = res.product as Product;
          if (product.quantity <= 0) {
            showOverlay({
              tone: "error",
              title: `${product.name} is out of stock`,
              description: "Adjust stock in Inventory before selling.",
            });
            setFormError(`${product.name} is out of stock.`);
            return;
          }
          addToCart(product, 1);
          showOverlay({
            tone: "success",
            title: `Added: ${product.name}`,
            description: `${formatINR(product.selling_price)} · ${product.quantity} in stock`,
          });
          setNotice(`Scanned: ${product.name}`);
          if (from === "input") setBarcode("");
        } else if (res.result === "MULTIPLE_MATCHES") {
          showOverlay({
            tone: "error",
            title: "Barcode matches multiple products",
            description: `Pick the right one via search — ${res.matches?.length ?? 0} matches.`,
          });
          setFormError(
            `Barcode matches ${res.matches?.length} products — search by name and pick the right one.`
          );
        } else if (res.result === "INACTIVE_PRODUCT") {
          showOverlay({ tone: "error", title: "That product is inactive and cannot be sold." });
          setFormError("That product is inactive and cannot be sold.");
        } else {
          showOverlay({
            tone: "error",
            title: "Not in your catalog",
            description: `No product with barcode ${trimmed}. Add it in Inventory or search manually.`,
          });
          setFormError(`No product with barcode ${trimmed} in this store.`);
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : "Barcode lookup failed";
        const notFound = msg.includes("404") || msg.includes("No product with barcode");
        if (notFound) {
          showOverlay({
            tone: "error",
            title: "Not in your catalog",
            description: `No product with barcode ${trimmed}. Add it in Inventory or search manually.`,
          });
          setFormError(`No product with barcode ${trimmed} in this store.`);
        } else {
          showOverlay({ tone: "error", title: "Lookup failed", description: msg });
          setFormError(msg);
        }
      } finally {
        if (from === "camera") setResolvingBarcode(false);
      }
    },
    // addToCart/setNotice/etc. are stable-enough for this page's lifetime;
    // everything they read is captured at call time.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    []
  );

  /** Camera scanner: paused while a lookup is in flight. */
  const onCameraScan = useCallback(
    (hit: ScanHit) => {
      void resolveBarcode(hit.code, "camera");
    },
    [resolveBarcode]
  );
  const { engine: scanEngine, lastHit } = useBarcodeScanner(
    videoRef,
    cameraEnabled && cameraStatus === "ready",
    onCameraScan,
    useCallback(() => resolvingBarcode, [resolvingBarcode])
  );

  /** Server-side recognition hits → resolve candidates for confirmation. */
  const onVisionHit = useCallback(
    (hit: VisionHit) => {
      if (hit.match.method !== "BARCODE" && visionHealth?.recognition_ready) {
        visionHitAtRef.current = Date.now();
        setVisionHit(hit);
        void (async () => {
          const ids = [
            hit.match.product_id,
            ...hit.match.alternatives.map((a) => a.product_id),
          ].filter((x): x is string => Boolean(x));
          const found = await Promise.all(
            ids.map((id) =>
              api<Product>(`/api/products/${id}`).catch(() => null)
            )
          );
          setVisionCandidates(found.filter((p): p is Product => p !== null));
        })();
      }
    },
    [visionHealth?.recognition_ready]
  );

  useVisionRecognize(
    videoRef,
    cameraEnabled && cameraStatus === "ready",
    visionHealth,
    useCallback(
      () => resolvingBarcode || Date.now() - visionHitAtRef.current < 3000,
      [resolvingBarcode]
    ),
    onVisionHit
  );

  /** Record merchant feedback on a recognition event (idempotent upsert).
   *  Analytics only — never blocks or mutates the bill. */
  const sendVisionFeedback = useCallback(
    async (hit: VisionHit, action: "MERCHANT_CONFIRMED" | "MERCHANT_CORRECTED" | "MERCHANT_REJECTED", confirmedProductId?: string) => {
      if (!hit.eventId) return;
      try {
        await api(`/api/counter/events/${hit.eventId}/feedback`, {
          method: "POST",
          json: { action, confirmed_product_id: confirmedProductId ?? null },
        });
      } catch {
        // feedback is analytical; a failed call never affects the sale
      }
    },
    []
  );

  /** Merchant confirmed a vision candidate → stock-guarded add via the SAME rules. */
  function confirmVisionCandidate(p: Product) {
    addToCart(p, 1);
    if (visionHit) void sendVisionFeedback(visionHit, "MERCHANT_CONFIRMED");
    setVisionHit(null);
    setVisionCandidates([]);
    setNotice(`Added from camera: ${p.name}`);
  }

  /** Merchant corrected a wrong candidate → hard-negative feedback + add the right product. */
  function correctVisionCandidate(hit: VisionHit, p: Product) {
    addToCart(p, 1);
    void sendVisionFeedback(hit, "MERCHANT_CORRECTED", p.id);
    setVisionHit(null);
    setVisionCandidates([]);
    setNotice(`Corrected — added ${p.name}`);
  }

  /** Merchant rejected the detection → strong-negative feedback, no bill change. */
  function rejectVisionHit(hit: VisionHit) {
    void sendVisionFeedback(hit, "MERCHANT_REJECTED");
    setVisionHit(null);
    setVisionCandidates([]);
  }

  function dismissVisionHit() {
    setVisionHit(null);
    setVisionCandidates([]);
  }

  // Reset the camera overlay when leaving camera mode; clear pending timer.
  useEffect(() => {
    if (mode !== "camera") {
      setCameraOverlay(null);
      if (overlayTimerRef.current) {
        clearTimeout(overlayTimerRef.current);
        overlayTimerRef.current = null;
      }
    }
    return () => {
      if (overlayTimerRef.current) {
        clearTimeout(overlayTimerRef.current);
        overlayTimerRef.current = null;
      }
    };
  }, [mode]);

  const filtered = useMemo(() => {
    const term = q.trim().toLowerCase();
    if (!term) return products;
    return products.filter(
      (p) =>
        p.name.toLowerCase().includes(term) ||
        (p.sku || "").toLowerCase().includes(term) ||
        (p.barcode || "").includes(term) ||
        p.category.toLowerCase().includes(term)
    );
  }, [products, q]);

  const subtotal = cart.reduce(
    (sum, line) => sum + (line.unitPrice ?? line.product.selling_price) * line.quantity,
    0
  );
  const discountNum = Math.max(0, Number(discount) || 0);
  const total = Math.max(0, subtotal - discountNum);

  function addToCart(product: Product, qty: number = 1) {
    setFormError(null);
    setCart((prev) => {
      const existing = prev.find((l) => l.product.id === product.id);
      if (existing) {
        if (existing.quantity + qty > product.quantity) {
          setFormError(`Only ${product.quantity} in stock for ${product.name}`);
          return prev;
        }
        return prev.map((l) =>
          l.product.id === product.id ? { ...l, quantity: l.quantity + qty } : l
        );
      }
      if (product.quantity <= 0) {
        setFormError(`${product.name} is out of stock`);
        return prev;
      }
      if (qty > product.quantity) {
        setFormError(`Only ${product.quantity} in stock for ${product.name}`);
        return prev;
      }
      return [...prev, { product, quantity: qty }];
    });
  }

  function changeQty(productId: string, delta: number) {
    setFormError(null);
    setCart((prev) =>
      prev
        .map((l) => {
          if (l.product.id !== productId) return l;
          const next = l.quantity + delta;
          if (next <= 0) return l;
          if (next > l.product.quantity) {
            setFormError(`Only ${l.product.quantity} in stock for ${l.product.name}`);
            return l;
          }
          return { ...l, quantity: next };
        })
        .filter((l) => l.quantity > 0)
    );
  }

  function removeLine(productId: string) {
    setCart((prev) => prev.filter((l) => l.product.id !== productId));
  }

  /** Add stock at a negotiated price after the pricing engine approved it. */
  function addBargained(unitPrice: number, quantity: number) {
    if (!bargainProduct) return;
    const p = bargainProduct;
    setFormError(null);
    setCart((prev) => {
      const existing = prev.find((l) => l.product.id === p.id);
      if (existing) {
        const nextQty = existing.quantity + quantity;
        if (nextQty > p.quantity) {
          setFormError(`Only ${p.quantity} in stock for ${p.name}`);
          return prev;
        }
        return prev.map((l) =>
          l.product.id === p.id ? { ...l, quantity: nextQty, unitPrice } : l
        );
      }
      if (quantity > p.quantity) {
        setFormError(`Only ${p.quantity} in stock for ${p.name}`);
        return prev;
      }
      return [...prev, { product: p, quantity, unitPrice }];
    });
    setBargainProduct(null);
  }

  /** Barcode mode (typed input / USB scanner wedge) — same resolver as camera. */
  function lookupBarcode(e: FormEvent) {
    e.preventDefault();
    void resolveBarcode(barcode, "input");
  }

  async function ensureCustomer(): Promise<string | null> {
    if (customerId) return customerId;
    if (!newCustomer?.name.trim()) return null;
    const created = await api<{ id: string }>("/api/customers", {
      method: "POST",
      json: {
        name: newCustomer.name.trim(),
        phone: newCustomer.phone.trim() || null,
      },
    });
    return created.id;
  }

  async function createOrder(e: FormEvent) {
    e.preventDefault();
    if (submitting) return;
    setFormError(null);
    setNotice(null);

    if (cart.length === 0) {
      setFormError("Cart is empty. Add at least one product.");
      return;
    }
    if (discountNum > subtotal) {
      setFormError("Discount cannot exceed subtotal.");
      return;
    }

    setSubmitting(true);
    try {
      const custId = await ensureCustomer();
      const res = await api<{ order: { id: string }; order_id: string }>("/api/orders", {
        method: "POST",
        json: {
          items: cart.map((l) => ({
            product_id: l.product.id,
            quantity: l.quantity,
            unit_price: l.unitPrice,
            list_price_at_cart: l.product.selling_price,
          })),
          customer_id: custId,
          discount: discountNum,
          payment_method: payment,
        },
      });
      setLastCreated(res.order_id);
      // Hand off to checkout (revalidation + payment + split + receipt happen there)
      router.push(`/checkout/${res.order_id}`);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Order failed";
      try {
        const parsed = JSON.parse(message);
        setFormError(parsed.message || message);
      } catch {
        setFormError(message);
      }
      await load();
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AppShell>
      <div className="space-y-5">
        <PageHeader
          title="Smart Counter"
          description="Scan barcodes by camera or scanner, search products, build the bill, then check out with payment or split."
          icon={ScanLine}
          action={
            lastCreated ? (
              <span className="btn btn-ghost" onClick={() => router.push("/orders")}>
                View orders
              </span>
            ) : null
          }
        />

        {notice ? (
          <div className="flex items-center gap-2 rounded-xl border border-[#bbf7d0] bg-[#f0fdf4] px-4 py-3 text-sm text-[#15803d]">
            <CheckCircle2 className="h-4 w-4" />
            {notice}
          </div>
        ) : null}

        <div className="grid gap-4 xl:grid-cols-3">
          <div className="space-y-4 xl:col-span-2">
            {/* Input mode selector — Camera | Barcode | Search (spec §17 fallback hierarchy) */}
            <div className="flex items-center gap-2" role="tablist" aria-label="Product input mode">
              {(
                [
                  ["camera", "Camera", Camera],
                  ["barcode", "Barcode", ScanLine],
                  ["search", "Search", Search],
                ] as const
              ).map(([value, label, Icon]) => (
                <button
                  key={value}
                  type="button"
                  role="tab"
                  aria-selected={mode === value}
                  className={cn(
                    "flex items-center gap-1.5 rounded-xl border px-4 py-2 text-sm font-semibold transition",
                    mode === value
                      ? "border-primary bg-[#eff6ff] text-primary"
                      : "border-border bg-white text-text-secondary hover:bg-[#f8fafc]"
                  )}
                  onClick={() => setMode(value)}
                >
                  <Icon className="h-4 w-4" />
                  {label}
                </button>
              ))}
            </div>

            {mode === "camera" ? (
              <CameraWorkspace
                videoRef={videoRef}
                status={cameraStatus}
                engine={scanEngine}
                devices={cameraDevices}
                activeDeviceId={activeDeviceId}
                onSwitchDevice={switchDevice}
                onRetry={restartCamera}
                lastHit={lastHit}
                overlay={cameraOverlay}
                visionHealth={visionHealth}
              />
            ) : null}

            {mode === "camera" && visionHit ? (
              <VisionCandidateCard
                hit={visionHit}
                candidates={visionCandidates}
                onConfirm={confirmVisionCandidate}
                onCorrect={correctVisionCandidate}
                onReject={rejectVisionHit}
                onDismiss={dismissVisionHit}
              />
            ) : null}

            {mode === "search" ? (
            <div className="relative">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" />
              <input
                className="input pl-9"
                placeholder="Search product, SKU or barcode (e.g. Maggi, Parle-G)…"
                value={q}
                onChange={(e) => setQ(e.target.value)}
                aria-label="Search products"
              />
            </div>
            ) : null}

            {/* Barcode mode — CAMERA-FIRST (§12); typed entry is the fallback */}
            {mode === "barcode" ? (
              showBarcodeManual ? (
                <form onSubmit={lookupBarcode} className="flex gap-2">
                  <div className="relative flex-1">
                    <ScanLine className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" />
                    <input
                      className="input pl-9"
                      placeholder="Scan or type a barcode, then Enter…"
                      value={barcode}
                      onChange={(e) => setBarcode(e.target.value)}
                      aria-label="Barcode input"
                    />
                  </div>
                  <button type="submit" className="btn btn-secondary">
                    <ScanLine className="h-4 w-4" />
                    Look up
                  </button>
                </form>
              ) : (
                <BarcodeScannerPanel
                  onScan={(code) => void resolveBarcode(code, "camera")}
                  busy={resolvingBarcode}
                />
              )
            ) : null}

            {mode === "barcode" ? (
              <button
                type="button"
                className="btn btn-ghost w-full justify-center text-xs"
                onClick={() => setShowBarcodeManual((v) => !v)}
              >
                {showBarcodeManual ? "Use camera instead" : "Enter barcode manually"}
              </button>
            ) : null}

            {loading ? <LoadingState /> : null}
            {error && !loading ? <ErrorState message={error} onRetry={() => void load()} /> : null}

            {!loading && !error ? (
              filtered.length === 0 ? (
                <EmptyState title="No products match" description="Add products in Inventory first." />
              ) : (
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                  {filtered.map((p) => (
                    <div
                      key={p.id}
                      className="card cursor-pointer p-4 transition hover:border-primary"
                      onClick={() => setDetailsProduct(p)}
                    >
                      <div className="flex items-start gap-2.5">
                        <ProductImage
                          src={p.image_url}
                          alt={p.name}
                          className="h-11 w-11 shrink-0 rounded-lg border border-border"
                        />
                        <div className="min-w-0 flex-1">
                          <div className="flex items-start justify-between gap-2">
                            <span className="text-[13px] font-semibold leading-snug">{p.name}</span>
                            <StatusBadge label={p.status.replace("_", " ")} tone={statusTone(p.status)} />
                          </div>
                          <p className="mt-1 text-[11px] text-text-muted">{p.category}</p>
                        </div>
                      </div>
                      <div className="mt-3 flex items-center justify-between">
                        <span className="text-lg font-bold">{formatINR(p.selling_price)}</span>
                        <span className="text-[11px] text-text-secondary">Stock: {p.quantity}</span>
                      </div>
                      <div className="mt-3 grid grid-cols-2 gap-2">
                        <button
                          type="button"
                          className="btn btn-secondary justify-center py-1.5 text-xs"
                          onClick={(e) => {
                            e.stopPropagation();
                            addToCart(p);
                          }}
                          disabled={p.quantity <= 0}
                        >
                          {p.quantity <= 0 ? "Out of stock" : "Add to bill"}
                        </button>
                        <button
                          type="button"
                          className="btn btn-ghost justify-center py-1.5 text-xs"
                          onClick={(e) => {
                            e.stopPropagation();
                            setBargainProduct(p);
                          }}
                          disabled={p.quantity <= 0}
                        >
                          <HandCoins className="h-3.5 w-3.5" />
                          Bargain
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )
            ) : null}
          </div>

          <form onSubmit={createOrder} className="card flex h-fit flex-col p-5">
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-base font-bold">Current bill</h2>
              <span className="text-xs text-text-muted">{cart.length} item lines</span>
            </div>

            {formError ? (
              <div className="mb-3 rounded-xl border border-[#fecaca] bg-[#fef2f2] px-3 py-2 text-sm text-[#b91c1c]">
                {formError}
              </div>
            ) : null}

            {cart.length === 0 ? (
              <EmptyState title="Cart is empty" description="Select products on the left." />
            ) : (
              <ul className="mb-4 space-y-3">
                {cart.map((line) => (
                  <li key={line.product.id} className="rounded-xl border border-border p-3">
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <p className="text-sm font-semibold">{line.product.name}</p>
                        <p className="text-[11px] text-text-muted">
                          {formatINR(line.unitPrice ?? line.product.selling_price)} each
                          {line.unitPrice != null && line.unitPrice !== line.product.selling_price ? (
                            <span className="ml-1 font-semibold text-[#b45309]">(negotiated)</span>
                          ) : null}
                        </p>
                      </div>
                      <button
                        type="button"
                        className="text-text-muted hover:text-danger"
                        onClick={() => removeLine(line.product.id)}
                        aria-label={`Remove ${line.product.name}`}
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </div>
                    <div className="mt-2 flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <button
                          type="button"
                          className="btn btn-ghost h-8 w-8 p-0"
                          onClick={() => changeQty(line.product.id, -1)}
                          aria-label="Decrease quantity"
                        >
                          <Minus className="h-3.5 w-3.5" />
                        </button>
                        <span className="w-6 text-center font-semibold">{line.quantity}</span>
                        <button
                          type="button"
                          className="btn btn-ghost h-8 w-8 p-0"
                          onClick={() => changeQty(line.product.id, 1)}
                          aria-label="Increase quantity"
                        >
                          <Plus className="h-3.5 w-3.5" />
                        </button>
                      </div>
                      <span className="font-semibold">
                        {formatINR((line.unitPrice ?? line.product.selling_price) * line.quantity)}
                      </span>
                    </div>
                  </li>
                ))}
              </ul>
            )}

            <label className="mb-2 block text-sm">
              <span className="mb-1 block font-medium">Discount (₹)</span>
              <input
                className="input"
                type="number"
                min="0"
                step="0.01"
                value={discount}
                onChange={(e) => setDiscount(e.target.value)}
              />
            </label>

            <label className="mb-2 block text-sm">
              <span className="mb-1 block font-medium">Customer (optional)</span>
              <select
                className="input"
                value={customerId}
                onChange={(e) => {
                  setCustomerId(e.target.value);
                  if (e.target.value) setNewCustomer(null);
                }}
              >
                <option value="">Walk-in customer</option>
                {customers.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                    {c.phone ? ` · ${c.phone}` : ""}
                  </option>
                ))}
              </select>
            </label>

            {!customerId ? (
              <div className="mb-2 rounded-xl border border-dashed border-border p-3">
                <button
                  type="button"
                  className="flex items-center gap-1 text-xs font-semibold text-primary"
                  onClick={() => setNewCustomer((v) => (v ? null : { name: "", phone: "" }))}
                >
                  <UserPlus className="h-3.5 w-3.5" />
                  {newCustomer ? "Cancel new customer" : "Add new customer on this sale"}
                </button>
                {newCustomer ? (
                  <div className="mt-2 space-y-2">
                    <input
                      className="input"
                      placeholder="Customer name *"
                      required
                      value={newCustomer.name}
                      onChange={(e) => setNewCustomer({ ...newCustomer, name: e.target.value })}
                    />
                    <input
                      className="input"
                      placeholder="Phone (optional)"
                      value={newCustomer.phone}
                      onChange={(e) => setNewCustomer({ ...newCustomer, phone: e.target.value })}
                    />
                  </div>
                ) : null}
              </div>
            ) : null}

            <div className="mb-3">
              <span className="mb-1.5 block text-sm font-medium">Payment method</span>
              <div className="grid grid-cols-2 gap-2">
                {(
                  [
                    ["cash", "Cash"],
                    ["upi", "UPI"],
                    ["card", "Card"],
                    ["split", "Split"],
                  ] as const
                ).map(([value, label]) => (
                  <button
                    key={value}
                    type="button"
                    className={cn(
                      "rounded-xl border px-3 py-2 text-sm font-semibold",
                      payment === value
                        ? "border-primary bg-[#eff6ff] text-primary"
                        : "border-border bg-white text-text-secondary"
                    )}
                    onClick={() => setPayment(value)}
                  >
                    {label}
                  </button>
                ))}
              </div>
              {payment === "split" ? (
                <p className="mt-1 flex items-start gap-1 text-[11px] text-text-muted">
                  <Info className="mt-0.5 h-3 w-3 shrink-0" />
                  Split payment: create participants at checkout; the order completes only when all
                  parts are confirmed.
                </p>
              ) : null}
            </div>

            <div className="mb-3 space-y-1 text-sm">
              <div className="flex justify-between text-text-secondary">
                <span>Subtotal</span>
                <span>{formatINR(subtotal)}</span>
              </div>
              <div className="flex justify-between text-text-secondary">
                <span>Discount</span>
                <span>- {formatINR(discountNum)}</span>
              </div>
              <div className="flex justify-between border-t border-border pt-2 text-base font-bold">
                <span>Total</span>
                <span>{formatINR(total)}</span>
              </div>
            </div>

            <button type="submit" className="btn btn-primary w-full" disabled={submitting || cart.length === 0}>
              {submitting ? (
                "Creating order…"
              ) : (
                <>
                  <CreditCard className="h-4 w-4" />
                  Proceed to checkout {formatINR(total)}
                </>
              )}
            </button>
            <p className="mt-2 flex items-center gap-1 text-[11px] text-text-muted">
              <X className="h-3 w-3 opacity-0" />
              Totals are revalidated on the backend at checkout; stock is only deducted after
              confirmed payment.
            </p>
          </form>
        </div>
      </div>

      {bargainProduct ? (
        <BargainModal
          product={{
            id: bargainProduct.id,
            name: bargainProduct.name,
            selling_price: bargainProduct.selling_price,
            quantity: bargainProduct.quantity,
          }}
          onAccept={addBargained}
          onClose={() => setBargainProduct(null)}
        />
      ) : null}

      {detailsProduct ? (
        <ProductDetailsModal
          product={detailsProduct}
          onAddToCart={(p, qty) => addToCart(p, qty)}
          onBargain={(p) => setBargainProduct(p)}
          onClose={() => setDetailsProduct(null)}
        />
      ) : null}
    </AppShell>
  );
}
