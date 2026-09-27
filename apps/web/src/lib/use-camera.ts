"use client";

/**
 * useCamera — camera lifecycle for the Smart Counter scan mode.
 *
 * States (each mapped to first-class UI in CameraWorkspace):
 *   idle | requesting | ready | denied | not_found | unavailable | insecure
 *
 * Rules:
 * - Requests `facingMode: environment` (rear camera) where available.
 * - Never leaks: every acquired stream is stopped on unmount, on stop(), and
 *   before switching devices.
 * - The [enabled] effect is the ONLY lifecycle trigger; device switches are an
 *   explicit action (switchDevice) that starts the new device and falls back
 *   to the previous working one if the switch fails. This avoids the
 *   start→setDeviceId→effect-restart→failure loop that a device-id effect
 *   dependency would create (observed in real browsers: an immediate
 *   re-open of the same device can transiently fail with NotFoundError).
 * - Frames stay local. Nothing here uploads or processes anything — the
 *   video element is consumed by useBarcodeScanner.
 * - Secure context (https/localhost) is required by getUserMedia; surfaced
 *   as a distinct state instead of a generic error.
 */

import { useCallback, useEffect, useRef, useState } from "react";

export type CameraStatus =
  | "idle"
  | "requesting"
  | "ready"
  | "denied"
  | "not_found"
  | "unavailable"
  | "insecure";

export type CameraDevice = { deviceId: string; label: string };

function isSecure(): boolean {
  if (typeof window === "undefined") return false;
  // localhost counts as secure even over http
  return window.isSecureContext || window.location.hostname === "localhost";
}

export function useCamera(enabled: boolean) {
  const [status, setStatus] = useState<CameraStatus>("idle");
  const [devices, setDevices] = useState<CameraDevice[]>([]);
  const [activeDeviceId, setActiveDeviceId] = useState<string | null>(null);

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  // Device the CURRENT stream is actually running on (source of truth for
  // "is a restart needed?" and for fallback on switch failure).
  const streamDeviceIdRef = useRef<string | null>(null);
  const mountedRef = useRef(true);

  const stopStream = useCallback(() => {
    const stream = streamRef.current;
    if (stream) {
      for (const track of stream.getTracks()) track.stop();
      streamRef.current = null;
    }
    streamDeviceIdRef.current = null;
    const video = videoRef.current;
    if (video) {
      video.srcObject = null;
    }
  }, []);

  const start = useCallback(
    async (deviceId?: string): Promise<boolean> => {
      if (!mountedRef.current) return false;
      if (!isSecure()) {
        setStatus("insecure");
        return false;
      }
      if (!navigator.mediaDevices?.getUserMedia) {
        setStatus("unavailable");
        return false;
      }
      // No-op if already live on the requested device (prevents re-open races).
      if (
        streamRef.current &&
        (deviceId ?? null) === streamDeviceIdRef.current
      ) {
        return true;
      }
      setStatus("requesting");
      stopStream();
      try {
        const constraints: MediaStreamConstraints = {
          audio: false,
          video: deviceId
            ? { deviceId: { exact: deviceId } }
            : {
                facingMode: { ideal: "environment" },
                width: { ideal: 1280 },
                height: { ideal: 720 },
              },
        };
        const stream = await navigator.mediaDevices.getUserMedia(constraints);
        if (!mountedRef.current) {
          // Component unmounted while the permission prompt was open.
          for (const track of stream.getTracks()) track.stop();
          return false;
        }
        streamRef.current = stream;

        const video = videoRef.current;
        if (video) {
          video.srcObject = stream;
          try {
            await video.play();
          } catch {
            // Autoplay can reject until user gesture; the video element is
            // muted+playsInline so this rarely blocks scanning.
          }
        }

        const current = stream.getVideoTracks()[0];
        const currentId = current?.getSettings().deviceId ?? null;
        streamDeviceIdRef.current = currentId ?? null;

        // Enumerate cameras now that permission is granted (labels available).
        try {
          const all = await navigator.mediaDevices.enumerateDevices();
          if (mountedRef.current) {
            const cams = all
              .filter((d) => d.kind === "videoinput")
              .map((d) => ({
                deviceId: d.deviceId,
                label: d.label || `Camera ${d.deviceId.slice(0, 6)}`,
              }));
            setDevices(cams);
          }
        } catch {
          // Enumeration is best-effort; scanning works without the list.
        }

        setActiveDeviceId(streamDeviceIdRef.current);
        setStatus("ready");
        return true;
      } catch (err) {
        if (!mountedRef.current) return false;
        const name = err instanceof DOMException ? err.name : "";
        // A switch to a specific device failed — if we were previously live
        // on another device, fall back to it instead of dying in an error
        // state (covers cameras that vanish between enumeration and open).
        if (
          deviceId &&
          streamDeviceIdRef.current &&
          deviceId !== streamDeviceIdRef.current
        ) {
          const fallback = await start(streamDeviceIdRef.current);
          if (fallback) return true;
        }
        if (name === "NotAllowedError" || name === "SecurityError") {
          setStatus("denied");
        } else if (name === "NotFoundError" || name === "OverconstrainedError") {
          setStatus("not_found");
        } else if (name === "NotReadableError" || name === "AbortError") {
          setStatus("unavailable");
        } else {
          setStatus("unavailable");
        }
        return false;
      }
    },
    [stopStream]
  );

  // Lifecycle: start/stop only with `enabled`. Device changes go through
  // switchDevice() — deliberately NOT a dependency here.
  useEffect(() => {
    mountedRef.current = true;
    if (enabled) {
      void start(streamDeviceIdRef.current ?? undefined);
    } else {
      stopStream();
      setActiveDeviceId(null);
      setStatus("idle");
    }
    return () => {
      mountedRef.current = false;
      stopStream();
    };
    // start/stopStream are stable useCallbacks; including them would re-run
    // this effect and re-open the device (the exact race this design avoids).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled]);

  // Privacy/battery hygiene: restart the same device when the tab becomes
  // visible again (browsers suspend hidden-tab capture).
  useEffect(() => {
    if (!enabled) return;
    function onVisibility() {
      if (
        document.visibilityState === "visible" &&
        !streamRef.current &&
        mountedRef.current
      ) {
        void start(streamDeviceIdRef.current ?? undefined);
      }
    }
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled]);

  const stop = useCallback(() => {
    stopStream();
    setActiveDeviceId(null);
    setStatus("idle");
  }, [stopStream]);

  /** Retry after a failure (denied/not_found/unavailable) — re-prompts permission. */
  const restart = useCallback(() => {
    void start(streamDeviceIdRef.current ?? undefined);
  }, [start]);

  /** Explicit device switch: start the new device; start() falls back to the
   *  previous working device if the switch fails. */
  const switchDevice = useCallback(
    (deviceId: string) => {
      if (deviceId && deviceId !== streamDeviceIdRef.current) {
        setActiveDeviceId(deviceId);
        void start(deviceId);
      }
    },
    [start]
  );

  return { videoRef, status, devices, activeDeviceId, switchDevice, stop, restart };
}
