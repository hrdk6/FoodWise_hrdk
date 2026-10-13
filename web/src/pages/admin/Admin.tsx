import { useIsFetching } from "@tanstack/react-query";
import { LogOut } from "lucide-react";
import { Suspense, lazy } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router";

import { useAuth } from "@/app/auth";
import { Spinner, cx } from "@/ui/primitives";

const Overview = lazy(() => import("./Overview"));
const Models = lazy(() => import("./Models"));
const System = lazy(() => import("./System"));
const People = lazy(() => import("./People"));

const TABS = [
  { to: "/admin", label: "Overview", end: true },
  { to: "/admin/models", label: "Models" },
  { to: "/admin/api", label: "API" },
  { to: "/admin/jobs", label: "Jobs and workers" },
  { to: "/admin/database", label: "Database" },
  { to: "/admin/web", label: "Page speed" },
  { to: "/admin/organisations", label: "Organisations" },
  { to: "/admin/security", label: "Sign-ins" },
];

function Live() {
  const busy = useIsFetching({ queryKey: ["ops"] });
  return (
    <span className="inline-flex items-center gap-2 text-sm text-steel/75" aria-live="polite">
      <span className={cx("size-2 rounded-full bg-[#7FC79A]", busy ? "animate-pulse" : "")} aria-hidden />
      {busy ? "Refreshing" : "Live, every 15 s"}
    </span>
  );
}

export default function Admin() {
  const { me, logout } = useAuth();
  return (
    <div className="min-h-dvh bg-steel">
      <header className="bg-ink text-steel">
        <div className="mx-auto flex max-w-[1280px] flex-wrap items-center justify-between gap-3 px-4 pt-4 sm:px-6">
          <div className="flex items-center gap-2.5">
            <svg viewBox="0 0 64 64" className="size-8" aria-hidden>
              <rect width="64" height="64" rx="14" fill="#E6A21A" />
              <circle cx="32" cy="34" r="19" fill="none" stroke="#1C2A4A" strokeWidth="3" />
              <path d="M14 30c6 4 12 6 18 6s12-2 18-6" fill="none" stroke="#1C2A4A" strokeWidth="4" strokeLinecap="round" />
            </svg>
            <span className="text-xl font-[720]" style={{ fontStretch: "118%" }}>
              FoodWise
            </span>
            <span className="rounded-[6px] border border-white/20 px-2 py-0.5 text-sm text-steel/80">Admin</span>
          </div>
          <div className="flex items-center gap-4">
            <Live />
            <span className="hidden text-sm text-steel/70 sm:inline">{me?.user.email}</span>
            <button onClick={logout} className="inline-flex items-center gap-1.5 rounded-md px-2 py-1.5 text-sm text-steel/80 hover:bg-white/10 hover:text-steel">
              <LogOut className="size-4" aria-hidden /> Sign out
            </button>
          </div>
        </div>
        <nav aria-label="Admin" className="mx-auto mt-3 max-w-[1280px] overflow-x-auto px-2 sm:px-4">
          <ul className="flex min-w-max gap-1">
            {TABS.map((t) => (
              <li key={t.to}>
                <NavLink
                  to={t.to}
                  end={t.end}
                  className={({ isActive }) =>
                    cx(
                      "block rounded-t-[8px] px-3.5 py-2.5 text-[15px] font-medium transition-colors",
                      isActive ? "bg-steel text-ink" : "text-steel/75 hover:bg-white/10 hover:text-steel",
                    )
                  }
                >
                  {t.label}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
      </header>
      <main className="mx-auto max-w-[1280px] px-4 pb-16 pt-6 sm:px-6">
        <Suspense fallback={<Spinner />}>
          <Routes>
            <Route index element={<Overview />} />
            <Route path="models" element={<Models />} />
            <Route path="api" element={<System part="api" />} />
            <Route path="jobs" element={<System part="jobs" />} />
            <Route path="database" element={<System part="database" />} />
            <Route path="web" element={<People part="web" />} />
            <Route path="organisations" element={<People part="organisations" />} />
            <Route path="security" element={<People part="security" />} />
            <Route path="*" element={<Navigate to="/admin" replace />} />
          </Routes>
        </Suspense>
      </main>
    </div>
  );
}
