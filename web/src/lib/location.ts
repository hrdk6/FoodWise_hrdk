import { useCallback, useEffect, useState } from "react";

/**
 * The device's location, for finding places near where the food is. It is asked for only
 * when someone presses "Use my location" (or automatically if the browser already allows it),
 * kept in memory for this tab, and sent to FoodWise only as part of a nearby-places search.
 */

export type Coords = { lat: number; lon: number; accuracy: number; at: number };
export type LocationState =
  | { status: "idle" }
  | { status: "asking" }
  | { status: "ready"; coords: Coords }
  | { status: "denied"; message: string }
  | { status: "unavailable"; message: string };

let remembered: Coords | null = null;
const FRESH_MS = 10 * 60_000;

function locate(): Promise<Coords> {
  return new Promise((resolve, reject) => {
    if (!("geolocation" in navigator)) {
      reject(Object.assign(new Error("This browser cannot share its location."), { code: 0 }));
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (p) => resolve({ lat: p.coords.latitude, lon: p.coords.longitude, accuracy: Math.round(p.coords.accuracy), at: Date.now() }),
      (e) => reject(e),
      { enableHighAccuracy: false, timeout: 12_000, maximumAge: 5 * 60_000 },
    );
  });
}

export function useDeviceLocation({ auto = true }: { auto?: boolean } = {}) {
  const [state, setState] = useState<LocationState>(() =>
    remembered && Date.now() - remembered.at < FRESH_MS ? { status: "ready", coords: remembered } : { status: "idle" },
  );

  const request = useCallback(async () => {
    setState({ status: "asking" });
    try {
      const c = await locate();
      remembered = c;
      setState({ status: "ready", coords: c });
    } catch (e) {
      const code = (e as GeolocationPositionError).code;
      if (code === 1) setState({ status: "denied", message: "Location is blocked for FoodWise in this browser. Showing places near the canteen." });
      else setState({ status: "unavailable", message: "Could not find your location just now. Showing places near the canteen." });
    }
  }, []);

  // If the browser already allows location for this site, use it without asking again.
  useEffect(() => {
    if (!auto || state.status !== "idle" || !navigator.permissions?.query) return;
    let alive = true;
    navigator.permissions
      .query({ name: "geolocation" as PermissionName })
      .then((p) => {
        if (!alive) return;
        if (p.state === "granted") request();
        else if (p.state === "denied") setState({ status: "denied", message: "Location is blocked for FoodWise in this browser. Showing places near the canteen." });
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [auto, state.status, request]);

  return { state, request, coords: state.status === "ready" ? state.coords : null };
}

export function directionsUrl(lat: number, lon: number) {
  return `https://www.google.com/maps/dir/?api=1&destination=${lat.toFixed(6)},${lon.toFixed(6)}`;
}

/** wa.me wants the number in international form without symbols. Indian numbers without a
 * country code get +91. */
export function whatsappUrl(phone: string, text: string) {
  let digits = phone.replace(/[^\d+]/g, "");
  if (digits.startsWith("+")) digits = digits.slice(1);
  else if (digits.startsWith("0") && digits.length === 11) digits = `91${digits.slice(1)}`;
  else if (digits.length === 10) digits = `91${digits}`;
  return `https://wa.me/${digits}?text=${encodeURIComponent(text)}`;
}

export function telUrl(phone: string) {
  return `tel:${phone.replace(/[^\d+]/g, "")}`;
}

export function findNumberUrl(name: string, area?: string | null) {
  return `https://www.google.com/search?q=${encodeURIComponent(`${name} ${area ?? ""} phone number`.trim())}`;
}
