import type { InstallState, Severity, Trade } from "./types";

/** Status colors from docs/specs/platform-contract.md. */
export const STATUS_COLORS = {
  installed: "#22c55e",
  inProgress: "#eab308",
  deviation: "#ef4444",
  notStarted: "#9ca3af",
} as const;

export type StatusKind = keyof typeof STATUS_COLORS;

export function statusKind(state: InstallState, percent = 0): StatusKind {
  if (state === "DEVIATION_FLAGGED") return "deviation";
  if (state === "INSTALLED" || state === "INSULATED_OR_FINISHED") return "installed";
  if (state === "FRAMED_OR_ROUGHED") return "inProgress";
  if (percent >= 100) return "installed";
  if (percent >= 1) return "inProgress";
  return "notStarted";
}

export function statusColor(state: InstallState, percent = 0): string {
  return STATUS_COLORS[statusKind(state, percent)];
}

export const STATE_LABELS: Record<InstallState, string> = {
  NOT_STARTED: "Not started",
  FRAMED_OR_ROUGHED: "Framed / roughed",
  INSTALLED: "Installed",
  INSULATED_OR_FINISHED: "Insulated / finished",
  DEVIATION_FLAGGED: "Deviation",
};

export const TRADE_LABELS: Record<Trade, string> = {
  structural: "Structural",
  arch_interior: "Framing / Drywall",
  hvac: "HVAC",
  electrical: "Electrical",
  plumbing_fp: "Plumbing / FP",
  other: "Other",
};

export const TRADE_COLORS: Record<Trade, string> = {
  structural: "#64748b",
  arch_interior: "#0ea5e9",
  hvac: "#14b8a6",
  electrical: "#f59e0b",
  plumbing_fp: "#8b5cf6",
  other: "#a8a29e",
};

export const SEVERITY_COLORS: Record<Severity, string> = {
  low: "#64748b",
  medium: "#eab308",
  high: "#f97316",
  critical: "#ef4444",
};

function hex(c: string): [number, number, number] {
  const n = parseInt(c.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
function mix(a: string, b: string, t: number): string {
  const [ar, ag, ab] = hex(a);
  const [br, bg, bb] = hex(b);
  const f = (x: number, y: number) => Math.round(x + (y - x) * t).toString(16).padStart(2, "0");
  return `#${f(ar, br)}${f(ag, bg)}${f(ab, bb)}`;
}

/** Continuous heatmap color: 0% = not-started gray, then yellow, then installed green at 100%. */
export function percentColor(pct: number): string {
  const p = Math.max(0, Math.min(100, pct));
  if (p <= 0) return STATUS_COLORS.notStarted;
  if (p < 50) return mix(STATUS_COLORS.notStarted, STATUS_COLORS.inProgress, 0.35 + (p / 50) * 0.65);
  return mix(STATUS_COLORS.inProgress, STATUS_COLORS.installed, (p - 50) / 50);
}

/** Pick readable text (dark/light) for a background color. */
export function textOn(bg: string): string {
  const [r, g, b] = hex(bg);
  return 0.299 * r + 0.587 * g + 0.114 * b > 150 ? "#0f172a" : "#ffffff";
}
