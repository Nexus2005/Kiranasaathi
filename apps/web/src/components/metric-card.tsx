import type { LucideIcon } from "lucide-react";
import { TrendingDown, TrendingUp } from "lucide-react";
import { cn } from "@/lib/cn";

type Props = {
  icon: LucideIcon;
  label: string;
  value: string;
  delta?: number | null;
  deltaLabel?: string;
  hint?: string;
  badge?: { label: string; tone: string };
  tone?: "blue" | "green" | "orange" | "red" | "purple" | "cyan";
  className?: string;
};

const tones = {
  blue: "bg-[#e8f2ff] text-[#2080f0]",
  green: "bg-[#dcfce7] text-[#16a34a]",
  orange: "bg-[#ffedd5] text-[#f97316]",
  red: "bg-[#fee2e2] text-[#dc2626]",
  purple: "bg-[#ede9fe] text-[#8b5cf6]",
  cyan: "bg-[#cffafe] text-[#0891b2]",
};

export function MetricCard({
  icon: Icon,
  label,
  value,
  delta,
  deltaLabel,
  hint,
  badge,
  tone = "blue",
  className,
}: Props) {
  return (
    <div className={cn("card flex flex-col gap-2 p-4", className)}>
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-3 min-w-0">
          <span className={cn("flex h-10 w-10 shrink-0 items-center justify-center rounded-full", tones[tone])}>
            <Icon className="h-5 w-5" />
          </span>
          <span className="truncate text-[12.5px] font-medium text-text-secondary">{label}</span>
        </div>
        {badge ? (
          <span
            className={cn(
              "rounded-full px-2 py-0.5 text-[10px] font-semibold",
              badge.tone === "danger"
                ? "bg-[#fee2e2] text-[#b91c1c]"
                : "bg-[#e8f2ff] text-[#1d4ed8]"
            )}
          >
            {badge.label}
          </span>
        ) : null}
      </div>
      <div className="text-[26px] font-bold leading-none text-foreground">{value}</div>
      <div className="flex flex-wrap items-center gap-2 text-[12px]">
        {typeof delta === "number" ? (
          <span
            className={cn(
              "inline-flex items-center gap-1 font-semibold",
              delta >= 0 ? "text-success" : "text-danger"
            )}
          >
            {delta >= 0 ? <TrendingUp className="h-3.5 w-3.5" /> : <TrendingDown className="h-3.5 w-3.5" />}
            {delta >= 0 ? "+" : ""}
            {delta}%
          </span>
        ) : null}
        {deltaLabel ? <span className="text-text-muted">{deltaLabel}</span> : null}
        {hint ? <span className="text-text-muted">{hint}</span> : null}
      </div>
    </div>
  );
}
