import { getAccessToken, refreshSession } from "@/api/client";

/**
 * Server-sent events over fetch, so the request can carry the Authorization header
 * (EventSource cannot). Reconnects with backoff until the signal aborts.
 */
export async function subscribe(path: string, onEvent: (event: string, data: unknown) => void, signal: AbortSignal) {
  let delay = 1000;
  while (!signal.aborted) {
    try {
      let res = await fetch(path, { headers: { Authorization: `Bearer ${getAccessToken() ?? ""}`, Accept: "text/event-stream" }, signal });
      if (res.status === 401 && (await refreshSession())) {
        res = await fetch(path, { headers: { Authorization: `Bearer ${getAccessToken() ?? ""}`, Accept: "text/event-stream" }, signal });
      }
      if (!res.ok || !res.body) throw new Error(`stream ${res.status}`);
      delay = 1000;
      const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += value;
        let idx: number;
        while ((idx = buffer.indexOf("\n\n")) >= 0) {
          const chunk = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          let event = "message";
          const data: string[] = [];
          for (const line of chunk.split("\n")) {
            if (line.startsWith("event:")) event = line.slice(6).trim();
            else if (line.startsWith("data:")) data.push(line.slice(5).trim());
          }
          if (data.length) {
            try {
              onEvent(event, JSON.parse(data.join("\n")));
            } catch {
              /* ignore malformed */
            }
          }
        }
      }
    } catch {
      if (signal.aborted) return;
    }
    await new Promise((r) => setTimeout(r, delay));
    delay = Math.min(15000, delay * 2);
  }
}
