from datetime import date

from fauxel_api.analytics.evm import compute_evm, forecast, s_curve

D = date


def act(i, trade, s, f, uids, cost=None, zone="Level 2"):
    return {"external_id": i, "trade": trade, "zone_name": zone, "element_uids": uids, "budget_cost": cost,
            "planned_start": s, "planned_finish": f, "percent_complete": 0}


ACTS = [act("A", "structural", D(2025, 1, 1), D(2025, 1, 10), ["e1", "e2"], 1000),
        act("B", "arch_interior", D(2025, 1, 6), D(2025, 1, 15), ["e3"], 3000)]


def test_evm_project_and_trade():
    r = compute_evm(ACTS, {"e1": 100, "e2": 50, "e3": 0}, D(2025, 1, 5), "project")[0]
    # PV: A 5/10 * 1000 = 500; B 0. EV: A 75% * 1000 = 750
    assert r["pv"] == 500 and r["ev"] == 750 and r["spi"] == 1.5 and r["cpi"] == 1.0 and "AC unknown" in r["note"]
    by = {x["scope_key"]: x for x in compute_evm(ACTS, {"e3": 100}, D(2025, 1, 10), "trade")}
    assert by["arch_interior"]["ev"] == 3000 and by["structural"]["pv"] == 1000


def test_evm_missing_budget_uses_element_count_and_activity_percent():
    acts = [act("A", "hvac", D(2025, 1, 1), D(2025, 1, 10), ["e1", "e2"]), act("B", "hvac", D(2025, 1, 1), D(2025, 1, 10), [])]
    acts[1]["percent_complete"] = 50
    r = compute_evm(acts, {"e1": 100, "e2": 100}, D(2025, 1, 10), "zone")[0]
    assert r["weight_basis"] == "element_count" and r["scope_key"] == "Level 2"
    assert r["pv"] == 3 and r["ev"] == 2.5


def test_s_curve():
    hist = [{"date": "2025-01-08", "element_uid": u, "percent": 100} for u in ("e1", "e2", "e3")]
    c = s_curve(ACTS, hist, D(2025, 1, 1), D(2025, 1, 15))
    assert [p["date"] for p in c] == ["2025-01-01", "2025-01-08", "2025-01-15"]
    assert c[0]["actual_pct"] == 0 and c[1]["actual_pct"] == 100 and c[2]["planned_pct"] == 100
    assert 0 < c[1]["planned_pct"] < 100


def test_forecast_velocity_slip_and_stacking():
    hist = [{"date": "2025-01-01", "element_uid": "e1", "percent": 0}, {"date": "2025-01-01", "element_uid": "e2", "percent": 0},
            {"date": "2025-01-08", "element_uid": "e1", "percent": 20}, {"date": "2025-01-08", "element_uid": "e2", "percent": 20}]
    f = forecast(ACTS, hist, D(2025, 1, 8))
    a = {x["external_id"]: x for x in f["activities"]}
    assert abs(a["A"]["velocity_per_week"] - 20) < 0.01  # 20 % in a week
    assert a["A"]["slip_days"] == 26  # 80 % left at 20 %/wk = 28 d from Jan 8 -> Feb 5 vs Jan 10
    assert f["projected_finish"] >= D(2025, 2, 5)
    al = f["stacking_alerts"]
    assert any(x["upstream_trade"] == "structural" and x["downstream_trade"] == "arch_interior" for x in al)
    assert al[0]["days_until_conflict"] == -2 and al[0]["zone"] == "Level 2"


def test_no_alert_when_upstream_complete():
    hist = [{"date": "2025-01-03", "element_uid": u, "percent": 100} for u in ("e1", "e2")]
    assert forecast(ACTS, hist, D(2025, 1, 5))["stacking_alerts"] == []
