"use client";

/**
 * useBarcodeScanner — barcode detection for the Smart Counter camera mode.
 *
 * Detection strategy (real, local, no upload):
 *   1. Native `BarcodeDetector` when the browser provides it
 *      (Chrome/Edge/Android — fastest path, GPU-accelerated).
 *   2. `@zxing/browser` BrowserMultiFormatReader (wasm-free JS) fallback for
 *      Safari/Firefox — covers EAN-13/8, UPC-A/E, Code128, Code39, ITF, QR,
 *      DataMatrix, etc.
 *
 * Cadence + dedupe:
 *   - Detection runs on a fixed interval (default 450 ms, tunable via
 *     NEXT_PUBLIC_COUNTER_SCAN_INTERVAL_MS) — never per animation frame.
 *   - The same code value within the repeat-lockout window (default 2500 ms)
 *     is not re-emitted, so holding a product in front of the camera yields
 *     one scan, not a stream of adds.
 *   - While a handler is running (e.g. awaiting /api/products/barcode),
 *     detection pauses to avoid double-fire.
 *
 * The hook never touches cart/billing state; it only reports detected codes.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { BrowserMultiFormatReader } from "@zxing/browser";
import {
  BarcodeFormat,
  DecodeHintType,
  type Result,
} from "@zxing/library";

export type ScanHit = { code: string; format: string; at: number };

const DEFAULT_INTERVAL_MS = 450;
const DEFAULT_REPEAT_LOCKOUT_MS = 2500;

function intervalMs(): number {
  const raw = Number(process.env.NEXT_PUBLIC_COUNTER_SCAN_INTERVAL_MS);
  return Number.isFinite(raw) && raw >= 150 ? raw : DEFAULT_INTERVAL_MS;
}

function repeatLockoutMs(): number {
  const raw = Number(process.env.NEXT_PUBLIC_COUNTER_REPEAT_LOCKOUT_MS);
  return Number.isFinite(raw) && raw >= 500 ? raw : DEFAULT_REPEAT_LOCKOUT_MS;
}

/** Retail-relevant formats for the ZXing fallback. */
const ZXING_FORMATS: BarcodeFormat[] = [
  BarcodeFormat.EAN_13,
  BarcodeFormat.EAN_8,
  BarcodeFormat.UPC_A,
  BarcodeFormat.UPC_E,
  BarcodeFormat.CODE_128,
  BarcodeFormat.CODE_39,
  BarcodeFormat.ITF,
  BarcodeFormat.CODABAR,
  BarcodeFormat.QR_CODE,
  BarcodeFormat.DATA_MATRIX,
];

type NativeDetector = {
  detect: (source: CanvasImageSource) => Promise<
    { rawValue: string; format: string }[]
  >;
};

/** Track native BarcodeDetector availability (SSR-safe). */
function detectNativeSupport(): boolean {
  if (typeof window === "undefined") return false;
  const ctor = (window as unknown as { BarcodeDetector?: unknown }).BarcodeDetector;
  if (typeof ctor !== "function") return false;
  // Some browsers expose the constructor without implementing getSupportedFormats
  const getFormats = (ctor as { getSupportedFormats?: () => Promise<string[]> })
    .getSupportedFormats;
  if (typeof getFormats !== "function") return true;
  // availability check happens async in the effect
  return true;
}

export function useBarcodeScanner(
  videoRef: React.RefObject<HTMLVideoElement | null>,
  enabled: boolean,
  onScan: (hit: ScanHit) => void,
  isBusy: () => boolean
) {
  const [engine, setEngine] = useState<"native" | "zxing" | null>(null);
  const [lastHit, setLastHit] = useState<ScanHit | null>(null);

  const onScanRef = useRef(onScan);
  onScanRef.current = onScan;
  const isBusyRef = useRef(isBusy);
  isBusyRef.current = isBusy;

  const lastValueRef = useRef<string | null>(null);
  const lastAtRef = useRef(0);
  const busyRef = useRef(false);
  const nativeRef = useRef<NativeDetector | null>(null);
  const zxingRef = useRef<BrowserMultiFormatReader | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const rafRef = useRef<number | null>(null);
  const mountedRef = useRef(true);

  const emit = useCallback((code: string, format: string) => {
    const now = Date.now();
    const normalized = code.trim();
    if (!normalized) return;
    if (
      lastValueRef.current === normalized &&
      now - lastAtRef.current < repeatLockoutMs()
    ) {
      return; // same physical product still in front of the camera
    }
    lastValueRef.current = normalized;
    lastAtRef.current = now;
    const hit: ScanHit = { code: normalized, format, at: now };
    setLastHit(hit);
    busyRef.current = true;
    try {
      onScanRef.current(hit);
    } finally {
      // Release the pause on the next tick so async handlers finishing later
      // still get a window; handlers that need a longer pause manage their
      // own state via isBusy().
      setTimeout(() => {
        busyRef.current = false;
      }, 0);
    }
  }, []);

  // Lazy-init engines once when first enabled.
  useEffect(() => {
    if (!enabled) return;
    mountedRef.current = true;

    let cancelled = false;

    async function init() {
      const ctor = (
        window as unknown as {
          BarcodeDetector?: new (options?: { formats?: string[] }) => NativeDetector;
        }
      ).BarcodeDetector;
      if (typeof ctor === "function") {
        try {
          const wanted = ["ean_13", "ean_8", "upc_a", "upc_e", "code_128", "code_39", "itf", "qr_code", "data_matrix"];
          const supported = (
            ctor as unknown as { getSupportedFormats?: () => Promise<string[]> }
          ).getSupportedFormats
            ? await (
                ctor as unknown as { getSupportedFormats: () => Promise<string[]> }
              ).getSupportedFormats()
            : wanted;
          const formats = wanted.filter((f) => supported.includes(f));
          if (formats.length > 0 && !cancelled) {
            nativeRef.current = new ctor({ formats });
            setEngine("native");
            return;
          }
        } catch {
          // fall through to zxing
        }
      }
      if (cancelled) return;
      const hints = new Map<DecodeHintType, unknown>();
      hints.set(DecodeHintType.POSSIBLE_FORMATS, ZXING_FORMATS);
      hints.set(DecodeHintType.TRY_HARDER, true);
      zxingRef.current = new BrowserMultiFormatReader(hints, {
        delayBetweenScanAttempts: intervalMs(),
        delayBetweenScanSuccess: repeatLockoutMs(),
      });
      setEngine("zxing");
    }

    void init();
    return () => {
      cancelled = true;
      mountedRef.current = false;
    };
  }, [enabled]);

  // Native loop: interval + drawImage to an offscreen canvas (throttled).
  useEffect(() => {
    if (!enabled || engine !== "native") return;

    function ensureCanvas(): HTMLCanvasElement {
      if (!canvasRef.current) {
        canvasRef.current = document.createElement("canvas");
      }
      return canvasRef.current;
    }

    async function tick() {
      if (busyRef.current || isBusyRef.current()) return;
      const video = videoRef.current;
      const detector = nativeRef.current;
      if (!video || !detector || video.readyState < 2 || video.videoWidth === 0) return;
      const canvas = ensureCanvas();
      const w = Math.min(video.videoWidth, 960);
      const scale = w / video.videoWidth;
      canvas.width = Math.round(video.videoWidth * scale);
      canvas.height = Math.round(video.videoHeight * scale);
      const ctx = canvas.getContext("2d", { willReadFrequently: true });
      if (!ctx) return;
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
      try {
        const results = await detector.detect(canvas);
        if (results.length > 0) {
          const best = results[0];
          emit(best.rawValue, best.format);
        }
      } catch {
        // transient decode errors are normal on blurry frames; ignore
      }
    }

    timerRef.current = setInterval(() => {
      void tick();
    }, intervalMs());

    return () => {
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [enabled, engine, emit, videoRef]);

  // ZXing fallback loop: decodeFromCanvas on the same throttle.
  useEffect(() => {
    if (!enabled || engine !== "zxing") return;

    function ensureCanvas(): HTMLCanvasElement {
      if (!canvasRef.current) {
        canvasRef.current = document.createElement("canvas");
      }
      return canvasRef.current;
    }

    function tick() {
      if (busyRef.current || isBusyRef.current()) return;
      const video = videoRef.current;
      const reader = zxingRef.current;
      if (!video || !reader || video.readyState < 2 || video.videoWidth === 0) return;
      const canvas = ensureCanvas();
      const w = Math.min(video.videoWidth, 960);
      const scale = w / video.videoWidth;
      canvas.width = Math.round(video.videoWidth * scale);
      canvas.height = Math.round(video.videoHeight * scale);
      const ctx = canvas.getContext("2d", { willReadFrequently: true });
      if (!ctx) return;
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
      rafRef.current = requestAnimationFrame(() => {
        try {
          const result: Result = reader.decodeFromCanvas(canvas);
          emit(result.getText(), String(result.getBarcodeFormat()));
        } catch {
          // NotFoundException etc. — normal when no barcode is visible
        }
      });
    }

    timerRef.current = setInterval(tick, intervalMs());

    return () => {
      if (rafRef.current) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
      canvasRef.current?.getContext("2d")?.clearRect(0, 0, 0, 0);
    };
  }, [enabled, engine, emit, videoRef]);

  // Full teardown when disabled/unmounted.
  useEffect(() => {
    if (enabled) return;
    nativeRef.current = null;
    zxingRef.current = null;
    setEngine(null);
  }, [enabled]);

  return { engine, lastHit };
}
