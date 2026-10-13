import {
  Activity,
  BarChart3,
  ChefHat,
  ClipboardList,
  Database,
  FlaskConical,
  HandHeart,
  LayoutDashboard,
  LogOut,
  Menu,
  MessageSquareText,
  Settings,
  MapPin,
  Scale,
  Sparkles,
  Table2,
  UtensilsCrossed,
  WifiOff,
  X,
} from "lucide-react";
import { type ReactNode, Suspense, useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router";

import { useIncidents } from "@/api/hooks";
import { useOnline } from "@/lib/offline";
import { Spinner, cx } from "@/ui/primitives";

import { useAuth, useCanteen } from "./auth";

type Item = { to: string; label: string; icon: ReactNode; perm: string; end?: boolean };
type Group = { label: string; items: Item[] };

const GROUPS: Group[] = [
  {
    label: "Today",
    items: [
      { to: "/", label: "Overview", icon: <LayoutDashboard />, perm: "forecast:read", end: true },
      { to: "/plan", label: "Prep plan", icon: <ClipboardList />, perm: "forecast:read" },
      { to: "/menu", label: "Menu", icon: <UtensilsCrossed />, perm: "tenant:read" },
      { to: "/kitchen", label: "Kitchen log", icon: <ChefHat />, perm: "logs:write" },
      { to: "/live", label: "Live service", icon: <Activity />, perm: "logs:read" },
    ],
  },
  {
    label: "Rescue",
    items: [
      { to: "/rescue", label: "Surplus rescue", icon: <HandHeart />, perm: "incident:read" },
      { to: "/places", label: "Places nearby", icon: <MapPin />, perm: "places:read" },
    ],
  },
  {
    label: "Learn",
    items: [
      { to: "/feedback", label: "Student feedback", icon: <MessageSquareText />, perm: "feedback:read" },
      { to: "/models", label: "Forecast models", icon: <Sparkles />, perm: "models:read" },
      { to: "/dataset", label: "Training data", icon: <Table2 />, perm: "logs:read" },
      { to: "/benchmark", label: "Real-data benchmark", icon: <Scale />, perm: "models:read" },
      { to: "/impact", label: "Impact", icon: <BarChart3 />, perm: "impact:read" },
      { to: "/whatif", label: "What if", icon: <FlaskConical />, perm: "forecast:read" },
    ],
  },
  {
    label: "Setup",
    items: [
      { to: "/data", label: "Data and imports", icon: <Database />, perm: "imports:write" },
      { to: "/settings", label: "Settings", icon: <Settings />, perm: "tenant:read" },
    ],
  },
];

function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <div className="flex shrink-0 items-center gap-2.5">
      <svg viewBox="0 0 64 64" className="size-8" aria-hidden>
        <rect width="64" height="64" rx="14" fill="#1C2A4A" />
        <circle cx="32" cy="34" r="19" fill="none" stroke="#EDF0EE" strokeWidth="3" />
        <circle cx="32" cy="34" r="12.5" fill="none" stroke="#EDF0EE" strokeWidth="1.5" opacity=".55" />
        <path d="M14 30c6 4 12 6 18 6s12-2 18-6" fill="none" stroke="#E6A21A" strokeWidth="4" strokeLinecap="round" />
      </svg>
      <span className={cx("text-xl font-[720] tracking-tight", compact && "sr-only sm:not-sr-only")} style={{ fontStretch: "118%" }}>
        FoodWise
      </span>
    </div>
  );
}

function CanteenSwitcher() {
  const { canteens, canteen, setCanteenId } = useCanteen();
  if (canteens.length <= 1) return <p className="truncate text-base font-medium text-ink">{canteen?.name}</p>;
  return (
    <label className="block">
      <span className="sr-only">Canteen</span>
      <select
        value={canteen?.id}
        onChange={(e) => setCanteenId(e.target.value)}
        className="h-9 w-full cursor-pointer appearance-none rounded-[8px] border border-rim bg-plate pl-3 pr-8 text-base font-medium text-ink hover:border-rim-strong"
        style={{
          backgroundImage:
            "url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'><path d='M2 4l4 4 4-4' fill='none' stroke='%233a4763' stroke-width='1.6'/></svg>\")",
          backgroundRepeat: "no-repeat",
          backgroundPosition: "right 0.7rem center",
        }}
      >
        {canteens.map((c) => (
          <option key={c.id} value={c.id}>
            {c.name}
          </option>
        ))}
      </select>
    </label>
  );
}

function NavList({ onNavigate }: { onNavigate?: () => void }) {
  const { can } = useAuth();
  const active = useIncidents("active");
  const activeCount = active.data?.length ?? 0;
  return (
    <nav aria-label="Main" className="flex flex-col gap-5">
      {GROUPS.map((g) => {
        const items = g.items.filter((i) => can(i.perm));
        if (!items.length) return null;
        return (
          <div key={g.label}>
            <p className="mb-1.5 px-3 text-sm text-muted">{g.label}</p>
            <ul className="flex flex-col gap-0.5">
              {items.map((i) => (
                <li key={i.to}>
                  <NavLink
                    to={i.to}
                    end={i.end}
                    onClick={onNavigate}
                    className={({ isActive }) =>
                      cx(
                        "relative flex h-9 items-center gap-2.5 rounded-[8px] px-3 text-base transition-colors [&_svg]:size-[18px]",
                        isActive ? "bg-plate font-[620] text-ink ring-1 ring-rim" : "text-ink-2 hover:bg-steel-2 hover:text-ink",
                      )
                    }
                  >
                    {({ isActive }) => (
                      <>
                        {isActive ? <span className="absolute -left-[13px] top-2 h-5 w-[3px] rounded-r bg-haldi" aria-hidden /> : null}
                        <span className={isActive ? "text-ink" : "text-muted"}>{i.icon}</span>
                        <span className="flex-1">{i.label}</span>
                        {i.to === "/rescue" && activeCount > 0 ? (
                          <span className="num rounded-full bg-chilli px-1.5 text-xs font-semibold leading-5 text-white">{activeCount}</span>
                        ) : null}
                      </>
                    )}
                  </NavLink>
                </li>
              ))}
            </ul>
          </div>
        );
      })}
    </nav>
  );
}

function UserBlock() {
  const { me, logout } = useAuth();
  const name = me?.tenant?.name ?? me?.user.name ?? "";
  return (
    <div className="flex items-center gap-2 border-t border-rim pt-4">
      <div className="flex size-9 shrink-0 items-center justify-center rounded-full bg-ink text-sm font-semibold text-steel" aria-hidden>
        {name.split(" ").filter((p) => /^[A-Z]/.test(p)).map((p) => p[0]).slice(0, 2).join("")}
      </div>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-ink">{me?.user.name}</p>
        <p className="truncate text-xs text-muted">{me?.user.email}</p>
      </div>
      <button onClick={logout} className="rounded-md p-2 text-muted hover:bg-steel-2 hover:text-ink" aria-label="Sign out" title="Sign out">
        <LogOut className="size-4.5" />
      </button>
    </div>
  );
}

export function OfflineBanner() {
  const online = useOnline();
  if (online) return null;
  return (
    <div role="status" className="flex items-center justify-center gap-2 bg-ink px-4 py-1.5 text-sm text-steel">
      <WifiOff className="size-4" aria-hidden /> Offline. You can keep logging; entries sync when the connection returns.
    </div>
  );
}

export function StaffShell() {
  const { me } = useAuth();
  const [open, setOpen] = useState(false);
  const loc = useLocation();
  useEffect(() => setOpen(false), [loc.pathname]);
  return (
    <div className="min-h-dvh">
      <OfflineBanner />
      <div className="lg:grid lg:grid-cols-[248px_minmax(0,1fr)]">
        <aside className="sticky top-0 hidden h-dvh flex-col gap-5 overflow-y-auto border-r border-rim px-4 py-5 lg:flex">
          <Brand />
          <div className="flex flex-col gap-1.5">
            <p className="truncate text-sm text-muted">{me?.tenant?.name}</p>
            <CanteenSwitcher />
          </div>
          <NavList />
          <div className="mt-auto">
            <UserBlock />
          </div>
        </aside>

        <header className="sticky top-0 z-30 flex items-center justify-between gap-3 border-b border-rim bg-steel/95 px-4 py-2.5 backdrop-blur lg:hidden">
          <Brand compact />
          <div className="flex min-w-0 flex-1 items-center justify-end gap-2">
            <div className="min-w-0 max-w-64 flex-1 sm:flex-none">
              <CanteenSwitcher />
            </div>
            <button onClick={() => setOpen(true)} className="rounded-md p-2 text-ink hover:bg-steel-2" aria-label="Open menu">
              <Menu className="size-5" />
            </button>
          </div>
        </header>
        {open ? (
          <div className="fixed inset-0 z-40 lg:hidden" role="dialog" aria-modal aria-label="Menu">
            <div className="absolute inset-0 bg-ink/35" onClick={() => setOpen(false)} />
            <div className="absolute inset-y-0 right-0 flex w-[min(320px,88vw)] flex-col gap-5 overflow-y-auto bg-steel px-4 py-4 shadow-xl">
              <div className="flex items-center justify-between">
                <p className="truncate text-sm text-muted">{me?.tenant?.name}</p>
                <button onClick={() => setOpen(false)} className="rounded-md p-2 hover:bg-steel-2" aria-label="Close menu">
                  <X className="size-5" />
                </button>
              </div>
              <NavList onNavigate={() => setOpen(false)} />
              <div className="mt-auto">
                <UserBlock />
              </div>
            </div>
          </div>
        ) : null}

        <main id="main" className="min-w-0 px-4 pb-24 pt-6 sm:px-6 lg:px-10 lg:pb-12 lg:pt-8">
          <div className="mx-auto max-w-[1180px]">
            <Suspense fallback={<Spinner />}>
              <Outlet />
            </Suspense>
          </div>
        </main>
      </div>
    </div>
  );
}
