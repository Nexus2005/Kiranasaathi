"use client";

import { EmptyState } from "@/components/states";
import { formatINR } from "@/lib/types";

export type SeriesPoint = { label: string; value: number; secondary?: number };

/** Vertical bar chart — values come straight from API data; empty state when flat. */
export function DailyBarChart({
  points,
  valueFormatter = (v: number) => formatINR(v),
  height = 180,
}: {
  points: SeriesPoint[];
  valueFormatter?: (v: number) => string;
  height?: number;
}) {
  if (!points.length || points.every((p) => p.value === 0)) {
    return <EmptyState title="No data in this period" description="Record sales to populate this chart." />;
  }
  const max = Math.max(...points.map((p) => p.value), 1);
  return (
    <div>
      <div className="flex items-end gap-1" style={{ height }}>
        {points.map((p) => (
          <div key={p.label} className="group flex flex-1 flex-col items-center justify-end gap-1" title={`${p.label}: ${valueFormatter(p.value)}`}>
            <div
              className="w-full rounded-t bg-primary/85 transition group-hover:bg-primary"
              style={{ height: `${Math.max((p.value / max) * (height - 24), 2)}px` }}
            />
          </div>
        ))}
      </div>
      <div className="mt-1 flex gap-1 text-[10px] text-text-muted">
        {points.map((p, i) => (
          <span key={p.label} className="flex-1 text-center">
            {i % Math.ceil(points.length / 8) === 0 ? p.label : ""}
          </span>
        ))}
      </div>
    </div>
  );
}

/** Two-series grouped bar chart (e.g., recent vs previous window). */
export function DualBarChart({
  points,
  labelA,
  labelB,
  height = 160,
}: {
  points: { label: string; a: number; b: number }[];
  labelA: string;
  labelB: string;
  height?: number;
}) {
  const usable = points.filter((p) => p.a > 0 || p.b > 0);
  if (!usable.length) {
    return <EmptyState title="No comparable data" description="Both periods have no recorded sales." />;
  }
  const max = Math.max(...usable.flatMap((p) => [p.a, p.b]), 1);
  return (
    <div>
      <div className="mb-2 flex items-center gap-4 text-[11px] text-text-secondary">
        <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm bg-primary" />{labelA}</span>
        <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm bg-[#94a3b8]" />{labelB}</span>
      </div>
      <div className="flex items-end gap-3" style={{ height }}>
        {usable.map((p) => (
          <div key={p.label} className="flex flex-1 flex-col items-center justify-end gap-1">
            <div className="flex w-full items-end justify-center gap-0.5" style={{ height: height - 22 }}>
              <div className="w-1/2 rounded-t bg-primary/85" style={{ height: `${Math.max((p.a / max) * (height - 24), 2)}px` }} title={`${labelA}: ${p.a}`} />
              <div className="w-1/2 rounded-t bg-[#94a3b8]" style={{ height: `${Math.max((p.b / max) * (height - 24), 2)}px` }} title={`${labelB}: ${p.b}`} />
            </div>
            <span className="text-[10px] text-text-muted">{p.label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/** Horizontal progress-style list used for category trends. */
export function TrendListBars({
  items,
}: {
  items: { label: string; value: number; caption?: string; deltaPct?: number | null }[];
}) {
  if (!items.length) {
    return <EmptyState title="No category sales yet" description="Record sales to see category trends." />;
  }
  const max = Math.max(...items.map((i) => i.value), 1);
  return (
    <ul className="space-y-3">
      {items.map((i) => (
        <li key={i.label}>
          <div className="mb-1 flex items-baseline justify-between gap-2 text-sm">
            <span className="truncate font-medium">{i.label}</span>
            <span className="flex items-center gap-2 shrink-0">
              {typeof i.deltaPct === "number" ? (
                <span className={i.deltaPct >= 0 ? "text-success text-xs font-semibold" : "text-danger text-xs font-semibold"}>
                  {i.deltaPct >= 0 ? "+" : ""}{i.deltaPct}%
                </span>
              ) : null}
              <span className="font-semibold">{formatINR(i.value)}</span>
            </span>
          </div>
          <div className="h-2 rounded-full bg-[#f1f5f9]">
            <div className="h-2 rounded-full bg-primary" style={{ width: `${(i.value / max) * 100}%` }} />
          </div>
          {i.caption ? <p className="mt-1 text-[11px] text-text-muted">{i.caption}</p> : null}
        </li>
      ))}
    </ul>
  );
}
