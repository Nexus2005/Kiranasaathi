"use client";

/**
 * AddInventoryModal — inventory onboarding as the PRIMARY enrollment path.
 *
 * Three entry points (§1): [Scan Barcode] (camera, primary) / [Search Product]
 * / [Add Manually]. Unknown barcode → local-then-OpenFoodFacts lookup
 * (backend-cached) → PREFILL + "verify before saving" banner → merchant sets
 * their OWN business fields (SOURCE: Store) → image capture → visual
 * enrollment (DINOv2, retrieval indexing — NOT training).
 */

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { Camera, ImageIcon, Loader2, PackagePlus, Search, ScanLine } from "lucide-react";
import { api } from "@/lib/api";
import { BarcodeScannerPanel } from "@/components/barcode-scanner-panel";
import { cn } from "@/lib/cn";

type Step = "choose" | "scan" | "enrich" | "manual" | "images" | "done";

type LocalHit = {
  id: string;
  name: string;
  category: string | null;
  barcode: string | null;
  mrp: number | null;
  selling_price: number | null;
  quantity: number;
  image_count: number;
};

type ExternalPrefill = {
  product_name?: string;
  brands?: string;
  quantity?: string;
  categories?: string;
  category_hint?: string;
  image_front_url?: string;
  image_url?: string;
};

type EnrichResponse = {
  result: "FOUND_LOCAL" | "FOUND_EXTERNAL" | "NOT_FOUND";
  source: string;
  product?: LocalHit & Record<string, unknown>;
  license_note?: string;
};

const OFF_BANNER = "External product information — please verify before saving.";

export function AddInventoryModal({
  open,
  onClose,
  onDone,
}: {
  open: boolean;
  onClose: () => void;
  onDone: (message: string) => void;
}) {
  const [step, setStep] = useState<Step>("choose");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // scan / lookup state
  const [resolving, setResolving] = useState(false);
  const [scannedCode, setScannedCode] = useState<string | null>(null);

  // enrichment state
  const [localHit, setLocalHit] = useState<LocalHit | null>(null);
  const [ext, setExt] = useState<ExternalPrefill | null>(null);
  const [licenseNote, setLicenseNote] = useState<string | null>(null);

  // search state
  const [q, setQ] = useState("");
  const [searchItems, setSearchItems] = useState<LocalHit[]>([]);

  // form (merchant-owned business fields + confirmed identity)
  const [form, setForm] = useState({
    name: "",
    brand: "",
    category: "Packaged Food",
    pack_size: "",
    barcode: "",
    mrp: "",
    selling_price: "",
    purchase_price: "",
    initial_stock: "0",
    reorder_level: "10",
  });

  // image capture
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [camOn, setCamOn] = useState(false);
  const [shots, setShots] = useState<{ data: string; blob: Blob }[]>([]);
  const [enrollResult, setEnrollResult] = useState<string | null>(null);

  const reset = useCallback(() => {
    setStep("choose");
    setBusy(false);
    setError(null);
    setResolving(false);
    setScannedCode(null);
    setLocalHit(null);
    setExt(null);
    setLicenseNote(null);
    setQ("");
    setSearchItems([]);
    setForm({
      name: "", brand: "", category: "Packaged Food", pack_size: "", barcode: "",
      mrp: "", selling_price: "", purchase_price: "", initial_stock: "0", reorder_level: "10",
    });
    setCamOn(false);
    setShots([]);
    setEnrollResult(null);
  }, []);

  useEffect(() => {
    if (open) reset();
  }, [open, reset]);

  // ---- local → external lookup chain (one code path for scan AND typed) ----
  // NOTE: all hooks must run unconditionally (Rules of Hooks) — the
  // `if (!open) return null` gate lives below, after every hook.
  const lookup = useCallback(async (code: string) => {
    setResolving(true);
    setError(null);
    try {
      const res = await api<EnrichResponse>(`/api/enrichment/product/${encodeURIComponent(code.trim())}`);
      if (res.result === "FOUND_LOCAL" && res.product) {
        setLocalHit(res.product);
        setScannedCode(code.trim());
        // existing product → only stock top-up asked later; prefill identity
        setForm((f) => ({
          ...f,
          name: res.product!.name,
          category: res.product!.category || f.category,
          barcode: res.product!.barcode || code.trim(),
          mrp: res.product!.mrp != null ? String(res.product!.mrp) : f.mrp,
          selling_price: res.product!.selling_price != null ? String(res.product!.selling_price) : f.selling_price,
        }));
        setStep("enrich");
      } else if (res.result === "FOUND_EXTERNAL" && res.product) {
        const p = res.product as unknown as ExternalPrefill;
        setExt(p);
        setLicenseNote(res.license_note ?? null);
        setScannedCode(code.trim());
        setForm((f) => ({
          ...f,
          name: p.product_name || f.name,
          brand: p.brands?.split(",")[0]?.trim() || f.brand,
          category: p.category_hint || p.categories?.split(",")[0]?.split(":").pop()?.trim() || f.category,
          pack_size: p.quantity || f.pack_size,
          barcode: code.trim(),
        }));
        setStep("enrich");
      } else {
        setScannedCode(code.trim());
        setStep("manual");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Lookup failed");
      setScannedCode(code.trim());
      setStep("manual");
    } finally {
      setResolving(false);
    }
  }, []);

  const onScan = useCallback((code: string) => void lookup(code), [lookup]);

  // ---- product search (existing catalog) ----
  useEffect(() => {
    if (step !== "choose") return;
    const term = q.trim();
    if (term.length < 2) { setSearchItems([]); return; }
    const t = setTimeout(async () => {
      try {
        const res = await api<{ items: (LocalHit & { product_id?: string })[] }>(`/api/inventory/intelligence?q=${encodeURIComponent(term)}`);
        setSearchItems((res.items || []).slice(0, 6).map((i) => ({
          id: i.id ?? i.product_id ?? "",   // intelligence rows expose product_id
          name: i.name, category: i.category ?? null, barcode: i.barcode ?? null,
          mrp: i.mrp ?? null, selling_price: i.selling_price ?? null, quantity: i.quantity ?? 0,
          image_count: i.image_count ?? 0,
        })).filter((i) => i.id));
      } catch { setSearchItems([]); }
    }, 250);
    return () => clearTimeout(t);
  }, [q, step]);

  // ---- image capture ----
  useEffect(() => {
    if (step !== "images" || !camOn) return;
    let stream: MediaStream | null = null;
    let cancelled = false;
    (async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
        if (cancelled) { stream.getTracks().forEach((t) => t.stop()); return; }
        if (videoRef.current) {
          videoRef.current.srcObject = stream;
          await videoRef.current.play().catch(() => undefined);
        }
      } catch {
        setError("Camera unavailable — you can upload an image file instead.");
      }
    })();
    return () => { cancelled = true; stream?.getTracks().forEach((t) => t.stop()); };
  }, [step, camOn]);

  // Gate AFTER every hook — conditional returns must not change hook order.
  if (!open) return null;

  function capture() {
    const video = videoRef.current;
    if (!video || video.videoWidth === 0) return;
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext("2d")!.drawImage(video, 0, 0);
    canvas.toBlob(
      (blob) => {
        if (!blob) return;
        setShots((s) => [...s, { data: canvas.toDataURL("image/jpeg", 0.9), blob }]);
      },
      "image/jpeg",
      0.9
    );
  }

  async function enrollImages(productId: string): Promise<string[]> {
    const results: string[] = [];
    for (const shot of shots) {
      const fd = new FormData();
      fd.append("image", shot.blob, "capture.jpg");
      fd.append("view", shots.indexOf(shot) === 0 ? "front" : "angle");
      const res = await fetch(`/api/enrichment/products/${productId}/images`, {
        method: "POST",
        headers: { Authorization: `Bearer ${localStorage.getItem("ks_token") ?? ""}` },
        body: fd,
      });
      if (res.ok) results.push((await res.json()).embedding_id);
    }
    return results;
  }

  // ---- create / update ----
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (localHit) {
        // existing product → add stock only
        const qty = Number(form.initial_stock) || 0;
        if (qty > 0) {
          await api("/api/enrichment/add-stock", {
            method: "POST",
            json: { product_id: localHit.id, quantity: qty },
          });
        }
        let enrolled = "";
        if (shots.length) {
          const ids = await enrollImages(localHit.id);
          enrolled = ids.length ? `, ${ids.length} image(s) enrolled` : "";
        }
        onDone(`Added ${qty} × ${localHit.name} to inventory${enrolled}.`);
      } else {
        const created = await api<{ product_id: string; image_ingested: boolean }>(
          "/api/enrichment/create-from-external",
          {
            method: "POST",
            json: {
              barcode: form.barcode,
              name: form.name,
              brand: form.brand || null,
              category: form.category || null,
              pack_size: form.pack_size || null,
              external_image_url: (!shots.length && ext?.image_front_url) || ext?.image_url || null,
              mrp: Number(form.mrp),
              selling_price: Number(form.selling_price),
              purchase_price: Number(form.purchase_price),
              initial_stock: Number(form.initial_stock) || 0,
              reorder_level: Number(form.reorder_level) || 10,
            },
          }
        );
        let enrolled = "";
        if (shots.length) {
          const ids = await enrollImages(created.product_id);
          enrolled = ids.length ? `, ${ids.length} image(s) enrolled` : "";
        } else if (created.image_ingested) {
          enrolled = ", external image enrolled";
        }
        onDone(`Created ${form.name}${enrolled}. Recognition-ready once enrolled.`);
      }
      setStep("done");
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4 pt-[6vh]" role="dialog" aria-modal="true">
      <div className="w-full max-w-lg rounded-2xl bg-white shadow-xl">
        <div className="flex items-center justify-between border-b border-border px-5 py-3">
          <h2 className="flex items-center gap-2 text-sm font-bold">
            <PackagePlus className="h-4 w-4 text-primary" />
            Add products to inventory
          </h2>
          <button type="button" className="text-text-muted hover:text-text-primary" onClick={onClose} aria-label="Close">✕</button>
        </div>

        <div className="space-y-4 px-5 py-4">
          {error ? <div className="rounded-xl bg-red-50 px-3 py-2 text-xs text-red-700">{error}</div> : null}

          {step === "choose" ? (
            <>
              <button type="button" className="btn btn-primary w-full justify-center" onClick={() => setStep("scan")}>
                <ScanLine className="h-4 w-4" /> Scan barcode
              </button>
              <div className="relative">
                <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" />
                <input
                  className="input pl-9"
                  placeholder="Search your products…"
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                  aria-label="Search products"
                />
              </div>
              {searchItems.length > 0 ? (
                <ul className="divide-y divide-border overflow-hidden rounded-xl border border-border">
                  {searchItems.map((it) => (
                    <li key={it.id}>
                      <button
                        type="button"
                        className="flex w-full items-center justify-between px-3 py-2 text-left text-xs hover:bg-[#f8fafc]"
                        onClick={() => { setLocalHit(it); setScannedCode(it.barcode); setForm((f) => ({ ...f, name: it.name, category: it.category || f.category, barcode: it.barcode || "", mrp: it.mrp != null ? String(it.mrp) : f.mrp, selling_price: it.selling_price != null ? String(it.selling_price) : f.selling_price })); setStep("enrich"); }}
                      >
                        <span className="font-semibold">{it.name}</span>
                        <span className="text-text-muted">{it.image_count > 0 ? `${it.image_count} img` : "no image"} · stock {it.quantity}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              ) : null}
              <div className="text-center text-[11px] text-text-muted">— or —</div>
              <button type="button" className="btn btn-secondary w-full justify-center" onClick={() => setStep("manual")}>
                Add product manually
              </button>
            </>
          ) : null}

          {step === "scan" ? (
            <>
              <BarcodeScannerPanel onScan={onScan} busy={resolving} />
              {resolving ? (
                <p className="flex items-center gap-2 text-xs text-text-secondary">
                  <Loader2 className="h-3 w-3 animate-spin" /> Looking up {scannedCode ?? "barcode"}…
                </p>
              ) : null}
              <button type="button" className="btn btn-ghost w-full justify-center text-xs" onClick={() => setStep("manual")}>
                Enter barcode manually instead
              </button>
            </>
          ) : null}

          {step === "enrich" ? (
            <form onSubmit={submit} className="space-y-3">
              {ext ? (
                <div className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] text-amber-800">
                  <p className="font-semibold">{OFF_BANNER}</p>
                  {licenseNote ? <p className="mt-0.5 text-[10px] text-amber-700">{licenseNote}</p> : null}
                </div>
              ) : localHit ? (
                <div className="rounded-xl bg-[#eff6ff] px-3 py-2 text-[11px] text-[#1e40af]">
                  In your catalog — confirm stock to add{scannedCode ? ` (barcode ${scannedCode})` : ""}.
                </div>
              ) : null}

              <label className="block text-xs font-semibold">
                Product name
                <input className="input mt-1" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
              </label>
              <div className="grid grid-cols-2 gap-3">
                <label className="block text-xs font-semibold">
                  Brand
                  <input className="input mt-1" value={form.brand} onChange={(e) => setForm({ ...form, brand: e.target.value })} />
                </label>
                <label className="block text-xs font-semibold">
                  Pack size
                  <input className="input mt-1" value={form.pack_size} onChange={(e) => setForm({ ...form, pack_size: e.target.value })} placeholder="70 g" />
                </label>
              </div>
              <div className="grid grid-cols-3 gap-3">
                <label className="block text-xs font-semibold">
                  Cost ₹
                  <input className="input mt-1" type="number" min="0" step="0.01" value={form.purchase_price} onChange={(e) => setForm({ ...form, purchase_price: e.target.value })} required />
                </label>
                <label className="block text-xs font-semibold">
                  Selling ₹
                  <input className="input mt-1" type="number" min="0" step="0.01" value={form.selling_price} onChange={(e) => setForm({ ...form, selling_price: e.target.value })} required />
                </label>
                <label className="block text-xs font-semibold">
                  MRP ₹
                  <input className="input mt-1" type="number" min="0" step="0.01" value={form.mrp} onChange={(e) => setForm({ ...form, mrp: e.target.value })} />
                </label>
              </div>
              <p className="text-[10px] text-text-muted">
                Identity fields {ext ? "SOURCE: Open Food Facts" : "SOURCE: store catalog"} · prices/stock SOURCE: Store (yours)
              </p>
              <ImageSection
                camOn={camOn} setCamOn={setCamOn} videoRef={videoRef}
                shots={shots} setShots={setShots} capture={capture}
                extImageUrl={ext?.image_front_url ?? ext?.image_url}
              />
              <div className="grid grid-cols-2 gap-3">
                <label className="block text-xs font-semibold">
                  Stock to add
                  <input className="input mt-1" type="number" min="0" value={form.initial_stock} onChange={(e) => setForm({ ...form, initial_stock: e.target.value })} />
                </label>
                <label className="block text-xs font-semibold">
                  Reorder level
                  <input className="input mt-1" type="number" min="0" value={form.reorder_level} onChange={(e) => setForm({ ...form, reorder_level: e.target.value })} />
                </label>
              </div>
              <button type="submit" className="btn btn-primary w-full justify-center" disabled={busy}>
                {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <PackagePlus className="h-4 w-4" />}
                {localHit ? "Add to inventory" : "Create & enroll"}
              </button>
            </form>
          ) : null}

          {step === "manual" ? (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                if (!scannedCode && !form.barcode) {
                  // manual without a scanned code keeps typed barcode
                }
                setStep("enrich");
              }}
              className="space-y-3"
            >
              <p className="text-[11px] text-text-secondary">
                Not found in your catalog or Open Food Facts — fill the product in manually.
              </p>
              <label className="block text-xs font-semibold">
                Barcode {scannedCode ? "(scanned)" : ""}
                <input className="input mt-1" value={scannedCode ?? form.barcode} onChange={(e) => setForm({ ...form, barcode: e.target.value })} disabled={!!scannedCode} placeholder="Leave empty for unbarcoded local product" />
              </label>
              <label className="block text-xs font-semibold">
                Product name
                <input className="input mt-1" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
              </label>
              <div className="grid grid-cols-2 gap-3">
                <label className="block text-xs font-semibold">
                  Brand
                  <input className="input mt-1" value={form.brand} onChange={(e) => setForm({ ...form, brand: e.target.value })} />
                </label>
                <label className="block text-xs font-semibold">
                  Pack size
                  <input className="input mt-1" value={form.pack_size} onChange={(e) => setForm({ ...form, pack_size: e.target.value })} placeholder="70 g" />
                </label>
              </div>
              <button type="submit" className="btn btn-primary w-full justify-center">Continue</button>
              {scannedCode ? (
                <button type="button" className="btn btn-ghost w-full justify-center text-xs" onClick={() => { setScannedCode(null); setStep("scan"); }}>
                  ← Rescan
                </button>
              ) : null}
            </form>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function ImageSection({
  camOn, setCamOn, videoRef, shots, setShots, capture, extImageUrl,
}: {
  camOn: boolean;
  setCamOn: (v: boolean) => void;
  videoRef: React.RefObject<HTMLVideoElement | null>;
  shots: { data: string; blob: Blob }[];
  setShots: React.Dispatch<React.SetStateAction<{ data: string; blob: Blob }[]>>;
  capture: () => void;
  extImageUrl?: string;
}) {
  return (
    <div className="rounded-xl border border-border p-3">
      <div className="flex items-center justify-between">
        <p className="text-xs font-semibold">Product image (visual enrollment)</p>
        <button type="button" className="btn btn-ghost px-2 py-1 text-xs" onClick={() => setCamOn(!camOn)}>
          <Camera className="h-3.5 w-3.5" /> {camOn ? "Stop camera" : "Capture"}
        </button>
      </div>
      {camOn ? (
        <div className="mt-2 space-y-2">
          <video ref={videoRef} className="aspect-video w-full rounded-lg bg-black object-cover" muted playsInline autoPlay />
          <button type="button" className="btn btn-secondary w-full justify-center py-1.5 text-xs" onClick={capture}>
            Take photo ({shots.length})
          </button>
        </div>
      ) : extImageUrl && shots.length === 0 ? (
        <div className="mt-2 flex items-center gap-2 text-[11px] text-text-secondary">
          <ImageIcon className="h-3.5 w-3.5" />
          External product image will be used (with attribution) unless you capture your own.
        </div>
      ) : null}
      {shots.length > 0 ? (
        <div className="mt-2 flex gap-2">
          {shots.map((s, i) => (
            <div key={i} className="relative">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={s.data} alt={`capture ${i + 1}`} className="h-12 w-12 rounded-lg object-cover" />
              <button
                type="button"
                aria-label="Remove"
                className="absolute -right-1 -top-1 h-4 w-4 rounded-full bg-red-600 text-[9px] font-bold text-white"
                onClick={() => setShots((prev) => prev.filter((_, j) => j !== i))}
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      ) : null}
      <p className="mt-1 text-[10px] text-text-muted">
        One image is enough to start. Enrollment = embedding for retrieval (the AI model is not retrained).
      </p>
    </div>
  );
}
