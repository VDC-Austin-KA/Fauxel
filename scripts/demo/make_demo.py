"""Creates the "Fauxel Demo" project: a generated 3-storey IFC (all trades), a P6 schedule around today, progress observations.
Use it to try the portal (3D model page, dashboard, schedule) without real data. Re-running reuses the project.
Runs inside the api container (it has ifcopenshell). From C:\\Fauxel in PowerShell:
  docker cp scripts/demo/make_demo.py fauxel-api-1:/tmp/make_demo.py
  docker exec -e FAUXEL_KEY=<your API key> fauxel-api-1 python /tmp/make_demo.py
Note: observations are recorded today, so forecast slip for early activities reads large. Delete the project when done."""
import json, os, random, time, urllib.request, uuid
from datetime import date, timedelta

import numpy as np
import ifcopenshell, ifcopenshell.api as api

API = "http://localhost:8000/api/v1"
KEY = os.environ["FAUXEL_KEY"]
TODAY = date.today()
random.seed(7)


def req(method, path, body=None, files=None):
    headers = {"X-Fauxel-Key": KEY}
    data = None
    if files:
        b = uuid.uuid4().hex
        name, content = files
        data = (f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\nContent-Type: application/octet-stream\r\n\r\n").encode() + content + f"\r\n--{b}--\r\n".encode()
        headers["Content-Type"] = f"multipart/form-data; boundary={b}"
    elif body is not None:
        data = json.dumps(body).encode(); headers["Content-Type"] = "application/json"
    with urllib.request.urlopen(urllib.request.Request(API + path, data=data, method=method, headers=headers), timeout=300) as r:
        t = r.read()
        return json.loads(t) if t else None


def mat(x, y, z, rot=0.0):
    m = np.eye(4); m[:3, 3] = (x, y, z)
    if rot:
        c, s = np.cos(rot), np.sin(rot); m[0, 0], m[0, 1], m[1, 0], m[1, 1] = c, -s, s, c
    return m


def build_ifc(path):
    f = ifcopenshell.file(schema="IFC4")
    proj = api.run("root.create_entity", f, ifc_class="IfcProject", name="Fauxel Demo")
    api.run("unit.assign_unit", f, length={"is_metric": True, "raw": "METRE"})
    ctx = api.run("context.add_context", f, context_type="Model")
    body = api.run("context.add_context", f, context_type="Model", context_identifier="Body", target_view="MODEL_VIEW", parent=ctx)
    site = api.run("root.create_entity", f, ifc_class="IfcSite", name="Site")
    bld = api.run("root.create_entity", f, ifc_class="IfcBuilding", name="Demo Building")
    api.run("aggregate.assign_object", f, products=[site], relating_object=proj)
    api.run("aggregate.assign_object", f, products=[bld], relating_object=site)
    W, D, H = 24.0, 16.0, 4.0

    def put(cls, name, storey, rep, m):
        e = api.run("root.create_entity", f, ifc_class=cls, name=name)
        api.run("geometry.assign_representation", f, product=e, representation=rep)
        api.run("geometry.edit_object_placement", f, product=e, matrix=m)
        api.run("spatial.assign_container", f, products=[e], relating_structure=storey)

    def box(cls, name, storey, x, y, z, dx, dy, dz):
        prof = f.create_entity("IfcRectangleProfileDef", ProfileType="AREA", XDim=dx, YDim=dy)
        put(cls, name, storey, api.run("geometry.add_profile_representation", f, context=body, profile=prof, depth=dz), mat(x + dx / 2, y + dy / 2, z))

    def run_x(cls, name, storey, x, y, z, length, w, h):  # horizontal run along X (duct/pipe/tray): profile extruded along X
        prof = f.create_entity("IfcRectangleProfileDef", ProfileType="AREA", XDim=w, YDim=h)
        m = np.eye(4); m[:3, :3] = [[0, 0, 1], [1, 0, 0], [0, 1, 0]]; m[:3, 3] = (x, y, z)
        put(cls, name, storey, api.run("geometry.add_profile_representation", f, context=body, profile=prof, depth=length), m)

    for lv in range(1, 4):
        z = (lv - 1) * H
        st = api.run("root.create_entity", f, ifc_class="IfcBuildingStorey", name=f"Level {lv}")
        st.Elevation = z
        api.run("aggregate.assign_object", f, products=[st], relating_object=bld)
        box("IfcSlab", f"L{lv} Slab", st, 0, 0, z - 0.25, W, D, 0.25)
        for i, x in enumerate((0, 8, 16, 23.6)):
            for j, y in enumerate((0, 8, 15.6)):
                box("IfcColumn", f"L{lv} Column {chr(65 + i)}{j + 1}", st, x, y, z, 0.4, 0.4, H - 0.25)
        for name, x, y, length, rot in (("North", 0, D - 0.2, W, 0), ("South", 0, 0, W, 0), ("West", 0.2, 0, D, np.pi / 2), ("East", W, 0, D, np.pi / 2)):
            put("IfcWall", f"L{lv} Exterior Wall {name}", st,
                api.run("geometry.add_wall_representation", f, context=body, length=length, height=H - 0.25, thickness=0.2), mat(x, y, z, rot))
        for k, x in enumerate((6, 12, 18)):
            put("IfcWall", f"L{lv} Partition {k + 1}", st,
                api.run("geometry.add_wall_representation", f, context=body, length=7, height=H - 0.6, thickness=0.12), mat(x, 8.5, z, np.pi / 2))
        for k, y in enumerate((4, 12)):
            run_x("IfcDuctSegment", f"L{lv} Supply Duct {k + 1}", st, 1, y, z + H - 0.9, W - 2, 0.6, 0.4)
        for k, y in enumerate((6, 10)):
            run_x("IfcPipeSegment", f"L{lv} Sprinkler Main {k + 1}", st, 1, y, z + H - 0.5, W - 2, 0.15, 0.15)
        run_x("IfcCableCarrierSegment", f"L{lv} Cable Tray", st, 1, 8, z + H - 0.7, W - 2, 0.45, 0.1)
    f.write(path)


def xer():
    d = lambda n: (TODAY + timedelta(days=n)).isoformat() + " 08:00"
    rows, tid = [], 100
    # Level 1 mostly done, Level 2 in progress, Level 3 starting; offsets in days from today
    plan = {1: -120, 2: -45, 3: 20}
    work = [("structural", "Concrete slab and columns", 0, 25), ("plumbing_fp", "Sprinkler pipe rough-in", 20, 30),
            ("hvac", "HVAC duct rough-in", 20, 35), ("electrical", "Electrical cable tray", 25, 30), ("arch_interior", "Framing and drywall", 45, 40)]
    for lv, base in plan.items():
        for _, name, s, dur in work:
            rows.append(f"%R\t{tid}\tA{tid}\t{name} Level {lv}\t{d(base + s)}\t{d(base + s + dur)}\t0"); tid += 1
    return ("ERMHDR\t20.12\t2026-10-01\tProject\tadmin\tPM\tdbxDatabaseNoName\tProject Management\tUSD\n%T\tPROJECT\n%F\tproj_id\tproj_short_name\n%R\t1\tDEMO\n"
            "%T\tTASK\n%F\ttask_id\ttask_code\ttask_name\ttarget_start_date\ttarget_end_date\tphys_complete_pct\n" + "\n".join(rows) + "\n%E\n")


def main():
    pid = next((p["id"] for p in req("GET", "/projects") if p["name"] == "Fauxel Demo"), None)
    if not pid:
        pid = req("POST", "/projects", {"name": "Fauxel Demo", "description": "Generated demo: 3 levels, all trades, schedule around today"})["id"]
        build_ifc("/tmp/demo.ifc")
        req("POST", f"/projects/{pid}/models", files=("fauxel-demo.ifc", open("/tmp/demo.ifc", "rb").read()))
    for _ in range(60):
        els = req("GET", f"/projects/{pid}/elements?limit=1000")
        els = els["items"] if isinstance(els, dict) else els
        if len(els) > 50: break
        time.sleep(3)
    print("elements:", len(els))
    print("schedule:", req("POST", f"/projects/{pid}/schedule", files=("demo.xer", xer().encode())))
    lv = {l["id"]: l["name"] for b in req("GET", f"/projects/{pid}/lbs")["buildings"] for l in b["levels"]}
    # Level 1: done except a lagging drywall and one deviation; Level 2: structure done, MEP partly, drywall lagging; Level 3: slab ahead.
    def state(e):
        L, t, r = lv.get(e["level_id"], ""), e["trade"], random.random()
        if L == "Level 1":
            if t == "arch_interior": return ("INSTALLED", 100) if r < 0.6 else ("FRAMED_OR_ROUGHED", 50)
            return ("DEVIATION_FLAGGED", 60) if r < 0.05 else ("INSTALLED", 100)
        if L == "Level 2":
            if t == "structural": return ("INSTALLED", 100)
            if t in ("hvac", "plumbing_fp", "electrical"): return ("INSTALLED", 100) if r < 0.4 else ("FRAMED_OR_ROUGHED", 50) if r < 0.7 else None
            return None
        if L == "Level 3" and t == "structural": return ("FRAMED_OR_ROUGHED", 40) if r < 0.5 else None
        return None
    n = 0
    for e in els:
        s = state(e)
        if s:
            req("POST", f"/elements/{e['element_uid']}/observations", {"state": s[0], "confidence": 0.95, "source": "human"}); n += 1
    print("observations:", n, "project:", pid)


main()
