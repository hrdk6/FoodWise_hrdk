import { useQueryClient } from "@tanstack/react-query";
import { type ReactNode, createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { ApiError, type Me, type Schemas, onSessionExpired, refreshSession, setSession } from "@/api/client";
import { TZ, isoDay } from "@/lib/format";

type Status = "loading" | "signed_out" | "signed_in";
export type Portal = "organization" | "admin";

type AuthCtx = {
  status: Status;
  me: Me | null;
  can: (permission: string) => boolean;
  login: (email: string, password: string, portal?: Portal) => Promise<void>;
  demoLogin: (email: string, portal?: Portal) => Promise<void>;
  logout: () => Promise<void>;
  expired: boolean;
};

const Ctx = createContext<AuthCtx | null>(null);

// A non-secret hint that this browser has a refresh cookie, so a fresh visit does not probe
// /auth/refresh (and log a 401) when nobody has signed in.
const HINT = "fw-signed-in";
const hint = {
  get: () => {
    try {
      return localStorage.getItem(HINT) === "1";
    } catch {
      return true;
    }
  },
  set: (on: boolean) => {
    try {
      if (on) localStorage.setItem(HINT, "1");
      else localStorage.removeItem(HINT);
    } catch {
      /* ignore */
    }
  },
};

async function postJson<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).catch(() => {
    throw new ApiError(0, "offline", "Can't reach FoodWise. Check the connection and try again.");
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const env = (data as { error?: { code?: string; message?: string } }).error;
    throw new ApiError(res.status, env?.code ?? "error", env?.message ?? "Sign-in failed.");
  }
  return data as T;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status>("loading");
  const [me, setMe] = useState<Me | null>(null);
  const [expired, setExpired] = useState(false);
  const qc = useQueryClient();

  useEffect(() => {
    let alive = true;
    (hint.get() ? refreshSession() : Promise.resolve(null)).then((m) => {
      if (!alive) return;
      hint.set(!!m);
      setMe(m);
      setStatus(m ? "signed_in" : "signed_out");
    });
    onSessionExpired(() => {
      hint.set(false);
      setMe(null);
      setExpired(true);
      setStatus("signed_out");
    });
    return () => {
      alive = false;
    };
  }, []);

  const accept = useCallback(
    (t: Schemas["TokenOut"]) => {
      setSession(t.access_token, t.expires_in);
      hint.set(true);
      qc.clear();
      setMe(t.me);
      setExpired(false);
      setStatus("signed_in");
    },
    [qc],
  );

  const value = useMemo<AuthCtx>(
    () => ({
      status,
      me,
      expired,
      can: (p) => !!me?.permissions.includes(p),
      login: async (email, password, portal = "organization") =>
        accept(await postJson<Schemas["TokenOut"]>("/api/v1/auth/login", { email, password, portal })),
      demoLogin: async (email, portal = "organization") =>
        accept(await postJson<Schemas["TokenOut"]>("/api/v1/auth/demo-login", { email, portal })),
      logout: async () => {
        await fetch("/api/v1/auth/logout", { method: "POST", credentials: "include", headers: { "X-FoodWise-CSRF": "1" } }).catch(() => {});
        setSession(null);
        hint.set(false);
        qc.clear();
        setMe(null);
        setStatus("signed_out");
      },
    }),
    [status, me, expired, accept, qc],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}

/** The canteen the user is working on, remembered per user on this device. */
type CanteenCtx = {
  canteens: Schemas["CanteenBrief"][];
  canteen: Schemas["CanteenBrief"] | null;
  setCanteenId: (id: string) => void;
  tz: string;
  today: string;
};
const CanteenContext = createContext<CanteenCtx | null>(null);

export function CanteenProvider({ children }: { children: ReactNode }) {
  const { me } = useAuth();
  const canteens = useMemo(() => me?.canteens ?? [], [me]);
  const key = me ? `fw-canteen-${me.user.id}` : "fw-canteen";
  const [id, setId] = useState<string | null>(() => {
    try {
      return localStorage.getItem(key);
    } catch {
      return null;
    }
  });
  const canteen = canteens.find((c) => c.id === id) ?? canteens[0] ?? null;
  const tz = me?.tenant?.timezone ?? TZ;
  const [today, setToday] = useState(() => isoDay(new Date(), tz));
  useEffect(() => {
    const t = setInterval(() => setToday(isoDay(new Date(), tz)), 60_000);
    return () => clearInterval(t);
  }, [tz]);
  const value = useMemo<CanteenCtx>(
    () => ({
      canteens,
      canteen,
      tz,
      today,
      setCanteenId: (next) => {
        setId(next);
        try {
          localStorage.setItem(key, next);
        } catch {
          /* ignore */
        }
      },
    }),
    [canteens, canteen, tz, today, key],
  );
  return <CanteenContext.Provider value={value}>{children}</CanteenContext.Provider>;
}

export function useCanteen() {
  const v = useContext(CanteenContext);
  if (!v) throw new Error("useCanteen outside CanteenProvider");
  return v;
}

export function homeFor(role: string | undefined) {
  return role === "admin" ? "/admin" : "/";
}
