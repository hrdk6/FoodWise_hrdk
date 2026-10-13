import "./styles/index.css";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { ApiError } from "./api/client";
import { App } from "./app/App";
import { AuthProvider } from "./app/auth";
import { startVitals } from "./lib/vitals";
import { ToastProvider } from "./ui/overlays";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: (count, error) => count < 2 && (!(error instanceof ApiError) || error.status === 0 || error.status >= 500),
    },
  },
});

startVitals();

// After a redeploy an open tab can ask for a page chunk that no longer exists. Reload once to pick
// up the new build instead of leaving a blank screen; the guard stops a reload loop.
window.addEventListener("vite:preloadError", (event) => {
  try {
    const last = Number(sessionStorage.getItem("fw-chunk-reload") ?? 0);
    if (Date.now() - last < 10_000) return;
    sessionStorage.setItem("fw-chunk-reload", String(Date.now()));
  } catch {
    // Storage unavailable: still reload once for this event.
  }
  event.preventDefault();
  window.location.reload();
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <AuthProvider>
          <App />
        </AuthProvider>
      </ToastProvider>
    </QueryClientProvider>
  </StrictMode>,
);
