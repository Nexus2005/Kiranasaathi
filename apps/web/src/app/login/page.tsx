"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import { AlertCircle, Lock, Mail, Store, User } from "lucide-react";
import { useAuth } from "@/lib/auth";
import { PaytmLogo } from "@/components/paytm-logo";

export default function LoginPage() {
  const { login, register } = useAuth();
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("ramesh@kirana.demo");
  const [password, setPassword] = useState("Demo@12345");
  const [fullName, setFullName] = useState("Ramesh Verma");
  const [storeName, setStoreName] = useState("Sharma Kirana Store");
  const [location, setLocation] = useState("Nashik, Maharashtra");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (mode === "login") {
        await login(email.trim(), password);
      } else {
        await register({
          email: email.trim(),
          password,
          full_name: fullName.trim(),
          store_name: storeName.trim(),
          location: location.trim() || undefined,
        });
      }
      router.replace("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Authentication failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <div className="grid w-full max-w-5xl overflow-hidden rounded-2xl border border-border bg-white shadow-sm md:grid-cols-2">
        <div className="hidden flex-col justify-between bg-gradient-to-br from-[#eff6ff] to-[#f8fafc] p-8 md:flex">
          <div>
            <div className="flex items-center gap-2">
              <PaytmLogo className="h-7 w-auto" />
              <span className="text-lg font-extrabold text-foreground">KiranaSaathi AI</span>
            </div>
            <p className="mt-1 text-sm text-text-muted">Your Kirana Shop&apos;s AI Business Partner</p>
            <h1 className="mt-10 text-3xl font-bold leading-snug text-foreground">
              Run a smarter
              <br />
              kirana store with AI
            </h1>
            <p className="mt-3 text-sm text-text-secondary">
              Sales, inventory, customers and AI priorities — one operating system for your shop.
            </p>
          </div>
          <ul className="space-y-2 text-sm text-text-secondary">
            <li>• Real inventory & sales state</li>
            <li>• Smart Counter with live stock updates</li>
            <li>• Evidence-backed recommendations foundation</li>
          </ul>
        </div>

        <form onSubmit={onSubmit} className="p-6 md:p-8">
          <div className="mb-6 flex rounded-xl bg-[#f1f5f9] p-1">
            <button
              type="button"
              className={`flex-1 rounded-lg py-2 text-sm font-semibold ${mode === "login" ? "bg-white text-primary shadow-sm" : "text-text-secondary"}`}
              onClick={() => setMode("login")}
            >
              Login
            </button>
            <button
              type="button"
              className={`flex-1 rounded-lg py-2 text-sm font-semibold ${mode === "register" ? "bg-white text-primary shadow-sm" : "text-text-secondary"}`}
              onClick={() => setMode("register")}
            >
              Create store
            </button>
          </div>

          <h2 className="text-xl font-bold text-foreground">
            {mode === "login" ? "Welcome back" : "Set up your shop"}
          </h2>
          <p className="mt-1 text-sm text-text-secondary">
            {mode === "login"
              ? "Demo: ramesh@kirana.demo / Demo@12345"
              : "Create merchant + store account"}
          </p>

          {error ? (
            <div className="mt-4 flex items-start gap-2 rounded-xl border border-[#fecaca] bg-[#fef2f2] px-3 py-2 text-sm text-[#b91c1c]">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>{error}</span>
            </div>
          ) : null}

          <div className="mt-5 space-y-3">
            {mode === "register" ? (
              <>
                <Field icon={User} label="Your name">
                  <input className="input" value={fullName} onChange={(e) => setFullName(e.target.value)} required minLength={2} />
                </Field>
                <Field icon={Store} label="Store name">
                  <input className="input" value={storeName} onChange={(e) => setStoreName(e.target.value)} required minLength={2} />
                </Field>
                <Field icon={Store} label="Location">
                  <input className="input" value={location} onChange={(e) => setLocation(e.target.value)} placeholder="City, State" />
                </Field>
              </>
            ) : null}

            <Field icon={Mail} label="Email">
              <input
                className="input"
                type="email"
                autoComplete="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
              />
            </Field>
            <Field icon={Lock} label="Password">
              <input
                className="input"
                type="password"
                autoComplete={mode === "login" ? "current-password" : "new-password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                minLength={8}
              />
            </Field>
          </div>

          <button type="submit" className="btn btn-primary mt-6 w-full" disabled={busy}>
            {busy ? "Please wait…" : mode === "login" ? "Login" : "Create store & login"}
          </button>
        </form>
      </div>
    </div>
  );
}

function Field({
  icon: Icon,
  label,
  children,
}: {
  icon: typeof Mail;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1.5 flex items-center gap-1.5 text-[13px] font-medium text-text-secondary">
        <Icon className="h-3.5 w-3.5" />
        {label}
      </span>
      {children}
    </label>
  );
}
