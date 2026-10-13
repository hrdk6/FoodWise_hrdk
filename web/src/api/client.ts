import createClient, { type Middleware } from "openapi-fetch";

import type { components, paths } from "./schema";

export type Schemas = components["schemas"];
export type Me = Schemas["MeOut"];

/** Access tokens live only in memory. The refresh token is an httpOnly cookie the page never sees. */
let accessToken: string | null = null;
let expiresAt = 0;
let refreshing: Promise<Me | null> | null = null;
let onExpired: (() => void) | null = null;

export class ApiError extends Error {
  status: number;
  code: string;
  details: unknown;
  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export function setSession(token: string | null, expiresInSeconds = 0) {
  accessToken = token;
  expiresAt = token ? Date.now() + expiresInSeconds * 1000 : 0;
}

export function onSessionExpired(fn: () => void) {
  onExpired = fn;
}

export function getAccessToken() {
  return accessToken;
}

export async function refreshSession(): Promise<Me | null> {
  if (!refreshing) {
    refreshing = (async () => {
      try {
        const res = await fetch("/api/v1/auth/refresh", {
          method: "POST",
          credentials: "include",
          headers: { "X-FoodWise-CSRF": "1" },
        });
        if (!res.ok) {
          setSession(null);
          return null;
        }
        const body = (await res.json()) as Schemas["TokenOut"];
        setSession(body.access_token, body.expires_in);
        return body.me;
      } catch {
        return null;
      } finally {
        setTimeout(() => (refreshing = null), 0);
      }
    })();
  }
  return refreshing;
}

const pending = new WeakMap<Request, Request>();

const auth: Middleware = {
  async onRequest({ request }) {
    const isAuthCall = request.url.includes("/api/v1/auth/");
    if (!isAuthCall && accessToken && Date.now() > expiresAt - 30_000) {
      await refreshSession();
    }
    if (accessToken) request.headers.set("Authorization", `Bearer ${accessToken}`);
    if (!isAuthCall) pending.set(request, request.clone());
    return request;
  },
  async onResponse({ request, response }) {
    if (response.status !== 401 || request.url.includes("/api/v1/auth/")) return response;
    const retry = pending.get(request);
    const me = await refreshSession();
    if (!me || !retry) {
      onExpired?.();
      return response;
    }
    retry.headers.set("Authorization", `Bearer ${accessToken}`);
    return fetch(retry);
  },
};

export const api = createClient<paths>({ baseUrl: "", credentials: "include" });
api.use(auth);

type Result<T> = { data?: T; error?: unknown; response: Response };

/** Unwrap an openapi-fetch result, throwing the API's error envelope as an ApiError. */
export async function unwrap<T>(p: Promise<Result<T>>): Promise<T> {
  let r: Result<T>;
  try {
    r = await p;
  } catch {
    throw new ApiError(0, "offline", "Can't reach FoodWise. Check the connection and try again.");
  }
  if (r.error !== undefined || !r.response.ok) {
    const env = (r.error as { error?: { code?: string; message?: string; details?: unknown } } | undefined)?.error;
    if (r.response.status === 503) {
      throw new ApiError(503, "unavailable", env?.message ?? "FoodWise is reconnecting. Try again in a few seconds.");
    }
    throw new ApiError(r.response.status, env?.code ?? "error", env?.message ?? "Something went wrong.", env?.details);
  }
  return r.data as T;
}

/** Authenticated download of a file (CSV or PDF report). */
export async function download(path: string, filename: string) {
  const res = await fetch(path, { headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {} });
  if (!res.ok) throw new ApiError(res.status, "download_failed", "The report could not be generated.");
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
