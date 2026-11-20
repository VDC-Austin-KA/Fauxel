import { useId, type ButtonHTMLAttributes, type ReactNode } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import { STATE_LABELS, TRADE_COLORS, TRADE_LABELS, percentColor, statusColor } from "@/lib/status";
import type { InstallState, Trade } from "@/lib/types";

export function cx(...a: (string | false | null | undefined)[]) { return a.filter(Boolean).join(" "); }

export function Card({ title, actions, children, className, pad = true }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; pad?: boolean }) {
  return (
    <section className={cx("rounded-xl border border-slate-200 bg-white shadow-sm", className)}>
      {(title || actions) && (
        <header className="flex items-center justify-between gap-2 border-b border-slate-100 px-4 py-2.5">
          <h2 className="text-sm font-semibold text-slate-700">{title}</h2>
          <div className="flex items-center gap-2">{actions}</div>
        </header>
      )}
      <div className={pad ? "p-4" : ""}>{children}</div>
    </section>
  );
}

export function Kpi({ label, value, sub, tone, children }: { label: string; value: ReactNode; sub?: ReactNode; tone?: "good" | "warn" | "bad"; children?: ReactNode }) {
  const color = tone === "good" ? "text-emerald-600" : tone === "warn" ? "text-amber-600" : tone === "bad" ? "text-red-600" : "text-slate-900";
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm" role="group" aria-label={label}>
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</div>
      <div className={cx("tabnum mt-1 text-2xl font-semibold", color)}>{value}</div>
      {sub && <div className="mt-0.5 text-xs text-slate-500">{sub}</div>}
      {children}
    </div>
  );
}

export function Button({ variant = "default", className, ...p }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "default" | "primary" | "danger" | "ghost" }) {
  const v = {
    default: "border border-slate-300 bg-white text-slate-700 hover:bg-slate-50",
    primary: "bg-sky-600 text-white hover:bg-sky-700 border border-sky-600",
    danger: "bg-red-600 text-white hover:bg-red-700 border border-red-600",
    ghost: "text-slate-600 hover:bg-slate-100 border border-transparent",
  }[variant];
  return <button type="button" {...p} className={cx("inline-flex items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50", v, className)} />;
}

export function Chip({ color, children, title }: { color: string; children: ReactNode; title?: string }) {
  return (
    <span title={title} className="inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium" style={{ borderColor: color + "66", background: color + "1f", color: "#1e293b" }}>
      <span className="h-2 w-2 rounded-full" style={{ background: color }} aria-hidden />
      {children}
    </span>
  );
}
export const StateChip = ({ state, percent }: { state: InstallState; percent?: number }) => <Chip color={statusColor(state, percent)}>{STATE_LABELS[state]}</Chip>;
export const TradeChip = ({ trade }: { trade: Trade }) => <Chip color={TRADE_COLORS[trade]}>{TRADE_LABELS[trade]}</Chip>;

export function ProgressBar({ value, planned, label }: { value: number; planned?: number; label?: string }) {
  return (
    <div className="relative h-2.5 w-full overflow-hidden rounded-full bg-slate-200" role="progressbar" aria-valuenow={Math.round(value)} aria-valuemin={0} aria-valuemax={100} aria-label={label}>
      <div className="h-full rounded-full" style={{ width: `${Math.min(100, value)}%`, background: percentColor(Math.max(value, 1)) }} />
      {planned != null && <div className="absolute top-0 h-full w-0.5 bg-slate-700" style={{ left: `${Math.min(100, planned)}%` }} title={`Planned ${planned.toFixed(0)}%`} />}
    </div>
  );
}

export function Spinner({ className }: { className?: string }) {
  return <span className={cx("inline-block h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-sky-600", className)} aria-hidden />;
}
export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex h-48 items-center justify-center gap-2 text-sm text-slate-500" role="status" aria-live="polite">
      <Spinner /> {label}…
    </div>
  );
}
export function Skeleton({ className }: { className?: string }) { return <div className={cx("animate-pulse rounded-lg bg-slate-200", className)} />; }

export function EmptyState({ title, hint, action }: { title: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-1 rounded-xl border border-dashed border-slate-300 bg-white/60 px-6 py-10 text-center">
      <div className="text-sm font-semibold text-slate-700">{title}</div>
      {hint && <div className="max-w-md text-xs text-slate-500">{hint}</div>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}
export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  return (
    <div role="alert" className="flex flex-col items-start gap-2 rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">
      <div className="font-semibold">Something went wrong</div>
      <div className="text-xs">{error instanceof Error ? error.message : String(error)}</div>
      {onRetry && <Button onClick={onRetry}>Retry</Button>}
    </div>
  );
}

/** Renders loading / error / empty states for a query, then the data. */
export function Q<T>({ q, children, empty, skeleton }: { q: UseQueryResult<T>; children: (d: T) => ReactNode; empty?: (d: T) => boolean; skeleton?: ReactNode }) {
  if (q.isPending) return <>{skeleton ?? <Loading />}</>;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  if (empty?.(q.data)) return <EmptyState title="Nothing here yet" hint="Upload data or adjust your filters." />;
  return <>{children(q.data)}</>;
}

export function PageHeader({ title, sub, actions }: { title: string; sub?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold text-slate-900">{title}</h1>
        {sub && <p className="text-sm text-slate-500">{sub}</p>}
      </div>
      <div className="flex flex-wrap items-center gap-2">{actions}</div>
    </div>
  );
}

export function Select({ label, value, onChange, options, className }: { label: string; value: string; onChange: (v: string) => void; options: { value: string; label: string }[]; className?: string }) {
  const id = useId();
  return (
    <label htmlFor={id} className={cx("flex items-center gap-1.5 text-xs text-slate-500", className)}>
      <span>{label}</span>
      <select id={id} value={value} onChange={(e) => onChange(e.target.value)} className="rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-sm text-slate-800">
        {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </label>
  );
}

export function Sparkline({ values, color = "#0ea5e9", w = 120, h = 32 }: { values: number[]; color?: string; w?: number; h?: number }) {
  if (values.length < 2) return <svg width={w} height={h} aria-hidden />;
  const min = Math.min(...values), max = Math.max(...values), span = max - min || 1;
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * (w - 2) + 1},${h - 3 - ((v - min) / span) * (h - 6)}`);
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} role="img" aria-label="trend sparkline">
      <polyline points={pts.join(" ")} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={pts[pts.length - 1].split(",")[0]} cy={pts[pts.length - 1].split(",")[1]} r={2.5} fill={color} />
    </svg>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="rounded border border-slate-300 bg-slate-100 px-1.5 py-0.5 font-mono text-[10px] text-slate-600">{children}</kbd>;
}
