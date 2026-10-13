import { useQuery } from "@tanstack/react-query";
import { ArrowRightLeft, Gauge } from "lucide-react";
import { type FormEvent, useState } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router";

import { ApiError, type Schemas } from "@/api/client";
import { type Portal, homeFor, useAuth } from "@/app/auth";
import { Button, Field, Input, cx } from "@/ui/primitives";

function Mark({ className = "size-9" }: { className?: string }) {
  return (
    <svg viewBox="0 0 64 64" className={className} aria-hidden>
      <rect width="64" height="64" rx="14" fill="#1C2A4A" />
      <circle cx="32" cy="34" r="19" fill="none" stroke="#EDF0EE" strokeWidth="3" />
      <circle cx="32" cy="34" r="12.5" fill="none" stroke="#EDF0EE" strokeWidth="1.5" opacity=".55" />
      <path d="M14 30c6 4 12 6 18 6s12-2 18-6" fill="none" stroke="#E6A21A" strokeWidth="4" strokeLinecap="round" />
    </svg>
  );
}

function useSignIn(portal: Portal) {
  const { login, demoLogin } = useAuth();
  const nav = useNavigate();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const run = async (key: string, fn: () => Promise<void>) => {
    setBusy(key);
    setError(null);
    try {
      await fn();
      nav(portal === "admin" ? "/admin" : "/", { replace: true });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Sign-in failed.");
    } finally {
      setBusy(null);
    }
  };
  return {
    error,
    busy,
    password: (email: string, password: string) => run("form", () => login(email, password, portal)),
    demo: (email: string) => run(email, () => demoLogin(email, portal)),
  };
}

function useDemoAccounts(portal: Portal) {
  return useQuery({
    queryKey: ["demo-accounts"],
    queryFn: async () => {
      const r = await fetch("/api/v1/auth/demo-accounts");
      return r.ok ? ((await r.json()) as Schemas["DemoAccount"][]) : [];
    },
    staleTime: Infinity,
    select: (all) => all.filter((a) => a.portal === portal),
  });
}

function SignInForm({ portal, tone = "light" }: { portal: Portal; tone?: "light" | "dark" }) {
  const { expired } = useAuth();
  const s = useSignIn(portal);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    s.password(email, password);
  };
  const dark = tone === "dark";
  return (
    <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
      {expired ? <p className="rounded-[8px] bg-haldi-soft px-3 py-2 text-sm text-haldi-deep">Your session ended. Sign in again to continue.</p> : null}
      <Field label={portal === "admin" ? "Engineer email" : "Organisation email"} htmlFor="email" className={dark ? "[&_label]:text-steel/80" : undefined}>
        <Input id="email" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required />
      </Field>
      <Field label="Password" htmlFor="password" className={dark ? "[&_label]:text-steel/80" : undefined}>
        <Input id="password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
      </Field>
      {s.error ? (
        <p role="alert" className={cx("text-sm", dark ? "text-[#F2A79C]" : "text-chilli")}>
          {s.error}
        </p>
      ) : null}
      <Button type="submit" variant="primary" size="lg" busy={s.busy === "form"} disabled={!email || !password}>
        Sign in
      </Button>
    </form>
  );
}

function DemoAccounts({ portal, dark = false }: { portal: Portal; dark?: boolean }) {
  const demo = useDemoAccounts(portal);
  const s = useSignIn(portal);
  if (!demo.data?.length) return null;
  return (
    <div>
      <div className={cx("flex items-center gap-2", dark ? "text-steel/80" : "text-ink-2")}>
        <ArrowRightLeft className="size-4" aria-hidden />
        <h2 className="text-lg font-[650]">Try a demo account</h2>
      </div>
      <p className={cx("mt-1.5 text-base", dark ? "text-steel/60" : "text-muted")}>Development only. No password needed.</p>
      <ul className="mt-4 flex flex-col gap-2">
        {demo.data.map((a, i) => (
          <li key={a.email}>
            <button
              onClick={() => s.demo(a.email)}
              disabled={!!s.busy}
              className={cx(
                "relative flex w-full items-center gap-4 rounded-[12px] border px-4 py-3 text-left transition-colors",
                dark ? "border-white/15 bg-white/5 hover:border-white/30" : "rim hover:border-rim-strong",
                s.busy === a.email && "opacity-60",
                !dark && i === 0 && "border-haldi ring-1 ring-haldi",
              )}
            >
              <span className={cx("flex size-10 shrink-0 items-center justify-center rounded-full text-sm font-semibold", dark ? "bg-haldi text-ink" : "bg-ink text-steel")}>
                {(a.organisation ?? a.name).split(" ").filter((p) => /^[A-Z]/.test(p)).map((p) => p[0]).slice(0, 2).join("")}
              </span>
              <span className="min-w-0 flex-1">
                <span className={cx("block font-[620]", dark ? "text-steel" : "text-ink")}>{a.organisation ?? a.name}</span>
                <span className={cx("block text-sm", dark ? "text-steel/60" : "text-muted")}>{a.detail}</span>
              </span>
              {!dark && i === 0 ? (
                <span className="hidden shrink-0 rounded-[6px] bg-haldi-soft px-2 py-0.5 text-xs font-medium text-haldi-deep sm:inline">Start here</span>
              ) : null}
            </button>
          </li>
        ))}
      </ul>
      {s.error ? (
        <p role="alert" className={cx("mt-3 text-sm", dark ? "text-[#F2A79C]" : "text-chilli")}>
          {s.error}
        </p>
      ) : null}
    </div>
  );
}

export default function Login({ portal }: { portal: Portal }) {
  const { status, me } = useAuth();
  const loc = useLocation();
  if (status === "signed_in" && me) {
    const from = (loc.state as { from?: string } | null)?.from;
    const home = homeFor(me.user.role);
    const fits = from && !from.endsWith("/login") && from.startsWith("/admin") === home.startsWith("/admin");
    return <Navigate to={fits ? from : home} replace />;
  }

  if (portal === "admin") {
    return (
      <div className="flex min-h-dvh flex-col bg-ink px-6 py-8 text-steel sm:px-12">
        <div className="flex items-center gap-2.5">
          <Mark />
          <span className="text-2xl font-[720]" style={{ fontStretch: "118%" }}>
            FoodWise
          </span>
          <span className="ml-1 rounded-[6px] border border-white/20 px-2 py-0.5 text-sm text-steel/80">Admin</span>
        </div>
        <div className="mx-auto my-auto grid w-full max-w-4xl gap-12 py-12 md:grid-cols-2">
          <div>
            <Gauge className="mb-4 size-8 text-haldi" aria-hidden />
            <h1 className="text-4xl font-[700] leading-[1.05]" style={{ fontStretch: "120%" }}>
              How FoodWise is running
            </h1>
            <p className="mt-4 text-lg text-steel/75">
              Forecast accuracy and drift for every canteen, API speed and errors, background jobs, the database and real users' page
              speed. You see aggregates, never an organisation's records.
            </p>
            <div className="mt-8 max-w-sm">
              <SignInForm portal="admin" tone="dark" />
            </div>
          </div>
          <div className="md:pt-14">
            <DemoAccounts portal="admin" dark />
          </div>
        </div>
        <p className="text-sm text-steel/60">
          Running a canteen?{" "}
          <Link to="/login" className="text-steel underline underline-offset-4">
            Organisation sign-in
          </Link>
        </p>
      </div>
    );
  }

  return (
    <div className="grid min-h-dvh lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
      <div className="flex flex-col justify-between px-6 py-8 sm:px-12 lg:px-16">
        <div className="flex items-center gap-2.5">
          <Mark />
          <span className="text-2xl font-[720]" style={{ fontStretch: "118%" }}>
            FoodWise
          </span>
        </div>

        <div className="my-12 max-w-md">
          <h1 className="text-4xl font-[700] leading-[1.05]" style={{ fontStretch: "120%" }}>
            Cook what will be eaten.
          </h1>
          <p className="mt-4 text-lg text-ink-2">
            Tomorrow's prep plan for every canteen, what students thought of today's food, and the nearest places that can use what is left.
          </p>
          <p className="mt-3 text-base text-muted">One sign-in for your whole organisation: managers, cooks and anyone who plans food.</p>
          <div className="mt-8">
            <SignInForm portal="organization" />
          </div>
        </div>
        <p className="text-sm text-muted">
          FoodWise engineer?{" "}
          <Link to="/admin/login" className="text-ink-2 underline underline-offset-4 hover:text-ink">
            Admin sign-in
          </Link>
        </p>
      </div>

      <aside className="border-t border-rim bg-steel-2 px-6 py-8 sm:px-12 lg:border-l lg:border-t-0 lg:px-14 lg:py-14">
        <div className="max-w-xl">
          <DemoAccounts portal="organization" />
        </div>
      </aside>
    </div>
  );
}
