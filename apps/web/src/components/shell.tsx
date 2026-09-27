"use client";

import { useEffect } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  BarChart3,
  Bell,
  Bot,
  Boxes,
  CalendarDays,
  ClipboardList,
  FileText,
  Globe,
  HelpCircle,
  Home,
  LayoutDashboard,
  LogOut,
  Megaphone,
  Package,
  PackageOpen,
  ScanBarcode,
  Settings,
  ShoppingCart,
  Sparkles,
  Store,
  Truck,
  Users,
} from "lucide-react";
import { PaytmLogo } from "@/components/paytm-logo";
import { cn } from "@/lib/cn";
import { useAuth } from "@/lib/auth";
import type { LucideIcon } from "lucide-react";

type NavItem = {
  href: string;
  label: string;
  icon: LucideIcon;
  soon?: boolean;
};

const mainNav: NavItem[] = [
  { href: "/", label: "Home", icon: Home },
  { href: "/ai-assistant", label: "AI Assistant", icon: Sparkles },
  { href: "/smart-counter", label: "Smart Counter", icon: ScanBarcode },
  { href: "/orders", label: "Orders", icon: ClipboardList },
  { href: "/inventory", label: "Inventory", icon: Package },
  { href: "/catalog-admin", label: "Product Catalog", icon: Boxes },
  { href: "/purchases", label: "Purchases & Suppliers", icon: Truck },
  { href: "/retail-ops", label: "Receiving & Counts", icon: PackageOpen },
  { href: "/demand", label: "Demand & Trends", icon: BarChart3 },
  { href: "/customers", label: "Customers", icon: Users },
  { href: "/whatsapp", label: "WhatsApp & Marketing", icon: Megaphone },
  { href: "/festivals", label: "Festival Intelligence", icon: CalendarDays },
  { href: "/external", label: "External Intelligence", icon: Globe },
  { href: "/online-store", label: "Online Store", icon: Globe },
  { href: "/analytics", label: "Analytics", icon: LayoutDashboard, soon: true },
  { href: "/reports", label: "Reports", icon: FileText, soon: true },
  { href: "/alerts", label: "Alerts", icon: Bell },
];

const bottomNav: NavItem[] = [
  { href: "/settings", label: "Settings", icon: Settings, soon: true },
  { href: "/help", label: "Help & Support", icon: HelpCircle, soon: true },
];

export function AppShell({
  children,
  topActions,
}: {
  children: React.ReactNode;
  topActions?: React.ReactNode;
}) {
  const pathname = usePathname();
  const router = useRouter();
  const { user, logout } = useAuth();

  function isActive(href: string) {
    if (href === "/") return pathname === "/";
    return pathname === href || pathname.startsWith(`${href}/`);
  }

  function handleLogout() {
    logout();
    router.push("/login");
  }

  return (
    <div className="min-h-screen bg-background">
      <header className="sticky top-0 z-40 border-b border-border bg-white">
        <div className="flex h-16 items-center gap-4 px-4">
          <Link href="/" className="flex items-center gap-3 shrink-0">
            <PaytmLogo className="h-5 w-auto sm:h-6" />
            <span className="hidden h-8 w-px bg-border sm:block" />
            <span className="flex flex-col leading-tight">
              <span className="flex items-center gap-2">
                <span className="text-[17px] font-bold text-foreground">KiranaSaathi AI</span>
                <span className="rounded-full bg-[#e8f2ff] px-2 py-0.5 text-[10px] font-bold text-[#2080f0]">
                  BETA
                </span>
              </span>
              <span className="hidden text-[11px] text-text-muted sm:block">
                Your Kirana Shop&apos;s AI Business Partner
              </span>
            </span>
          </Link>

          <div className="mx-auto hidden max-w-xl flex-1 md:block">
            <div className="flex items-center gap-2 rounded-xl border border-border bg-[#f8fafc] px-3 py-2">
              <Store className="h-4 w-4 text-text-muted" />
              <input
                className="w-full bg-transparent text-sm outline-none placeholder:text-text-muted"
                placeholder="Search products, customers, or ask AI anything..."
                readOnly
                aria-label="Global search (coming later)"
              />
              <kbd className="rounded border border-border bg-white px-1.5 py-0.5 text-[10px] text-text-muted">
                Ctrl K
              </kbd>
            </div>
          </div>

          <div className="ml-auto flex items-center gap-3">
            {topActions}
            <div className="hidden text-right sm:block">
              <div className="text-sm font-semibold text-foreground">
                {user?.full_name || "Merchant"}
              </div>
              <div className="text-[11px] text-text-muted">
                {user?.store_name || "Store"}
              </div>
            </div>
            <span className="flex h-9 w-9 items-center justify-center rounded-full bg-[#e8f2ff] text-sm font-bold text-primary">
              {(user?.full_name || "M")
                .split(" ")
                .map((p) => p[0])
                .slice(0, 2)
                .join("")}
            </span>
            <button
              type="button"
              className="btn btn-ghost h-9 w-9 p-0"
              onClick={handleLogout}
              title="Log out"
              aria-label="Log out"
            >
              <LogOut className="h-4 w-4" />
            </button>
          </div>
        </div>
      </header>

      <div className="flex">
        <aside className="sticky top-16 hidden h-[calc(100vh-4rem)] w-[220px] shrink-0 flex-col border-r border-border bg-white lg:flex">
          <nav className="flex-1 space-y-0.5 overflow-y-auto p-3">
            {mainNav.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  "flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition",
                  isActive(item.href)
                    ? "bg-[#e8f2ff] text-primary"
                    : "text-text-secondary hover:bg-[#f8fafc]"
                )}
              >
                <item.icon className="h-[18px] w-[18px] shrink-0" />
                <span className="flex-1 truncate">{item.label}</span>
                {item.soon ? (
                  <span className="rounded-full bg-[#e8f2ff] px-1.5 py-0.5 text-[9px] font-bold text-primary">
                    SOON
                  </span>
                ) : null}
              </Link>
            ))}
          </nav>

          <div className="mx-3 mb-2 rounded-xl bg-gradient-to-b from-[#eff6ff] to-white p-3">
            <p className="text-[13px] font-bold text-foreground">Grow Faster with KiranaSaathi AI</p>
            <p className="mt-1 text-[11px] text-text-secondary">AI business agent active</p>
          </div>

          <div className="space-y-0.5 border-t border-border p-3">
            {bottomNav.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  "flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium",
                  isActive(item.href)
                    ? "bg-[#e8f2ff] text-primary"
                    : "text-text-secondary hover:bg-[#f8fafc]"
                )}
              >
                <item.icon className="h-[18px] w-[18px]" />
                <span className="flex-1">{item.label}</span>
                {item.soon ? (
                  <span className="rounded-full bg-[#f1f5f9] px-1.5 py-0.5 text-[9px] font-bold text-text-muted">
                    SOON
                  </span>
                ) : null}
              </Link>
            ))}
          </div>
        </aside>

        <div className="min-w-0 flex-1">
          <nav className="flex gap-1 overflow-x-auto border-b border-border bg-white px-3 py-2 lg:hidden">
            {mainNav.filter((n) => !n.soon).map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  "flex items-center gap-1.5 whitespace-nowrap rounded-lg px-3 py-1.5 text-xs font-semibold",
                  isActive(item.href) ? "bg-[#e8f2ff] text-primary" : "text-text-secondary"
                )}
              >
                <item.icon className="h-3.5 w-3.5" />
                {item.label}
              </Link>
            ))}
          </nav>
          <main className="p-4 md:p-6">{children}</main>
        </div>
      </div>
    </div>
  );
}

export function AuthGuard({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const router = useRouter();

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background">
        <div className="card w-full max-w-sm p-6 text-center">
          <div className="skeleton mb-3 h-6 w-40 mx-auto" />
          <div className="skeleton h-4 w-full" />
          <p className="mt-3 text-sm text-text-muted">Checking session…</p>
        </div>
      </div>
    );
  }

  if (!user) {
    // Navigate in an effect — calling router.replace() during render updates
    // the Router while React is rendering AuthGuard, which React forbids.
    return <RedirectToLogin />;
  }

  return <>{children}</>;
}

function RedirectToLogin() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/login");
  }, [router]);
  return null;
}
