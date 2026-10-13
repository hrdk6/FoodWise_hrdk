import { AlertTriangle, Loader2, RotateCw } from "lucide-react";
import {
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
  forwardRef,
  useId,
} from "react";

import { ApiError } from "@/api/client";

export function cx(...parts: (string | false | null | undefined)[]) {
  return parts.filter(Boolean).join(" ");
}

type Variant = "primary" | "secondary" | "ghost" | "danger" | "leaf";
type Size = "sm" | "md" | "lg";

const VARIANTS: Record<Variant, string> = {
  primary: "bg-haldi text-ink hover:bg-[#f0ad24] active:bg-[#d79512] border border-[#c98c0f] shadow-[inset_0_1px_0_rgb(255_255_255/0.35)]",
  secondary: "bg-plate text-ink border border-rim hover:border-rim-strong hover:bg-white",
  ghost: "text-ink-2 hover:bg-steel-2 border border-transparent",
  danger: "bg-chilli text-white hover:bg-[#a33224] border border-[#9b2f22]",
  leaf: "bg-leaf text-white hover:bg-[#28603f] border border-[#255a3c]",
};
const SIZES: Record<Size, string> = {
  sm: "h-8 px-3 text-sm gap-1.5",
  md: "h-10 px-4 text-base gap-2",
  lg: "h-13 px-6 text-lg gap-2.5",
};

export const Button = forwardRef<
  HTMLButtonElement,
  ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: Size; busy?: boolean; icon?: ReactNode }
>(function Button({ variant = "secondary", size = "md", busy, icon, className, children, disabled, ...rest }, ref) {
  return (
    <button
      ref={ref}
      className={cx(
        "inline-flex select-none items-center justify-center whitespace-nowrap rounded-[8px] font-medium transition-colors",
        "disabled:cursor-not-allowed disabled:opacity-55",
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      {...rest}
    >
      {busy ? <Loader2 className="size-4 animate-spin" aria-hidden /> : icon}
      {children}
    </button>
  );
});

export function Rim({ className, children, as: As = "div", ...rest }: { as?: "div" | "section" | "article" | "li" } & React.HTMLAttributes<HTMLElement>) {
  return (
    <As className={cx("rim", className)} {...rest}>
      {children}
    </As>
  );
}

type Tone = "neutral" | "haldi" | "leaf" | "chilli" | "ink" | "rain";
const TONES: Record<Tone, string> = {
  neutral: "bg-steel-2 text-ink-2",
  haldi: "bg-haldi-soft text-haldi-deep",
  leaf: "bg-leaf-soft text-leaf",
  chilli: "bg-chilli-soft text-chilli",
  ink: "bg-ink text-steel",
  rain: "bg-[#dfe7ee] text-rain",
};

export function Badge({ tone = "neutral", children, className }: { tone?: Tone; children: ReactNode; className?: string }) {
  return (
    <span className={cx("inline-flex items-center gap-1 rounded-[6px] px-2 py-0.5 text-xs font-medium leading-5", TONES[tone], className)}>
      {children}
    </span>
  );
}

export function Field({
  label,
  hint,
  error,
  children,
  htmlFor,
  className,
}: {
  label: string;
  hint?: ReactNode;
  error?: string;
  children: ReactNode;
  htmlFor?: string;
  className?: string;
}) {
  return (
    <div className={cx("flex flex-col gap-1.5", className)}>
      <label htmlFor={htmlFor} className="text-sm font-medium text-ink-2">
        {label}
      </label>
      {children}
      {error ? <p className="text-sm text-chilli">{error}</p> : hint ? <p className="text-sm text-muted">{hint}</p> : null}
    </div>
  );
}

const inputBase =
  "h-10 w-full rounded-[8px] border border-rim bg-white px-3 text-base text-ink placeholder:text-muted/70 focus:border-ink-2 focus:outline-none focus-visible:outline-2 focus-visible:outline-haldi disabled:bg-steel-2";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input({ className, ...rest }, ref) {
  return <input ref={ref} className={cx(inputBase, className)} {...rest} />;
});

export function Select({ className, children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select className={cx(inputBase, "appearance-none bg-[url('data:image/svg+xml;utf8,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%2212%22 height=%2212%22 viewBox=%220 0 12 12%22><path d=%22M2 4l4 4 4-4%22 fill=%22none%22 stroke=%22%233a4763%22 stroke-width=%221.6%22/></svg>')] bg-[right_0.75rem_center] bg-no-repeat pr-8", className)} {...rest}>
      {children}
    </select>
  );
}

export function Textarea({ className, ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cx(inputBase, "h-auto min-h-20 py-2 leading-normal", className)} {...rest} />;
}

export function Switch({ checked, onChange, label, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean }) {
  const id = useId();
  return (
    <label htmlFor={id} className={cx("inline-flex cursor-pointer items-center gap-2.5 text-base", disabled && "opacity-50")}>
      <button
        id={id}
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cx("relative h-6 w-10 rounded-full border transition-colors", checked ? "border-leaf bg-leaf" : "border-rim-strong bg-steel-2")}
      >
        <span className={cx("absolute top-0.5 size-[18px] rounded-full bg-white shadow-sm transition-transform", checked ? "translate-x-[18px]" : "translate-x-0.5")} />
      </button>
      {label}
    </label>
  );
}

export function Segmented<T extends string>({
  value,
  onChange,
  options,
  size = "md",
  label,
}: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: ReactNode }[];
  size?: "sm" | "md";
  label: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex rounded-[9px] border border-rim bg-steel-2 p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          onClick={() => onChange(o.value)}
          className={cx(
            "rounded-[7px] font-medium transition-colors",
            size === "sm" ? "h-7 px-2.5 text-sm" : "h-8 px-3.5 text-base",
            value === o.value ? "bg-plate text-ink shadow-[0_1px_0_rgb(28_42_74/0.08)] ring-1 ring-rim" : "text-muted hover:text-ink",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-muted" role="status">
      <Loader2 className="size-4 animate-spin" aria-hidden />
      <span className="text-sm">{label}</span>
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cx("animate-pulse rounded-md bg-steel-2", className)} aria-hidden />;
}

export function PageHeader({ title, sub, actions, children }: { title: ReactNode; sub?: ReactNode; actions?: ReactNode; children?: ReactNode }) {
  return (
    <header className="mb-6 flex flex-col gap-3">
      <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
        <div className="min-w-0">
          <h1 className="text-2xl font-[680] text-ink sm:text-3xl">{title}</h1>
          {sub ? <div className="mt-1.5 text-base text-muted">{sub}</div> : null}
        </div>
        {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
      {children}
    </header>
  );
}

export function Section({ title, sub, actions, children, className }: { title: ReactNode; sub?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cx("mb-8", className)}>
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <div>
          <h2 className="text-lg font-[650]">{title}</h2>
          {sub ? <p className="mt-0.5 text-sm text-muted">{sub}</p> : null}
        </div>
        {actions}
      </div>
      {children}
    </section>
  );
}

export function EmptyState({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="rim-sunken flex flex-col items-start gap-2 px-5 py-6">
      <p className="text-lg font-[620] text-ink">{title}</p>
      {children ? <div className="max-w-prose text-base text-muted">{children}</div> : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}

export function ErrorState({ error, retry }: { error: unknown; retry?: () => void }) {
  const msg = error instanceof ApiError ? error.message : "Something went wrong loading this.";
  return (
    <div role="alert" className="flex items-start gap-3 rounded-[10px] border border-chilli/30 bg-chilli-soft/60 px-4 py-3 text-ink">
      <AlertTriangle className="mt-0.5 size-5 shrink-0 text-chilli" aria-hidden />
      <div className="flex-1">
        <p className="font-medium">{msg}</p>
        {error instanceof ApiError && error.code === "offline" ? <p className="text-sm text-ink-2">Changes you make now may not be saved.</p> : null}
      </div>
      {retry ? (
        <Button size="sm" variant="ghost" onClick={retry} icon={<RotateCw className="size-3.5" />}>
          Try again
        </Button>
      ) : null}
    </div>
  );
}

export function Meter({ value, tone = "leaf", label }: { value: number; tone?: "leaf" | "haldi" | "chilli" | "ink"; label: string }) {
  const color = { leaf: "bg-leaf", haldi: "bg-haldi", chilli: "bg-chilli", ink: "bg-ink" }[tone];
  return (
    <div className="h-2 w-full overflow-hidden rounded-full bg-steel-2" role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(value * 100)}>
      <div className={cx("h-full rounded-full transition-[width] duration-500", color)} style={{ width: `${Math.max(2, Math.min(100, value * 100))}%` }} />
    </div>
  );
}

export function Tabs<T extends string>({ value, onChange, tabs }: { value: T; onChange: (v: T) => void; tabs: { value: T; label: ReactNode; count?: number }[] }) {
  return (
    <div role="tablist" className="-mb-px flex gap-1 overflow-x-auto border-b border-rim">
      {tabs.map((t) => (
        <button
          key={t.value}
          role="tab"
          aria-selected={value === t.value}
          onClick={() => onChange(t.value)}
          className={cx(
            "flex h-10 items-center gap-2 whitespace-nowrap border-b-2 px-3 text-base font-medium transition-colors",
            value === t.value ? "border-haldi text-ink" : "border-transparent text-muted hover:text-ink",
          )}
        >
          {t.label}
          {t.count !== undefined ? <span className="num rounded-full bg-steel-2 px-1.5 text-xs text-ink-2">{t.count}</span> : null}
        </button>
      ))}
    </div>
  );
}

export function KeyValue({ items }: { items: [ReactNode, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[minmax(0,auto)_1fr] gap-x-6 gap-y-2 text-base">
      {items.map(([k, v], i) => (
        <div key={i} className="contents">
          <dt className="text-muted">{k}</dt>
          <dd className="num text-ink">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

/** A number with a small label beneath, for the few places a figure really is the point. */
export function Figure({ value, label, tone }: { value: ReactNode; label: ReactNode; tone?: "leaf" | "chilli" | "haldi" }) {
  const color = tone === "leaf" ? "text-leaf" : tone === "chilli" ? "text-chilli" : tone === "haldi" ? "text-haldi-deep" : "text-ink";
  return (
    <div className="min-w-0">
      <div className={cx("qty text-2xl", color)}>{value}</div>
      <div className="mt-1 text-sm text-muted">{label}</div>
    </div>
  );
}
