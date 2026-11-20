import { useState, useSyncExternalStore, type ReactNode } from "react";
import { NavLink, useLocation, useNavigate, useParams } from "react-router-dom";
import { modeStore } from "@/api/client";
import { useProjects } from "@/api/hooks";
import { cx } from "./ui";

const ICONS: Record<string, string> = {
  portfolio: "M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z",
  dashboard: "M4 20V10M10 20V4M16 20v-8M22 20H2",
  viewer: "M12 3 3 8v8l9 5 9-5V8zM3 8l9 5 9-5M12 13v8",
  model: "M3 21h18M5 21V9l7-5 7 5v12M9 21v-6h6v6M9 11h6",
  verify: "M9 12l2 2 4-4M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z",
  elements: "M4 6h16M4 12h16M4 18h16",
  schedule: "M4 5h16v15H4zM4 10h16M9 3v4M15 3v4",
  qc: "M12 3 2 20h20zM12 10v4M12 17v.5",
  uploads: "M12 16V4M7 9l5-5 5 5M4 20h16",
  settings: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M19 5l-2 2M7 17l-2 2",
};
function Icon({ name }: { name: string }) {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden className="shrink-0">
      <path d={ICONS[name]} />
    </svg>
  );
}

const PROJECT_NAV = [
  ["", "dashboard", "Dashboard"],
  ["model", "model", "3D model"],
  ["viewer", "viewer", "Reality viewer"],
  ["verify", "verify", "Verification"],
  ["elements", "elements", "Elements"],
  ["schedule", "schedule", "Schedule & EVM"],
  ["qc", "qc", "QC issues"],
  ["uploads", "uploads", "Uploads"],
  ["settings", "settings", "Integrations"],
] as const;

export default function Layout({ children }: { children: ReactNode }) {
  const { pid } = useParams();
  const loc = useLocation();
  const m = loc.pathname.match(/^\/p\/([^/]+)/);
  const projectId = pid ?? m?.[1];
  const mode = useSyncExternalStore(modeStore.subscribe, modeStore.get);
  const projects = useProjects();
  const nav = useNavigate();
  const [open, setOpen] = useState(false);
  const current = projects.data?.find((p) => p.id === projectId);
  const sub = loc.pathname.replace(/^\/p\/[^/]+\/?/, "").split("/")[0];

  const link = (to: string, icon: string, label: string, end = false) => (
    <NavLink
      key={to} to={to} end={end} onClick={() => setOpen(false)} title={label}
      className={({ isActive }) => cx("flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium outline-offset-0", isActive ? "bg-sky-500/20 text-white" : "text-slate-300 hover:bg-slate-800 hover:text-white")}
    >
      <Icon name={icon} />
      <span className="md:hidden lg:inline">{label}</span>
    </NavLink>
  );

  return (
    <div className="flex h-full">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:z-50 focus:bg-white focus:p-2">Skip to content</a>
      <aside className={cx("z-30 flex w-56 shrink-0 flex-col bg-slate-900 text-slate-200 md:w-14 lg:w-56", "max-md:fixed max-md:inset-y-0 max-md:transition-transform", open ? "max-md:translate-x-0" : "max-md:-translate-x-full")} aria-label="Primary">
        <div className="flex h-14 items-center gap-2 border-b border-slate-800 px-4 md:justify-center lg:justify-start">
          <svg width="24" height="24" viewBox="0 0 32 32" aria-hidden><rect width="32" height="32" rx="7" fill="#0f172a" stroke="#334155" /><path d="M9 9h14v4H14v3h8v4h-8v3H9z" fill="#22c55e" /></svg>
          <span className="text-base font-semibold tracking-tight text-white md:hidden lg:inline">Fauxel</span>
        </div>
        <nav className="flex-1 space-y-1 overflow-y-auto p-2">
          {link("/", "portfolio", "Portfolio", true)}
          {projectId && (
            <>
              <div className="px-3 pb-1 pt-4 text-[10px] font-semibold uppercase tracking-wider text-slate-500 md:hidden lg:block">{current?.name ?? "Project"}</div>
              {PROJECT_NAV.map(([p, icon, label]) => link(`/p/${projectId}${p ? "/" + p : ""}`, icon, label, p === ""))}
            </>
          )}
          {!projectId && link("/settings", "settings", "Settings")}
        </nav>
        <div className="border-t border-slate-800 p-3 text-[11px] text-slate-500 md:hidden lg:block">Open source · Apache-2.0</div>
      </aside>
      {open && <div className="fixed inset-0 z-20 bg-black/40 md:hidden" onClick={() => setOpen(false)} aria-hidden />}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center gap-3 border-b border-slate-200 bg-white px-4">
          <button type="button" className="rounded-lg p-1.5 text-slate-600 hover:bg-slate-100 md:hidden" onClick={() => setOpen(true)} aria-label="Open navigation">
            <Icon name="elements" />
          </button>
          <label className="flex items-center gap-2 text-sm text-slate-500">
            <span className="sr-only">Project</span>
            <select
              aria-label="Switch project"
              className="max-w-[220px] rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-sm font-medium text-slate-800"
              value={projectId ?? ""}
              onChange={(e) => (e.target.value ? nav(`/p/${e.target.value}${sub ? "/" + sub : ""}`) : nav("/"))}
            >
              <option value="">All projects</option>
              {projects.data?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </label>
          <div className="ml-auto flex items-center gap-2">
            {mode === "mock" && (
              <span className="rounded-full border border-amber-300 bg-amber-100 px-2.5 py-0.5 text-xs font-semibold text-amber-800" title="No API reachable (or VITE_FAUXEL_MOCK=1): showing generated sample data" data-testid="demo-badge">
                Demo data
              </span>
            )}
            {mode === "live" && <span className="rounded-full border border-emerald-300 bg-emerald-50 px-2.5 py-0.5 text-xs font-semibold text-emerald-700">Live API</span>}
          </div>
        </header>
        <main id="main" className="min-h-0 flex-1 overflow-y-auto scroll-thin p-4 lg:p-6">{children}</main>
      </div>
    </div>
  );
}
