"use client";

/**
 * CameraWorkspace — camera viewport + overlay for Smart Counter scan mode.
 *
 * Renders the video element consumed by useCamera + useBarcodeScanner (refs
 * passed from the page) and draws the overlay: scan reticle, engine badge,
 * last-scan chip, and the per-state message layer (denied / no camera /
 * insecure / requesting / idle). All states from spec §38 are first-class.
 */

import { Camera, CameraOff, CheckCircle2, Eye, Info, Loader2, Lock, RefreshCw, ScanLine, SwitchCamera, XCircle } from "lucide-react";
import type { CameraDevice, CameraStatus } from "@/lib/use-camera";
import type { ScanHit } from "@/lib/use-barcode-scanner";
import type { VisionHealth } from "@/lib/use-vision-health";
import { cn } from "@/lib/cn";

export type CameraOverlay = {
  tone: "info" | "success" | "error" | "muted";
  title: string;
  description?: string;
};

export function CameraWorkspace({
  videoRef,
  status,
  engine,
  devices,
  activeDeviceId,
  onSwitchDevice,
  onRetry,
  lastHit,
  overlay,
  visionHealth,
}: {
  videoRef: React.RefObject<HTMLVideoElement | null>;
  status: CameraStatus;
  engine: "native" | "zxing" | null;
  devices: CameraDevice[];
  activeDeviceId: string | null;
  onSwitchDevice: (deviceId: string) => void;
  onRetry: () => void;
  lastHit: ScanHit | null;
  overlay: CameraOverlay | null;
  visionHealth: VisionHealth | null;
}) {
  const live = status === "ready";
  const visionReady = visionHealth?.recognition_ready === true;

  return (
    <div className="card overflow-hidden p-0">
      <div className="relative aspect-video w-full bg-[#0b1f3a]">
        <video
          ref={videoRef}
          className={cn("h-full w-full object-cover", !live && "opacity-0")}
          muted
          playsInline
          autoPlay
        />

        {/* Scan reticle — only while live */}
        {live ? (
          <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
            <div className="relative h-56 w-72 sm:h-64 sm:w-96">
              <span className="absolute left-0 top-0 h-8 w-8 rounded-tl-lg border-l-[3px] border-t-[3px] border-[#00baf2]" />
              <span className="absolute right-0 top-0 h-8 w-8 rounded-tr-lg border-r-[3px] border-t-[3px] border-[#00baf2]" />
              <span className="absolute bottom-0 left-0 h-8 w-8 rounded-bl-lg border-b-[3px] border-l-[3px] border-[#00baf2]" />
              <span className="absolute bottom-0 right-0 h-8 w-8 rounded-br-lg border-b-[3px] border-r-[3px] border-[#00baf2]" />
              <span className="absolute left-4 right-4 top-1/2 h-[2px] -translate-y-1/2 bg-[#00baf2]/70" />
            </div>
          </div>
        ) : null}

        {/* Engine + capability badges */}
        {live ? (
          <div className="absolute left-3 top-3 flex flex-col items-start gap-1.5">
            <div className="flex items-center gap-1.5 rounded-full bg-black/55 px-2.5 py-1 text-[11px] font-semibold text-white">
              <ScanLine className="h-3 w-3" />
              Barcode scan
              <span className="rounded-full bg-white/20 px-1.5 py-0.5 text-[10px] font-medium">
                {engine === "native" ? "native" : engine === "zxing" ? "zxing" : "…"}
              </span>
            </div>
            {/* Honest capability state (spec §77): state exactly what the
                backend can and cannot do right now. */}
            {visionHealth ? (
              visionReady ? (
                <div className="flex items-center gap-1.5 rounded-full bg-black/55 px-2.5 py-1 text-[11px] font-semibold text-white">
                  <Eye className="h-3 w-3 text-[#4ade80]" />
                  Visual recognition: on
                  <span className="rounded-full bg-white/20 px-1.5 py-0.5 text-[10px] font-medium">
                    {visionHealth.detector.name} + {visionHealth.embedder.name}
                  </span>
                </div>
              ) : (
                <div className="flex max-w-[75%] items-start gap-1.5 rounded-xl bg-black/55 px-2.5 py-1.5 text-[11px] font-medium text-white/90">
                  <Info className="mt-0.5 h-3 w-3 shrink-0" />
                  <span>
                    Visual recognition not configured on this store — barcode
                    scanning is active. Merchants can add reference images per
                    product to enable it.
                  </span>
                </div>
              )
            ) : null}
          </div>
        ) : null}

        {/* Last scan chip */}
        {live && lastHit ? (
          <div className="absolute bottom-3 left-3 flex max-w-[70%] items-center gap-1.5 rounded-full bg-black/55 px-2.5 py-1 text-[11px] font-semibold text-white">
            <CheckCircle2 className="h-3 w-3 text-[#4ade80]" />
            <span className="truncate">{lastHit.code}</span>
          </div>
        ) : null}

        {/* State overlays */}
        {status === "requesting" ? (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-[#0b1f3a]/95 text-center text-white">
            <Loader2 className="h-8 w-8 animate-spin text-[#00baf2]" />
            <p className="text-sm font-semibold">Starting camera…</p>
            <p className="max-w-xs text-xs text-white/70">
              Your browser will ask for camera permission. Allow it to scan product barcodes.
            </p>
          </div>
        ) : null}

        {status === "denied" ? (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-[#0b1f3a]/95 p-6 text-center text-white">
            <CameraOff className="h-8 w-8 text-[#f87171]" />
            <p className="text-sm font-semibold">Camera access denied</p>
            <p className="max-w-xs text-xs text-white/70">
              Camera permission was blocked for this site. Enable it in your browser's address-bar
              settings, then try again. Barcode and search still work below.
            </p>
            <button type="button" className="btn btn-secondary mt-1" onClick={onRetry}>
              <RefreshCw className="h-4 w-4" />
              Try again
            </button>
          </div>
        ) : null}

        {status === "not_found" ? (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-[#0b1f3a]/95 p-6 text-center text-white">
            <CameraOff className="h-8 w-8 text-[#fbbf24]" />
            <p className="text-sm font-semibold">No camera found</p>
            <p className="max-w-xs text-xs text-white/70">
              We couldn't find a camera on this device. Use the barcode input or search below.
            </p>
            <button type="button" className="btn btn-secondary mt-1" onClick={onRetry}>
              <RefreshCw className="h-4 w-4" />
              Try again
            </button>
          </div>
        ) : null}

        {status === "unavailable" ? (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-[#0b1f3a]/95 p-6 text-center text-white">
            <CameraOff className="h-8 w-8 text-[#fbbf24]" />
            <p className="text-sm font-semibold">Camera unavailable</p>
            <p className="max-w-xs text-xs text-white/70">
              The camera is busy or unavailable (another app may be using it). Close other camera apps
              and try again.
            </p>
            <button type="button" className="btn btn-secondary mt-1" onClick={onRetry}>
              <RefreshCw className="h-4 w-4" />
              Try again
            </button>
          </div>
        ) : null}

        {status === "insecure" ? (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-[#0b1f3a]/95 p-6 text-center text-white">
            <Lock className="h-8 w-8 text-[#fbbf24]" />
            <p className="text-sm font-semibold">Secure connection required</p>
            <p className="max-w-xs text-xs text-white/70">
              Camera access needs HTTPS (or localhost). Open this page over HTTPS to scan, or use the
              barcode input below.
            </p>
          </div>
        ) : null}

        {status === "idle" ? (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-[#0b1f3a]/95 p-6 text-center text-white">
            <Camera className="h-8 w-8 text-white/70" />
            <p className="text-sm font-semibold">Camera off</p>
            <p className="max-w-xs text-xs text-white/70">
              Switch to the Camera tab to start scanning product barcodes.
            </p>
          </div>
        ) : null}

        {/* Transient result overlay (found / not in catalog / lookup error) */}
        {live && overlay ? (
          <div
            className={cn(
              "absolute inset-x-3 top-3 flex items-start gap-2 rounded-xl border px-3 py-2 text-xs font-medium shadow-lg",
              overlay.tone === "success" && "border-[#15803d] bg-[#f0fdf4] text-[#15803d]",
              overlay.tone === "error" && "border-[#b91c1c] bg-[#fef2f2] text-[#b91c1c]",
              overlay.tone === "info" && "border-[#1d4ed8] bg-[#eff6ff] text-[#1d4ed8]",
              overlay.tone === "muted" && "border-white/20 bg-black/55 text-white"
            )}
            role="status"
          >
            {overlay.tone === "success" ? (
              <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            ) : overlay.tone === "error" ? (
              <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            ) : (
              <ScanLine className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            )}
            <div>
              <p className="font-semibold">{overlay.title}</p>
              {overlay.description ? <p className="mt-0.5 opacity-80">{overlay.description}</p> : null}
            </div>
          </div>
        ) : null}
      </div>

      {/* Controls */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border px-4 py-3">
        <div className="flex items-center gap-2">
          <span
            className={cn(
              "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-semibold",
              live ? "bg-[#f0fdf4] text-[#15803d]" : "bg-[#f8fafc] text-text-muted"
            )}
          >
            <span className={cn("h-1.5 w-1.5 rounded-full", live ? "animate-pulse bg-[#16a34a]" : "bg-text-muted")} />
            {live ? "Live — scanning" : "Camera off"}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {devices.length > 1 ? (
            <label className="flex items-center gap-1.5 text-xs text-text-secondary">
              <SwitchCamera className="h-3.5 w-3.5" />
              <select
                className="input h-8 w-auto py-0 text-xs"
                value={activeDeviceId ?? ""}
                onChange={(e) => onSwitchDevice(e.target.value)}
                aria-label="Select camera"
              >
                {devices.map((d) => (
                  <option key={d.deviceId} value={d.deviceId}>
                    {d.label}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          {live ? (
            <button type="button" className="btn btn-ghost text-xs" onClick={onRetry}>
              <RefreshCw className="h-3.5 w-3.5" />
              Restart camera
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
