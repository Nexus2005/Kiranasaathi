"use client";

/**
 * ProductImage — product photo with graceful fallback.
 *
 * Sources (priority): merchant upload (/media/products/…) → Open Food Facts
 * image (attribution recorded server-side) → neutral placeholder. The
 * placeholder is honest: "no image" also means "cannot be visually
 * recognized yet" (see /catalog-admin).
 */
import { useState } from "react";
import { Package } from "lucide-react";
import { cn } from "@/lib/cn";

export function ProductImage({
  src,
  alt,
  className,
}: {
  src?: string | null;
  alt: string;
  className?: string;
}) {
  const [failed, setFailed] = useState(false);
  const show = src && !failed;
  return show ? (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={src}
      alt={alt}
      className={cn("object-contain", className)}
      onError={() => setFailed(true)}
      loading="lazy"
    />
  ) : (
    <div
      className={cn(
        "flex items-center justify-center bg-[#f1f5f9] text-text-muted",
        className
      )}
      aria-label={`${alt} — no image`}
    >
      <Package className="h-1/2 w-1/2 opacity-60" />
    </div>
  );
}
