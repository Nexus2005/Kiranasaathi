"use client";

/**
 * useVisionHealth — probes GET /api/counter/health (authenticated).
 *
 * The UI uses this to be HONEST about capability (spec §77):
 *   - recognition_ready=false → "Visual recognition not configured —
 *     barcode scanning active" badge in the camera workspace.
 *   - recognition_ready=true  → shows which providers back the pipeline.
 * Never pretends visual recognition exists when providers are absent.
 */

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";

export type VisionProviderInfo = { name: string; available: boolean; detail: string };

export type VisionHealth = {
  detector: VisionProviderInfo;
  barcode: VisionProviderInfo;
  embedder: VisionProviderInfo;
  ocr: VisionProviderInfo;
  pgvector: boolean;
  recognition_ready: boolean;
  allow_mock: boolean;
  thresholds: { auto_add: number; review: number };
};

export function useVisionHealth(enabled: boolean) {
  const [health, setHealth] = useState<VisionHealth | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!enabled) return;
    setError(null);
    try {
      const h = await api<VisionHealth>("/api/counter/health");
      setHealth(h);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Vision health unavailable");
    }
  }, [enabled]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { health, error, refresh };
}
