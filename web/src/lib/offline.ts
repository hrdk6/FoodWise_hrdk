import { createStore, get, set } from "idb-keyval";
import { useEffect, useState, useSyncExternalStore } from "react";

import { type Schemas, api, unwrap } from "@/api/client";

/**
 * Kitchen entries are written to IndexedDB first and synced to the server in batches.
 * The queue is keyed by the entry's natural key (canteen, date, meal, dish), so re-editing a dish
 * while offline replaces the earlier entry instead of piling up. The server upserts on the same
 * key, so replaying a batch after a dropped connection is harmless.
 */
export type Entry = Schemas["EntryIn"];

const store = typeof indexedDB !== "undefined" ? createStore("foodwise", "kitchen") : undefined;
const KEY = "queue";
const listeners = new Set<() => void>();
let memory: Record<string, Entry> = {};

function notify() {
  listeners.forEach((l) => l());
}

async function load(): Promise<Record<string, Entry>> {
  if (!store) return memory;
  try {
    return ((await get(KEY, store)) as Record<string, Entry> | undefined) ?? {};
  } catch {
    return memory;
  }
}

async function save(q: Record<string, Entry>) {
  memory = q;
  if (store) {
    try {
      await set(KEY, q, store);
    } catch {
      /* private mode: keep in memory */
    }
  }
  notify();
}

export const entryKey = (e: Pick<Entry, "canteen_id" | "date" | "meal" | "dish_id">) => `${e.canteen_id}|${e.date}|${e.meal}|${e.dish_id}`;

export async function enqueue(entries: Entry[]) {
  const q = await load();
  for (const e of entries) q[entryKey(e)] = { ...e, edited_at: new Date().toISOString() };
  await save(q);
}

export async function pendingEntries(): Promise<Entry[]> {
  return Object.values(await load());
}

let flushing: Promise<Schemas["BulkOut"] | null> | null = null;

export async function flush(): Promise<Schemas["BulkOut"] | null> {
  if (flushing) return flushing;
  flushing = (async () => {
    const q = await load();
    const entries = Object.values(q);
    if (!entries.length || !navigator.onLine) return null;
    try {
      const out = await unwrap(api.POST("/api/v1/logs/bulk", { body: { entries } }));
      const current = await load();
      for (const e of entries) {
        const k = entryKey(e);
        // Drop only what we sent; a newer edit made during the request stays queued.
        if (current[k]?.edited_at === e.edited_at) delete current[k];
      }
      await save(current);
      return out;
    } catch {
      return null;
    } finally {
      flushing = null;
    }
  })();
  return flushing;
}

export function usePendingCount() {
  const [n, setN] = useState(0);
  useEffect(() => {
    const update = () => load().then((q) => setN(Object.keys(q).length));
    update();
    listeners.add(update);
    return () => {
      listeners.delete(update);
    };
  }, []);
  return n;
}

function subscribeOnline(cb: () => void) {
  window.addEventListener("online", cb);
  window.addEventListener("offline", cb);
  return () => {
    window.removeEventListener("online", cb);
    window.removeEventListener("offline", cb);
  };
}

export function useOnline() {
  return useSyncExternalStore(subscribeOnline, () => navigator.onLine, () => true);
}

/** Flush whenever the connection comes back, and every 30 seconds while anything is waiting. */
export function startBackgroundSync(onSynced?: (out: Schemas["BulkOut"]) => void) {
  const run = () =>
    flush().then((out) => {
      if (out) onSynced?.(out);
    });
  window.addEventListener("online", run);
  const t = setInterval(run, 30_000);
  run();
  return () => {
    window.removeEventListener("online", run);
    clearInterval(t);
  };
}
