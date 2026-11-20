"""Progress analytics, EVM/forecast glue (engine in fauxel_api.analytics), write-back, TimeLiner, CSV."""
from __future__ import annotations

import csv
import io
from datetime import date, datetime, timezone

from .domain import load_rules, status_color

VERIFIED_CONFIDENCE = 0.8


def evidence_url(base: str, frame_id) -> str | None:
    return f"{base}/api/v1/frames/{frame_id}/image?w=1024" if frame_id else None


# ---------- schedule / EVM inputs ----------

def load_activities(conn, pid) -> list[dict]:
    rows = conn.execute(
        "SELECT a.*, COALESCE(a.zone_name, z.name) AS zone_label, "
        "COALESCE(array_agg(ae.element_uid::text) FILTER (WHERE ae.element_uid IS NOT NULL), '{}') AS element_uids "
        "FROM schedule_activities a LEFT JOIN zones z ON z.id = a.zone_id "
        "LEFT JOIN activity_elements ae ON ae.activity_id = a.id WHERE a.project_id = %s "
        "GROUP BY a.id, z.name ORDER BY a.planned_start NULLS LAST, a.external_id", (pid,)).fetchall()
    for r in rows:
        r["zone_name"] = r.pop("zone_label")
    return rows


def element_percent(conn, pid) -> dict[str, float]:
    return {str(r["element_uid"]): r["p"] for r in conn.execute(
        "SELECT e.element_uid, COALESCE(s.percent_complete, 0) AS p FROM bim_elements e "
        "LEFT JOIN element_status s USING (element_uid) WHERE e.project_id = %s", (pid,))}


def element_history(conn, pid) -> list[dict]:
    return [{"date": r["d"], "element_uid": str(r["element_uid"]), "percent": r["p"]} for r in conn.execute(
        "SELECT o.element_uid, o.observed_at::date AS d, max(o.percent) AS p FROM element_observations o "
        "JOIN bim_elements e USING (element_uid) WHERE e.project_id = %s AND o.percent IS NOT NULL "
        "GROUP BY 1, 2 ORDER BY 2", (pid,))]


def today() -> date:
    return datetime.now(timezone.utc).date()


def evm(conn, pid, scope: str = "project") -> list[dict]:
    from .analytics.evm import compute_evm
    acts = load_activities(conn, pid)
    if not acts:
        return []
    return compute_evm(acts, element_percent(conn, pid), today(), scope)


def snapshot_evm(conn, pid) -> None:
    """Persist project + trade EVM snapshots (called after each processed capture)."""
    for scope in ("project", "trade"):
        for r in evm(conn, pid, scope):
            conn.execute(
                "INSERT INTO evm_snapshots (project_id, as_of, scope, scope_key, pv, ev, ac, spi, cpi) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (pid, today(), r["scope"], r["scope_key"], r["pv"], r["ev"], r["ac"], r["spi"], r["cpi"]))


def forecast(conn, pid) -> dict:
    from .analytics.evm import forecast as fc
    acts = load_activities(conn, pid)
    if not acts:
        return {"projected_finish": None, "activities": [], "stacking_alerts": []}
    return fc(acts, element_history(conn, pid), today())


# ---------- progress ----------

def summary(conn, pid) -> dict:
    t = conn.execute(
        "SELECT count(*) AS total, COALESCE(avg(COALESCE(s.percent_complete, 0)), 0) AS pct, "
        "count(*) FILTER (WHERE s.last_verified IS NOT NULL AND COALESCE(s.confidence, 0) >= %s) AS verified "
        "FROM bim_elements e LEFT JOIN element_status s USING (element_uid) WHERE e.project_id = %s",
        (VERIFIED_CONFIDENCE, pid)).fetchone()
    dev = conn.execute("SELECT count(*) AS n FROM deviations WHERE project_id = %s AND status = 'open'", (pid,)).fetchone()["n"]
    out = {"percent_complete": round(float(t["pct"]), 2), "spi": None, "cpi": None, "ev": None, "pv": None,
           "open_deviations": dev, "projected_finish": None, "elements_total": t["total"], "elements_verified": t["verified"]}
    try:
        proj = evm(conn, pid, "project")
        if proj:
            out.update({k: proj[0][k] for k in ("spi", "cpi", "ev", "pv")})
            pf = forecast(conn, pid)["projected_finish"]
            out["projected_finish"] = pf.isoformat() if pf else None
    except ImportError:
        pass  # analytics engine not installed
    return out


def breakdown(conn, pid, by: str) -> list[dict]:
    if by == "trade":
        sel, join, grp = "e.trade AS key, e.trade AS name", "", "e.trade"
    else:
        sel, join, grp = "z.id::text AS key, COALESCE(z.name, 'unzoned') AS name", "LEFT JOIN zones z ON z.id = e.zone_id", "z.id, z.name"
    rows = conn.execute(
        f"SELECT {sel}, count(*) AS elements, COALESCE(avg(COALESCE(s.percent_complete, 0)), 0) AS percent_complete, "
        "count(*) FILTER (WHERE s.state IN ('INSTALLED','INSULATED_OR_FINISHED')) AS installed, "
        "count(*) FILTER (WHERE s.state = 'FRAMED_OR_ROUGHED' OR (s.percent_complete > 0 AND s.percent_complete < 100 "
        "AND s.state NOT IN ('INSTALLED','INSULATED_OR_FINISHED'))) AS in_progress, "
        "count(*) FILTER (WHERE s.state IS NULL OR s.state = 'NOT_STARTED') AS not_started "
        f"FROM bim_elements e LEFT JOIN element_status s USING (element_uid) {join} WHERE e.project_id = %s "
        f"GROUP BY {grp} ORDER BY name", (pid,)).fetchall()
    for r in rows:
        r["percent_complete"] = round(float(r["percent_complete"]), 2)
        if by == "trade":
            r["trade"] = r.pop("key")
            r.pop("name")
        else:
            r["zone_id"], r["zone"] = r.pop("key"), r.pop("name")
    return rows


def timeseries(conn, pid) -> list[dict]:
    hist = element_history(conn, pid)
    acts = load_activities(conn, pid)
    dated = [a for a in acts if a["planned_start"] and a["planned_finish"]]
    if dated:
        from .analytics.evm import s_curve
        start = min(a["planned_start"] for a in dated)
        end = max(a["planned_finish"] for a in dated)
        return s_curve(acts, hist, start, end)
    # no schedule: actual-only curve, mean of each element's latest percent as of each observation day
    total = conn.execute("SELECT count(*) AS n FROM bim_elements WHERE project_id = %s", (pid,)).fetchone()["n"]
    latest: dict[str, float] = {}
    out = []
    for d in sorted({h["date"] for h in hist}):
        for h in (h for h in hist if h["date"] == d):
            latest[h["element_uid"]] = max(latest.get(h["element_uid"], 0.0), h["percent"])
        out.append({"date": d.isoformat(), "planned_pct": None, "actual_pct": round(sum(latest.values()) / max(total, 1), 2)})
    return out


# ---------- write-back / TimeLiner / CSV ----------

def writeback(conn, pid, since: datetime | None, base_url: str = "") -> dict:
    version, _ = load_rules(conn)
    params: list = [pid]
    where = ""
    if since:
        where = ("AND (s.updated_at > %s OR EXISTS (SELECT 1 FROM deviations d WHERE d.element_uid = e.element_uid "
                 "AND d.updated_at > %s))")
        params += [since, since]
    rows = conn.execute(
        "SELECT e.element_uid, e.revit_unique_id, e.ifc_global_id, e.navis_item_guid, "
        "COALESCE(s.state, 'NOT_STARTED') AS state, COALESCE(s.percent_complete, 0) AS percent, s.last_verified, "
        "s.confidence, s.evidence_frame_id, EXISTS (SELECT 1 FROM deviations d WHERE d.element_uid = e.element_uid "
        "AND d.status = 'open') AS dev FROM bim_elements e LEFT JOIN element_status s USING (element_uid) "
        f"WHERE e.project_id = %s {where} ORDER BY e.element_uid", params).fetchall()
    elements = []
    for r in rows:
        state = "DEVIATION_FLAGGED" if r["dev"] else r["state"]
        elements.append({
            "element_uid": str(r["element_uid"]), "revit_unique_id": r["revit_unique_id"], "ifc_global_id": r["ifc_global_id"],
            "navis_item_guid": r["navis_item_guid"], "percent_complete": float(r["percent"]), "status": state,
            "last_verified": r["last_verified"].isoformat() if r["last_verified"] else None,
            "confidence": r["confidence"], "evidence_url": evidence_url(base_url, r["evidence_frame_id"]),
            "color": status_color(state, r["percent"], r["dev"])})
    return {"project_id": str(pid), "generated_at": datetime.now(timezone.utc).isoformat(),
            "rules_version": version, "elements": elements}


def timeliner(conn, pid) -> list[dict]:
    rows = conn.execute(
        "SELECT a.external_id, a.name, a.actual_start, a.actual_finish, a.percent_complete AS stored_pct, "
        "(SELECT count(*) FROM activity_elements ae WHERE ae.activity_id = a.id) AS n, "
        "(SELECT avg(COALESCE(s.percent_complete, 0)) FROM activity_elements ae "
        "   LEFT JOIN element_status s USING (element_uid) WHERE ae.activity_id = a.id) AS pct, "
        "(SELECT min(o.observed_at) FROM activity_elements ae JOIN element_observations o USING (element_uid) "
        "   WHERE ae.activity_id = a.id AND o.percent > 0) AS first_seen, "
        "(SELECT max(o.observed_at) FROM activity_elements ae JOIN element_observations o USING (element_uid) "
        "   WHERE ae.activity_id = a.id) AS last_seen "
        "FROM schedule_activities a WHERE a.project_id = %s ORDER BY a.planned_start NULLS LAST, a.external_id", (pid,)).fetchall()
    out = []
    for r in rows:
        pct = float(r["pct"]) if r["n"] else float(r["stored_pct"] or 0)
        start = r["actual_start"] or (r["first_seen"].date() if r["first_seen"] and pct > 0 else None)
        finish = r["actual_finish"] or (r["last_seen"].date() if r["last_seen"] and pct >= 99.5 else None)
        out.append({"external_id": r["external_id"], "name": r["name"], "actual_start": start.isoformat() if start else None,
                    "actual_finish": finish.isoformat() if finish else None, "percent_complete": round(pct, 2)})
    return out


def activities_view(conn, pid) -> list[dict]:
    ep = element_percent(conn, pid)
    out = []
    for a in load_activities(conn, pid):
        uids = a["element_uids"]  # kept in the response: the portal's model viewer colors elements by their activity's dates
        pct = sum(ep.get(u, 0.0) for u in uids) / len(uids) if uids else float(a["percent_complete"] or 0)
        out.append({**a, "element_count": len(uids), "percent_complete": round(pct, 2)})
    return out


def export_csv(conn, pid) -> str:
    rows = conn.execute(
        "SELECT e.element_uid, e.name, e.category, e.ifc_class, e.trade, l.name AS level, z.name AS zone, e.revit_unique_id, "
        "e.ifc_global_id, e.navis_item_guid, COALESCE(s.state, 'NOT_STARTED') AS state, COALESCE(s.percent_complete, 0) AS percent_complete, "
        "s.confidence, s.last_verified, e.expected_qty, e.qty_unit, "
        "(SELECT count(*) FROM deviations d WHERE d.element_uid = e.element_uid AND d.status = 'open') AS open_deviations "
        "FROM bim_elements e LEFT JOIN element_status s USING (element_uid) LEFT JOIN levels l ON l.id = e.level_id "
        "LEFT JOIN zones z ON z.id = e.zone_id WHERE e.project_id = %s ORDER BY l.name, e.trade, e.name", (pid,)).fetchall()
    buf = io.StringIO()
    cols = ["element_uid", "name", "category", "ifc_class", "trade", "level", "zone", "revit_unique_id", "ifc_global_id",
            "navis_item_guid", "state", "percent_complete", "confidence", "last_verified", "expected_qty", "qty_unit", "open_deviations"]
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    for r in rows:
        w.writerow({c: (r[c].isoformat() if isinstance(r[c], datetime) else r[c]) for c in cols})
    return buf.getvalue()
