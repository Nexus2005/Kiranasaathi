"use client";

/**
 * useVisionRecognize — server-side visual recognition loop for Smart Counter.
 *
 * While the camera is live AND the backend reports recognition_ready, this
 * hook periodically captures a downscaled JPEG frame from the video element
 * and sends it to POST /api/counter/recognize (multipart). The server does
 * detection → crop → barcode/embedding retrieval → match → confidence and
 * returns per-detection identity candidates. Nothing is invented client-side:
 * the UI renders exactly what the backend reports, and a candidate NEVER
 * becomes a bill line until the merchant confirms it (spec §16).
 *
 * Cadence + dedupe mirror useBarcodeScanner: fixed interval, repeat lockout
 * per signature (product_id|status|bbox), paused while a request is in
 * flight or the caller reports busy (e.g. barcode lookup running).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { apiForm } from "@/lib/api";
import type { VisionHealth } from "@/lib/use-vision-health";

export type VisionEvidence = {
  visual_similarity?: number | null;
  ocr_similarity?: number | null;
  brand_match?: boolean | null;
  pack_size_match?: boolean | null;
  barcode_match?: boolean | null;
};

export type VisionMatch = {
  product_id: string | null;
  confidence: number;
  method: "BARCODE" | "VISUAL" | "OCR" | "MANUAL" | "COMBINED";
  reasons: string[];
  alternatives: { product_id: string; similarity: number; rank: number }[];
  barcode: string | null;
  evidence?: VisionEvidence | null;
};

export type VisionDetectionResult = {
  detection: {
    detection_id: string;
    bbox: { x: number; y: number; width: number; height: number };
    confidence: number;
    class_name: string;
  };
  match: VisionMatch;
  status: "IDENTIFIED" | "REVIEW_REQUIRED" | "UNRESOLVED" | string;
  auto_addable: boolean;
  requires_review: boolean;
  latency_ms: number;
  ocr_mrp_evidence?: number | null;
  recognition_event_id?: string | null;
};

export type FrameRecognitionResponse = {
  frame_id: string;
  timestamp: number;
  detections: VisionDetectionResult[];
  skipped_stages: Record<string, string>;
  timings_ms?: Record<string, number>;
  latency_ms: number;
};

export type VisionHit = {
  /** Key for dedupe: product+status when identified, else bbox+status. */
  signature: string;
  status: VisionDetectionResult["status"];
  match: VisionMatch;
  detection: VisionDetectionResult["detection"];
  latency_ms: number;
  /** recognition_events row — merchant feedback updates THIS row. */
  eventId?: string | null;
  at: number;
};

const DEFAULT_INTERVAL_MS = 1500;
const DEFAULT_REPEAT_LOCKOUT_MS = 6000;

function intervalMs(): number {
  const raw = Number(process.env.NEXT_PUBLIC_COUNTER_VISION_INTERVAL_MS);
  return Number.isFinite(raw) && raw >= 700 ? raw : DEFAULT_INTERVAL_MS;
}

function lockoutMs(): number {
  const raw = Number(process.env.NEXT_PUBLIC_COUNTER_VISION_LOCKOUT_MS);
  return Number.isFinite(raw) && raw >= 1500 ? raw : DEFAULT_REPEAT_LOCKOUT_MS;
}

export function useVisionRecognize(
  videoRef: React.RefObject<HTMLVideoElement | null>,
  enabled: boolean,
  health: VisionHealth | null,
  isBusy: () => boolean,
  onHit: (hit: VisionHit) => void
) {
  const [recognizing, setRecognizing] = useState(false);
  const [lastFrame, setLastFrame] = useState<FrameRecognitionResponse | null>(null);
  const [frameError, setFrameError] = useState<string | null>(null);

  const onHitRef = useRef(onHit);
  onHitRef.current = onHit;
  const isBusyRef = useRef(isBusy);
  isBusyRef.current = isBusy;

  const busyRef = useRef(false);
  const lastSignatureRef = useRef<string | null>(null);
  const lastAtRef = useRef(0);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const recognize = useCallback(async () => {
    if (busyRef.current || isBusyRef.current()) return;
    const video = videoRef.current;
    if (!video || video.readyState < 2 || video.videoWidth === 0) return;

    busyRef.current = true;
    setRecognizing(true);
    try {
      const w = Math.min(video.videoWidth, 960);
      const scale = w / video.videoWidth;
      const canvas = canvasRef.current ?? document.createElement("canvas");
      canvasRef.current = canvas;
      canvas.width = Math.round(video.videoWidth * scale);
      canvas.height = Math.round(video.videoHeight * scale);
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height);

      const blob: Blob | null = await new Promise((resolve) =>
        canvas.toBlob((b) => resolve(b), "image/jpeg", 0.85)
      );
      if (!blob) return;

      const form = new FormData();
      form.append("frame", blob, "frame.jpg");
      const res = await apiForm<FrameRecognitionResponse>("/api/counter/recognize", form, {
        method: "POST",
      });
      setLastFrame(res);
      setFrameError(null);

      for (const det of res.detections) {
        const bboxKey = `${Math.round(det.detection.bbox.x)}:${Math.round(det.detection.bbox.y)}`;
        const signature = `${det.match.product_id ?? bboxKey}|${det.status}`;
        const now = Date.now();
        if (
          lastSignatureRef.current === signature &&
          now - lastAtRef.current < lockoutMs()
        ) {
          continue; // same physical product still held in front of the camera
        }
        lastSignatureRef.current = signature;
        lastAtRef.current = now;
        onHitRef.current({
          signature,
          status: det.status,
          match: det.match,
          detection: det.detection,
          latency_ms: det.latency_ms,
          eventId: det.recognition_event_id ?? null,
          at: now,
        });
      }
    } catch (err) {
      // 503 VISION_NOT_CONFIGURED etc. — surface once, keep scanning honestly
      setFrameError(err instanceof Error ? err.message : "Recognition failed");
    } finally {
      busyRef.current = false;
      setRecognizing(false);
    }
  }, [videoRef]);

  // Poll only while the camera is live AND the backend says recognition is on.
  const active = enabled && health?.recognition_ready === true;
  useEffect(() => {
    if (!active) {
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
      return;
    }
    timerRef.current = setInterval(() => void recognize(), intervalMs());
    return () => {
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [active, recognize]);

  useEffect(() => {
    if (!enabled) {
      setLastFrame(null);
      setFrameError(null);
      lastSignatureRef.current = null;
      lastAtRef.current = 0;
    }
  }, [enabled]);

  return { recognizing, lastFrame, frameError };
}
