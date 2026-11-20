import { Link } from "react-router-dom";
import { isMock } from "@/api/client";
import { useElements } from "@/api/hooks";
import { Chip, TradeChip } from "./ui";
import { useLocalState } from "@/lib/hooks";
import { formatMoney } from "@/lib/evm";
import type { Trade, TradeProgress } from "@/lib/types";

const SUBS: Partial<Record<Trade, string>> = {
  structural: "Lone Star Concrete & Steel", arch_interior: "Hill Country Interiors", hvac: "Capitol Mechanical", electrical: "Brazos Electric", plumbing_fp: "Colorado River Plumbing & FP",
};

function Row({ pid, t, value }: { pid: string; t: TradeProgress; value: number }) {
  const els = useElements(pid, { trade: t.trade, limit: 200 });
  const best = els.data?.items.filter((e) => e.evidence_frame_id && e.percent_complete > 0).sort((a, b) => b.percent_complete - a.percent_complete)[0];
  const [claimed, setClaimed] = useLocalState<number>(`fauxel.claim.${pid}.${t.trade}`, Math.min(100, Math.round(t.percent_complete + (isMock() ? ({ structural: 2, arch_interior: 14, hvac: 9, electrical: 4, plumbing_fp: 12, other: 0 }[t.trade] ?? 0) : 0))));
  const delta = claimed - t.percent_complete;
  const flag = delta > 5 ? "over" : delta < -5 ? "under" : "ok";
  return (
    <tr className="border-t border-slate-100">
      <td className="py-2 pr-3"><TradeChip trade={t.trade} /></td>
      <td className="py-2 pr-3 text-slate-600">{SUBS[t.trade] ?? "—"}</td>
      <td className="tabnum py-2 pr-3 text-right text-slate-600">{formatMoney(value)}</td>
      <td className="py-2 pr-3 text-right">
        <label className="sr-only" htmlFor={`claim-${t.trade}`}>{t.trade} percent claimed</label>
        <input id={`claim-${t.trade}`} type="number" min={0} max={100} value={claimed} onChange={(e) => setClaimed(Math.max(0, Math.min(100, Number(e.target.value) || 0)))} className="tabnum w-16 rounded-md border border-slate-300 px-1.5 py-1 text-right text-sm" />
      </td>
      <td className="tabnum py-2 pr-3 text-right font-medium">{t.percent_complete.toFixed(0)}%</td>
      <td className="tabnum py-2 pr-3 text-right">{delta > 0 ? "+" : ""}{delta.toFixed(0)}</td>
      <td className="py-2 pr-3">
        {flag === "over" ? <Chip color="#ef4444">Over-claimed {formatMoney((delta / 100) * value)}</Chip> : flag === "under" ? <Chip color="#eab308">Under-claimed</Chip> : <Chip color="#22c55e">Verified</Chip>}
      </td>
      <td className="py-2 text-right">
        {best ? <Link className="text-sky-700 hover:underline" to={`/p/${pid}/viewer?element=${best.element_uid}`}>360° evidence</Link> : <span className="text-slate-400">—</span>}
      </td>
    </tr>
  );
}

export default function PayApps({ pid, trades, budgets }: { pid: string; trades: TradeProgress[]; budgets: Partial<Record<Trade, number>> }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[720px] text-sm">
        <caption className="sr-only">Subcontractor pay application verification</caption>
        <thead>
          <tr className="text-left text-xs uppercase tracking-wide text-slate-500">
            <th className="pb-2 pr-3 font-medium">Trade</th><th className="pb-2 pr-3 font-medium">Subcontractor</th><th className="pb-2 pr-3 text-right font-medium">Contract</th>
            <th className="pb-2 pr-3 text-right font-medium">% claimed</th><th className="pb-2 pr-3 text-right font-medium">% verified</th><th className="pb-2 pr-3 text-right font-medium">Δ pts</th><th className="pb-2 pr-3 font-medium">Flag</th><th className="pb-2 text-right font-medium">Evidence</th>
          </tr>
        </thead>
        <tbody>{trades.filter((t) => t.trade !== "other").map((t) => <Row key={t.trade} pid={pid} t={t} value={budgets[t.trade] ?? 0} />)}</tbody>
      </table>
      <p className="mt-2 text-xs text-slate-500">Claimed percentages are editable and kept in this browser only; verified percentages come from reality capture.</p>
    </div>
  );
}
