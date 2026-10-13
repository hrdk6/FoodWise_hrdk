import { type Metric, onCLS, onFCP, onINP, onLCP, onTTFB } from "web-vitals";

/**
 * Real-user page speed (Core Web Vitals) for the admin console. Only the metric, the route
 * pattern (ids and tokens replaced) and the connection type are sent; nothing about the user.
 */

type Sample = { metric: Metric["name"]; value: number; rating: Metric["rating"]; route: string; connection?: string };

const queue: Sample[] = [];

export function routePattern(path: string) {
  return (
    path
      .replace(/\/s\/[^/]+/, "/s/:token")
      .replace(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi, ":id")
      .replace(/\/\d+(?=\/|$)/g, "/:n")
      .replace(/[^A-Za-z0-9/_:-]/g, "")
      .slice(0, 120) || "/"
  );
}

function flush() {
  if (!queue.length) return;
  const body = JSON.stringify({ samples: queue.splice(0, 20) });
  const blob = new Blob([body], { type: "application/json" });
  if (!navigator.sendBeacon?.("/api/v1/telemetry/vitals", blob)) {
    fetch("/api/v1/telemetry/vitals", { method: "POST", body, headers: { "Content-Type": "application/json" }, keepalive: true }).catch(() => {});
  }
}

function record(m: Metric) {
  const conn = (navigator as Navigator & { connection?: { effectiveType?: string } }).connection?.effectiveType;
  queue.push({ metric: m.name, value: Math.round(m.value * (m.name === "CLS" ? 1000 : 1)) / (m.name === "CLS" ? 1000 : 1), rating: m.rating, route: routePattern(location.pathname), connection: conn });
  if (queue.length >= 10) flush();
}

export function startVitals() {
  if (import.meta.env.MODE === "test") return;
  onLCP(record);
  onINP(record);
  onCLS(record);
  onFCP(record);
  onTTFB(record);
  addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") flush();
  });
  addEventListener("pagehide", flush);
}
