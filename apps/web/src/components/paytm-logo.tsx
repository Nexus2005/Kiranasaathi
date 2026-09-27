"use client";

/**
 * PaytmLogo — the official standalone Paytm wordmark (public/brand/paytm-logo.svg),
 * replacing the previous hand-typed "Pay/tm" colored text. Height-driven so it
 * scales cleanly next to the KiranaSaathi AI lockup in the shell and login.
 */
export function PaytmLogo({ className }: { className?: string }) {
  return (
    <img
      src="/brand/paytm-logo.svg"
      alt="Paytm"
      className={className ?? "h-6 w-auto"}
    />
  );
}
