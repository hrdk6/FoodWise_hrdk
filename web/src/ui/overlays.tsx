import { CheckCircle2, Info, X, XCircle } from "lucide-react";
import { type ReactNode, createContext, useCallback, useContext, useEffect, useRef, useState } from "react";

import { minutesUntil } from "@/lib/format";

import { cx } from "./primitives";

/** Accessible modal built on <dialog>: focus trap, Escape to close, and a backdrop. */
export function Dialog({
  open,
  onClose,
  title,
  children,
  footer,
  wide,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog
      ref={ref}
      onClose={onClose}
      onClick={(e) => {
        if (e.target === ref.current) onClose();
      }}
      className={cx(
        "m-auto w-[calc(100%-2rem)] rounded-[14px] border border-rim bg-plate p-0 text-ink shadow-[0_18px_50px_rgb(28_42_74/0.25)] backdrop:bg-ink/35",
        wide ? "max-w-3xl" : "max-w-lg",
      )}
    >
      <div className="flex items-start justify-between gap-4 border-b border-rim px-5 py-4">
        <h2 className="text-lg font-[650]">{title}</h2>
        <button onClick={onClose} className="-mr-1 rounded-md p-1 text-muted hover:bg-steel-2 hover:text-ink" aria-label="Close">
          <X className="size-5" />
        </button>
      </div>
      <div className="max-h-[70vh] overflow-y-auto px-5 py-4">{children}</div>
      {footer ? <div className="flex flex-wrap justify-end gap-2 border-t border-rim px-5 py-3">{footer}</div> : null}
    </dialog>
  );
}

type Toast = { id: number; tone: "ok" | "error" | "info"; message: string };
const ToastCtx = createContext<(tone: Toast["tone"], message: string) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((tone: Toast["tone"], message: string) => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t.slice(-3), { id, tone, message }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), tone === "error" ? 7000 : 4000);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div aria-live="polite" className="pointer-events-none fixed inset-x-0 bottom-20 z-50 flex flex-col items-center gap-2 px-4 sm:bottom-6">
        {toasts.map((t) => (
          <div
            key={t.id}
            role={t.tone === "error" ? "alert" : "status"}
            className="animate-ladle pointer-events-auto flex max-w-md items-start gap-2.5 rounded-[10px] bg-ink px-4 py-3 text-base text-steel shadow-[0_10px_30px_rgb(28_42_74/0.3)]"
          >
            {t.tone === "ok" ? <CheckCircle2 className="mt-0.5 size-4.5 shrink-0 text-[#8fd1a8]" /> : t.tone === "error" ? <XCircle className="mt-0.5 size-4.5 shrink-0 text-[#f2a093]" /> : <Info className="mt-0.5 size-4.5 shrink-0 text-haldi" />}
            <span>{t.message}</span>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export function useToast() {
  return useContext(ToastCtx);
}

/** Minutes left until a deadline, re-rendering every 20 seconds. Chilli when under 30 minutes. */
export function Countdown({ until, prefix = "", passed = "Window passed", className }: { until: string | null | undefined; prefix?: string; passed?: string; className?: string }) {
  const [, tick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => tick((n) => n + 1), 20_000);
    return () => clearInterval(t);
  }, []);
  const m = minutesUntil(until);
  if (m === null) return null;
  const urgent = m < 30;
  const text = m <= 0 ? passed : m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`;
  return (
    <span className={cx("num font-medium", m <= 0 ? "text-muted" : urgent ? "text-chilli" : "text-ink", className)}>
      {m > 0 ? prefix : ""}
      {text}
    </span>
  );
}
