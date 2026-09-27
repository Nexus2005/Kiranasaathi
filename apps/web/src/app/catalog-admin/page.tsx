"use client";

/**
 * CatalogAdminPage — product-catalog administration (§11).
 *
 * One screen answering: which products can the camera actually recognize?
 * Recognition status derives from REAL state (embeddings, global link,
 * correction history) — never claimed. Lists: recognition readiness,
 * missing images, missing barcodes, needs-review (merchant corrections).
 */

import { useCallback, useEffect, useState } from "react";
import { Boxes, Camera, ImageOff, ScanBarcode, TriangleAlert } from "lucide-react";
import { AppShell, AuthGuard } from "@/components/shell";
import { PageHeader } from "@/components/page-header";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { StatusBadge, statusTone } from "@/components/status-badge";
import { api } from "@/lib/api";
import { formatINR, type Product } from "@/lib/types";
import { cn } from "@/lib/cn";

type CatalogItem = {
  id: string;
  name: string;
  category: string;
  barcode: string | null;
  selling_price: number;
  is_active: boolean;
  quantity: number;
  image_count: number;
  embedding_count: number;
  global_linked: boolean;
  correction_count: number;
  recognition_status: "NOT_ENROLLED" | "ENROLLED" | "VERIFIED" | "NEEDS_REVIEW";
  missing_image: boolean;
  missing_barcode: boolean;
};

type CatalogResponse = {
  items: CatalogItem[];
  summary: {
    total: number;
    not_enrolled: number;
    enrolled: number;
    verified: number;
    needs_review: number;
    missing_images: number;
    missing_barcodes: number;
  };
};

const STATUS_TONE: Record<CatalogItem["recognition_status"], "green" | "amber" | "red" | "blue" | "gray"> = {
  VERIFIED: "green",
  ENROLLED: "blue",
  NOT_ENROLLED: "gray",
  NEEDS_REVIEW: "amber",
};

type SectionFilter = "all" | "recognition" | "missing_images" | "missing_barcodes" | "needs_review";

const SECTIONS: [SectionFilter, string][] = [
  ["all", "All products"],
  ["recognition", "Recognition ready"],
  ["missing_images", "Missing images"],
  ["missing_barcodes", "Missing barcodes"],
  ["needs_review", "Needs review"],
];

function matches(item: CatalogItem, filter: SectionFilter): boolean {
  switch (filter) {
    case "recognition":
      return item.recognition_status === "ENROLLED" || item.recognition_status === "VERIFIED";
    case "missing_images":
      return item.missing_image;
    case "missing_barcodes":
      return item.missing_barcode;
    case "needs_review":
      return item.recognition_status === "NEEDS_REVIEW";
    default:
      return true;
  }
}

export default function CatalogAdminPage() {
  return (
    <AuthGuard>
      <CatalogAdminScreen />
    </AuthGuard>
  );
}

function CatalogAdminScreen() {
  const [data, setData] = useState<CatalogResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [section, setSection] = useState<SectionFilter>("all");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await api<CatalogResponse>("/api/retail/admin/product-catalog"));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load catalog");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const s = data?.summary;
  const items = (data?.items ?? []).filter((i) => matches(i, section));

  return (
    <AppShell>
      <div className="space-y-5">
        <PageHeader
          title="Product catalog"
          description="What the camera can recognize today — and what's missing before it can."
          icon={Boxes}
        />

        {s ? (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <SummaryCard icon={Camera} label="Recognition ready" value={`${s.enrolled + s.verified}`} hint={`${s.verified} globally verified`} tone="green" />
            <SummaryCard icon={ScanBarcode} label="Missing barcodes" value={`${s.missing_barcodes}`} hint="Exact identity unavailable" tone="orange" />
            <SummaryCard icon={ImageOff} label="Missing images" value={`${s.missing_images}`} hint="Cannot be visually recognized" tone="red" />
            <SummaryCard icon={TriangleAlert} label="Needs review" value={`${s.needs_review}`} hint="Merchant corrections on record" tone="purple" />
          </div>
        ) : null}

        <div className="flex flex-wrap items-center gap-2">
          {SECTIONS.map(([key, label]) => (
            <button
              key={key}
              type="button"
              className={cn(
                "rounded-xl border px-3 py-1.5 text-xs font-semibold transition",
                section === key
                  ? "border-primary bg-[#eff6ff] text-primary"
                  : "border-border bg-white text-text-secondary hover:bg-[#f8fafc]"
              )}
              onClick={() => setSection(key)}
            >
              {label}
            </button>
          ))}
        </div>

        {loading ? <LoadingState /> : null}
        {error && !loading ? <ErrorState message={error} onRetry={() => void load()} /> : null}

        {!loading && !error ? (
          items.length === 0 ? (
            <EmptyState title="Nothing here" description="No products match this section." />
          ) : (
            <div className="card overflow-x-auto p-0">
              <table className="w-full text-left text-xs">
                <thead className="border-b border-border text-[11px] uppercase tracking-wide text-text-muted">
                  <tr>
                    <th className="px-4 py-2.5">Product</th>
                    <th className="px-4 py-2.5">Recognition</th>
                    <th className="px-4 py-2.5">Images</th>
                    <th className="px-4 py-2.5">Barcode</th>
                    <th className="px-4 py-2.5">Price</th>
                    <th className="px-4 py-2.5">Stock</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {items.map((it) => (
                    <tr key={it.id} className="hover:bg-[#f8fafc]">
                      <td className="px-4 py-2.5">
                        <p className="font-semibold">{it.name}</p>
                        <p className="text-[11px] text-text-muted">{it.category}</p>
                      </td>
                      <td className="px-4 py-2.5">
                        <StatusBadge label={it.recognition_status.replace("_", " ").toLowerCase()} tone={STATUS_TONE[it.recognition_status] ?? "gray"} />
                        {it.global_linked ? (
                          <span className="ml-1.5 text-[10px] text-text-muted">global ✓</span>
                        ) : null}
                      </td>
                      <td className="px-4 py-2.5">
                        {it.image_count > 0 ? (
                          <span>{it.image_count} ref{it.image_count > 1 ? "s" : ""}</span>
                        ) : (
                          <span className="text-red-600">none</span>
                        )}
                      </td>
                      <td className="px-4 py-2.5 font-mono text-[11px]">
                        {it.barcode ?? <span className="text-amber-600">—</span>}
                      </td>
                      <td className="px-4 py-2.5">{formatINR(it.selling_price)}</td>
                      <td className="px-4 py-2.5">{it.quantity}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        ) : null}
      </div>
    </AppShell>
  );
}

function SummaryCard({
  icon: Icon,
  label,
  value,
  hint,
  tone,
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  value: string;
  hint: string;
  tone: "green" | "orange" | "red" | "purple";
}) {
  const tones = {
    green: "bg-emerald-50 text-emerald-700",
    orange: "bg-amber-50 text-amber-700",
    red: "bg-red-50 text-red-700",
    purple: "bg-purple-50 text-purple-700",
  } as const;
  return (
    <div className="card flex items-center gap-3 p-4">
      <span className={cn("flex h-9 w-9 items-center justify-center rounded-xl", tones[tone])}>
        <Icon className="h-4.5 w-4.5" />
      </span>
      <div>
        <p className="text-lg font-bold leading-tight">{value}</p>
        <p className="text-[11px] font-medium text-text-secondary">{label}</p>
        <p className="text-[10px] text-text-muted">{hint}</p>
      </div>
    </div>
  );
}
