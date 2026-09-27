"use client";

/**
 * BarcodeScannerPanel — reusable camera barcode scanning viewport.
 *
 * Reuses the EXISTING scan stack (directive: no second barcode implementation):
 *   useCamera (lifecycle/permissions/device switching) +
 *   useBarcodeScanner (BarcodeDetector native → zxing fallback).
 * Used by Inventory "Add product → Scan barcode" and Smart Counter's
 * camera-first Barcode mode. Calls onScan(code) once per detected code;
 * repeat-detection lockout is handled inside useBarcodeScanner.
 */

import { useCallback, useState } from "react";
import { ScanLine } from "lucide-react";
import { useBarcodeScanner, type ScanHit } from "@/lib/use-barcode-scanner";
import { useCamera } from "@/lib/use-camera";
import { cn } from "@/lib/cn";

export function BarcodeScannerPanel({
  onScan,
  busy,
  compact,
}: {
  onScan: (code: string) => void;
  busy: boolean;
  compact?: boolean;
}) {
  const [overlay, setOverlay] = useState<{ tone: "info" | "success" | "error" | "muted"; text: string } | null>(null);
  const cameraEnabled = true;
  const {
    videoRef,
    status: cameraStatus,
    devices,
    activeDeviceId,
    switchDevice,
    restart,
  } = useCamera(cameraEnabled);

  const onHit = useCallback(
    (hit: ScanHit) => {
      setOverlay({ tone: "success", text: `Detected ${hit.code}` });
      onScan(hit.code);
    },
    [onScan]
  );

  const { engine } = useBarcodeScanner(
    videoRef,
    cameraStatus === "ready",
    onHit,
    useCallback(() => busy, [busy])
  );

  return (
    <div className="space-y-2">
      <div className={cn("card relative overflow-hidden bg-[#0b1f3a] p-0", compact ? "aspect-[4/3]" : "aspect-video")}>
        <video
          ref={videoRef}
          className={cn("h-full w-full object-cover", cameraStatus !== "ready" && "opacity-0")}
          muted
          playsInline
          autoPlay
        />
        {/* reticle */}
        {cameraStatus === "ready" ? (
          <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
            <div className="relative h-32 w-56">
              <span className="absolute left-0 top-0 h-6 w-6 rounded-tl-lg border-l-[3px] border-t-[3px] border-[#00baf2]" />
              <span className="absolute right-0 top-0 h-6 w-6 rounded-tr-lg border-r-[3px] border-t-[3px] border-[#00baf2]" />
              <span className="absolute bottom-0 left-0 h-6 w-6 rounded-bl-lg border-b-[3px] border-l-[3px] border-[#00baf2]" />
              <span className="absolute bottom-0 right-0 h-6 w-6 rounded-br-lg border-b-[3px] border-r-[3px] border-[#00baf2]" />
              <span className="absolute left-3 right-3 top-1/2 h-[2px] -translate-y-1/2 bg-[#00baf2]/70" />
            </div>
          </div>
        ) : null}
        {/* status layer */}
        {cameraStatus !== "ready" ? (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 p-4 text-center text-white/90">
            {cameraStatus === "denied" || cameraStatus === "unavailable" || cameraStatus === "not_found" ? (
              <>
                <p className="text-sm font-semibold">Camera unavailable</p>
                <button type="button" className="btn btn-secondary" onClick={restart}>
                  Try again
                </button>
              </>
            ) : cameraStatus === "insecure" ? (
              <p className="text-sm">Camera requires HTTPS (or localhost).</p>
            ) : (
              <p className="flex items-center gap-2 text-sm">
                <ScanLine className="h-4 w-4 animate-pulse" /> Starting camera…
              </p>
            )}
          </div>
        ) : null}
        {/* engine + hit badges */}
        {cameraStatus === "ready" ? (
          <div className="absolute left-3 top-3 flex items-center gap-2">
            <span className="rounded-full bg-black/50 px-2 py-0.5 text-[10px] font-medium text-white/80">
              {engine === "native" ? "native detector" : engine === "zxing" ? "zxing" : "…"}
            </span>
          </div>
        ) : null}
        {overlay ? (
          <div
            className={cn(
              "absolute bottom-3 left-3 right-3 rounded-xl px-3 py-2 text-xs font-medium text-white",
              overlay.tone === "success" && "bg-emerald-600/90",
              overlay.tone === "error" && "bg-red-600/90",
              overlay.tone === "info" && "bg-sky-600/90",
              overlay.tone === "muted" && "bg-black/60"
            )}
          >
            {overlay.text}
          </div>
        ) : null}
      </div>

      {devices.length > 1 ? (
        <div className="flex items-center gap-2">
          <select
            className="input flex-1 text-xs"
            value={activeDeviceId ?? ""}
            onChange={(e) => switchDevice(e.target.value)}
            aria-label="Camera device"
          >
            {devices.map((d) => (
              <option key={d.deviceId} value={d.deviceId}>
                {d.label || "Camera"}
              </option>
            ))}
          </select>
        </div>
      ) : null}
    </div>
  );
}
