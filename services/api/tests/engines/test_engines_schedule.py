from datetime import date

import pytest

from fauxel_api.analytics.schedule import infer_location, infer_trade, map_activities, norm_loc, parse_schedule


def xer():
    rows = [
        "ERMHDR\t20.12\t2025-01-01\tProject",
        "%T\tPROJWBS", "%F\twbs_id\tparent_wbs_id\twbs_name",
        "%R\t1\t\tProject", "%R\t2\t1\tLevel 2", "%R\t3\t2\tZone 3B",
        "%T\tTASK",
        "%F\ttask_id\ttask_code\ttask_name\twbs_id\ttarget_start_date\ttarget_end_date\tact_start_date\tact_end_date\tphys_complete_pct\ttarget_cost",
        "%R\t10\tA1000\tHang drywall\t3\t2025-02-03 07:00\t2025-02-14 15:00\t2025-02-04 07:00\t\t40\t12000",
        "%R\t11\tA1010\tInstall duct rough-in L2\t2\t2025-02-01 07:00\t2025-02-10 15:00\t\t\t0\t",
        "%E",
    ]
    return "\n".join(rows).encode()


def test_xer():
    acts = {a["external_id"]: a for a in parse_schedule(xer(), "sched.xer")}
    a = acts["A1000"]
    assert a["trade"] == "arch_interior" and a["zone_name"] == "Zone 3B" and a["level_name"] == "Level 2"
    assert a["planned_start"] == date(2025, 2, 3) and a["actual_start"] == date(2025, 2, 4) and a["actual_finish"] is None
    assert a["percent_complete"] == 40 and a["budget_cost"] == 12000 and a["wbs"] == "Project > Level 2 > Zone 3B"
    assert acts["A1010"]["trade"] == "hvac" and acts["A1010"]["zone_name"] == "Level 2"
    assert acts["A1010"]["budget_cost"] is None


MSP = """<?xml version="1.0"?>
<Project xmlns="http://schemas.microsoft.com/project"><Tasks>
<Task><UID>0</UID><Name>Root</Name><Summary>1</Summary><OutlineLevel>0</OutlineLevel></Task>
<Task><UID>1</UID><Name>Area A</Name><Summary>1</Summary><OutlineLevel>1</OutlineLevel><WBS>1</WBS></Task>
<Task><UID>2</UID><Name>Pour slab on grade</Name><WBS>1.1</WBS><OutlineLevel>2</OutlineLevel><Start>2025-03-03T08:00:00</Start>
<Finish>2025-03-07T17:00:00</Finish><ActualStart>2025-03-03T08:00:00</ActualStart><PercentComplete>50</PercentComplete><Cost>5000</Cost></Task>
<Task><UID>3</UID><Name>Rough electrical conduit</Name><WBS>1.2</WBS><OutlineLevel>2</OutlineLevel><Start>2025-03-10T08:00:00</Start><Finish>2025-03-14T17:00:00</Finish><PercentComplete>0</PercentComplete></Task>
</Tasks></Project>""".encode()


def test_msp_xml():
    acts = parse_schedule(MSP, "plan.xml")
    assert [a["external_id"] for a in acts] == ["1.1", "1.2"]
    assert acts[0]["trade"] == "structural" and acts[0]["zone_name"] == "Area A"
    assert acts[0]["planned_finish"] == date(2025, 3, 7) and acts[0]["percent_complete"] == 50
    assert acts[1]["trade"] == "electrical"


P6 = """<APIBusinessObjects xmlns="http://xmlns.oracle.com/Primavera/P6/V21.12/API/BusinessObjects">
<Project><WBS><ObjectId>5</ObjectId><Name>Level 3</Name></WBS>
<Activity><Id>X1</Id><Name>Install sprinkler mains</Name><WBSObjectId>5</WBSObjectId><PlannedStartDate>2025-04-01T08:00:00</PlannedStartDate>
<PlannedFinishDate>2025-04-05T17:00:00</PlannedFinishDate><PhysicalPercentComplete>25</PhysicalPercentComplete></Activity></Project>
</APIBusinessObjects>""".encode()


def test_p6_xml():
    a = parse_schedule(P6, "x.xml")[0]
    assert a["trade"] == "plumbing_fp" and a["level_name"] == "Level 3" and a["percent_complete"] == 25


def test_bad_input():
    with pytest.raises(ValueError):
        parse_schedule(b"<foo/>", "a.xml")


def test_inference_and_normalisation():
    assert infer_trade("Install steel stud framing") == "arch_interior"
    assert infer_trade("Erect structural steel") == "structural"
    assert infer_trade("Misc", "Mechanical > Ductwork") == "hvac"
    assert infer_location("Rough-in L2 Zone 3B") == ("Zone 3B", "Level 2")
    assert infer_location("Paint Area a")[0] == "Area A"
    assert norm_loc("L2") == norm_loc("Level 2") == "level2"


def test_map_activities():
    acts = [{"external_id": "a", "trade": "arch_interior", "zone_name": "Zone 3B", "level_name": "Level 2"},
            {"external_id": "b", "trade": "arch_interior", "zone_name": "Zone 9", "level_name": "Level 2"},
            {"external_id": "c", "trade": "hvac", "zone_name": None, "level_name": None},
            {"external_id": "d", "trade": "structural", "zone_name": "Level 2", "level_name": "Level 2"}]
    els = [{"element_uid": "w1", "trade": "arch_interior", "zone_name": "zone 3b", "level_name": "L2"},
           {"element_uid": "w2", "trade": "arch_interior", "zone_name": "Zone 4", "level_name": "L2"},
           {"element_uid": "d1", "trade": "hvac", "zone_name": None, "level_name": "L1"},
           {"element_uid": "s1", "trade": "structural", "zone_name": None, "level_name": "Level 2"}]
    m = map_activities(acts, els)
    assert m["a"] == ["w1"]
    assert sorted(m["b"]) == ["w1", "w2"]  # unknown zone falls back to level
    assert m["c"] == ["d1"] and m["d"] == ["s1"]
