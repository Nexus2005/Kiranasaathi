"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ArrowLeft, Construction, Sparkles } from "lucide-react";
import { AppShell, AuthGuard } from "@/components/shell";
import { PageHeader } from "@/components/page-header";

const copy: Record<string, { title: string; description: string }> = {
  "/ai-assistant": {
    title: "AI Assistant",
    description: "Conversational access to store tools — built after the foundation loop is verified.",
  },
  "/purchases": {
    title: "Purchases & Suppliers",
    description: "Supplier comparison and purchase orders plug into the same inventory model in a later phase.",
  },
  "/demand": {
    title: "Demand & Trends",
    description: "Forecasts will read real sales history from this database.",
  },
  "/whatsapp": {
    title: "WhatsApp & Marketing",
    description: "Consent-based campaigns arrive after customer intelligence is solid.",
  },
  "/festivals": {
    title: "Festival Calendar",
    description: "Festival → demand → inventory planning is a later phase.",
  },
  "/quick-commerce": {
    title: "Quick Commerce",
    description: "Integration adapter architecture only — no fake partner APIs in Phase 1.",
  },
  "/analytics": {
    title: "Analytics",
    description: "Historical analytics will aggregate the same sales tables used by the dashboard.",
  },
  "/reports": {
    title: "Reports",
    description: "Report exports come after enough real transaction data exists.",
  },
  "/alerts": {
    title: "Alerts",
    description: "Alerts already exist in the data model; the full alert center UI is a later phase. Open alerts show on Home.",
  },
  "/settings": {
    title: "Settings",
    description: "Store profile and preferences are planned after Phase 1 vertical slice sign-off.",
  },
  "/help": {
    title: "Help & Support",
    description: "Help center content is not part of Phase 1.",
  },
};

export default function FutureModulePage() {
  return (
    <AuthGuard>
      <FutureScreen />
    </AuthGuard>
  );
}

function FutureScreen() {
  const path = usePathname() || "/ai-assistant";
  const info = copy[path] || {
    title: "Coming in a later phase",
    description: "This module is intentionally not implemented yet.",
  };

  return (
    <AppShell>
      <div className="space-y-5">
        <PageHeader title={info.title} description={info.description} icon={Construction} />
        <div className="card max-w-2xl p-6">
          <div className="flex items-start gap-3">
            <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-[#e8f2ff] text-primary">
              <Sparkles className="h-5 w-5" />
            </span>
            <div>
              <p className="font-semibold">Future module — not a working workflow yet</p>
              <p className="mt-1 text-sm text-text-secondary">
                Phase 1 only ships navigation honesty: no fake charts, no fake AI answers, and no dead
                primary actions. This page will be replaced by the real module.
              </p>
            </div>
          </div>
          <div className="mt-5 flex flex-wrap gap-2">
            <Link href="/" className="btn btn-primary">
              <ArrowLeft className="h-4 w-4" />
              Back to Home
            </Link>
            <Link href="/inventory" className="btn btn-secondary">
              Open Inventory
            </Link>
            <Link href="/smart-counter" className="btn btn-secondary">
              Open Smart Counter
            </Link>
          </div>
        </div>
      </div>
    </AppShell>
  );
}
