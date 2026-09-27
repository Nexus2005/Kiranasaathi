import { cn } from "@/lib/cn";

const tones: Record<string, string> = {
  success: "bg-[#dcfce7] text-[#15803d]",
  danger: "bg-[#fee2e2] text-[#b91c1c]",
  warning: "bg-[#ffedd5] text-[#c2410c]",
  info: "bg-[#e8f2ff] text-[#1d4ed8]",
  neutral: "bg-[#f1f5f9] text-[#475569]",
  gold: "bg-[#fef9c3] text-[#a16207]",
  purple: "bg-[#ede9fe] text-[#6d28d9]",
};

export function StatusBadge({
  label,
  tone = "neutral",
  className,
}: {
  label: string;
  tone?: keyof typeof tones | string;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2.5 py-0.5 text-[11px] font-semibold",
        tones[tone] || tones.neutral,
        className
      )}
    >
      {label}
    </span>
  );
}

export function statusTone(status: string): string {
  const s = status.toLowerCase();
  if (["in_stock", "completed", "paid", "active", "delivered", "operational", "refunded"].includes(s))
    return "success";
  if (["low_stock", "pending", "pending_payment", "partially_paid", "processing", "ready", "warning", "expiring_soon", "refund_pending"].includes(s))
    return "warning";
  if (["out_of_stock", "cancelled", "critical", "failed", "overdue", "near_expiry", "payment_failed", "expired"].includes(s))
    return "danger";
  if (["info", "new", "reviewed", "draft"].includes(s)) return "info";
  return "neutral";
}
