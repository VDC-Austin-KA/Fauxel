"""Demo projects for the portal: three building types, each with federated discipline IFC models (A, S, M, E, P, FP),
a P6 schedule around today (WBS, costs, % complete), field progress that follows the schedule, and a few QC deviations.

  bastrop-dc   Bastrop Data Center - Hall A         1-storey data hall: tilt-wall, steel roof, rack pods, hot-aisle
                                                     containment, CRAHs + CHW, air-cooled chillers, switchgear/UPS/PDUs,
                                                     busway, generators, pre-action sprinklers
  cedar-mob    Cedar Park Medical Office Building   3 storeys + roof: steel frame, curtain wall, 72 exam rooms, VAVs,
                                                     diffusers, exam sinks, restroom cores, RTUs, sprinklers
  riverside-mf Riverside Lofts - Building 2         5-storey podium multifamily: concrete L1, wood-frame L2-L5,
                                                     48 units with fixtures, fan coils, load centers, NFPA 13R

Runs inside the api container (it has ifcopenshell). From C:\\Fauxel in PowerShell:
  docker cp scripts/demo/make_demo.py fauxel-api-1:/tmp/make_demo.py
  docker exec -e FAUXEL_KEY=<your API key> fauxel-api-1 python /tmp/make_demo.py            # all three
  docker exec -e FAUXEL_KEY=<your API key> fauxel-api-1 python /tmp/make_demo.py cedar-mob  # one
  ... python /tmp/make_demo.py --ifc-only /tmp/out   # write the IFC files only (no API)
An existing project of the same name is skipped; add --replace to delete and rebuild it (also: --delete to remove the demo projects).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import time
import urllib.request
import uuid
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import ifcopenshell
import ifcopenshell.api as api

API = os.environ.get("FAUXEL_API", "http://localhost:8000/api/v1")
TODAY = date.today()
DISCIPLINES = {"A": "Architectural", "S": "Structural", "M": "Mechanical", "E": "Electrical", "P": "Plumbing", "FP": "Fire Protection"}
WBS = {"structural": "Structure", "arch_interior": "Architectural and Interiors", "hvac": "Mechanical",
       "plumbing_fp": "Plumbing and Fire Protection", "electrical": "Electrical", "other": "Owner Furnished"}


# ---------------------------------------------------------------- IFC authoring

class Disc:
    """One discipline model. Every discipline of a project shares storeys, spaces (zones) and the project frame."""

    def __init__(self, project: str, building: str, code: str, storeys, spaces):
        f = self.f = ifcopenshell.file(schema="IFC4")
        self.code, self.n = code, 0
        proj = api.run("root.create_entity", f, ifc_class="IfcProject", name=f"{project} - {DISCIPLINES[code]}")
        api.run("unit.assign_unit", f, length={"is_metric": True, "raw": "METRE"})
        ctx = api.run("context.add_context", f, context_type="Model")
        self.body = api.run("context.add_context", f, context_type="Model", context_identifier="Body", target_view="MODEL_VIEW", parent=ctx)
        site = api.run("root.create_entity", f, ifc_class="IfcSite", name="Site")
        bld = api.run("root.create_entity", f, ifc_class="IfcBuilding", name=building)
        api.run("aggregate.assign_object", f, products=[site], relating_object=proj)
        api.run("aggregate.assign_object", f, products=[bld], relating_object=site)
        self.st = {}
        for name, elev in storeys:
            s = api.run("root.create_entity", f, ifc_class="IfcBuildingStorey", name=name)
            s.Elevation = elev
            api.run("aggregate.assign_object", f, products=[s], relating_object=bld)
            self.st[name] = s
        for storey, zone, x0, y0, x1, y1, h in spaces:
            sp = api.run("root.create_entity", f, ifc_class="IfcSpace", name=zone)
            sp.LongName = zone
            prof = f.create_entity("IfcRectangleProfileDef", ProfileType="AREA", XDim=x1 - x0, YDim=y1 - y0)
            api.run("geometry.assign_representation", f, product=sp,
                    representation=api.run("geometry.add_profile_representation", f, context=self.body, profile=prof, depth=h))
            api.run("geometry.edit_object_placement", f, product=sp, matrix=_m((x0 + x1) / 2, (y0 + y1) / 2, dict(storeys)[storey]))
            api.run("aggregate.assign_object", f, products=[sp], relating_object=self.st[storey])

    def _put(self, cls, name, storey, prof, depth, m):
        e = api.run("root.create_entity", self.f, ifc_class=cls, name=name)
        api.run("geometry.assign_representation", self.f, product=e,
                representation=api.run("geometry.add_profile_representation", self.f, context=self.body, profile=prof, depth=depth))
        api.run("geometry.edit_object_placement", self.f, product=e, matrix=m)
        api.run("spatial.assign_container", self.f, products=[e], relating_structure=self.st[storey])
        self.n += 1
        return e

    def box(self, cls, name, storey, cx, cy, z0, dx, dy, dz):
        """Axis-aligned box: plan centre (cx, cy), from z0 up by dz."""
        prof = self.f.create_entity("IfcRectangleProfileDef", ProfileType="AREA", XDim=dx, YDim=dy)
        return self._put(cls, name, storey, prof, dz, _m(cx, cy, z0))

    def seg(self, cls, name, storey, p0, p1, w, h=None):
        """Straight run from p0 to p1 (any direction). h=None: round section of diameter w; else w wide (horizontal) x h."""
        p0, p1 = np.asarray(p0, float), np.asarray(p1, float)
        d = p1 - p0
        length = float(np.linalg.norm(d))
        z = d / length
        x = np.array([1.0, 0, 0]) if abs(z[2]) > 0.99 else np.cross([0, 0, 1.0], z)
        x /= np.linalg.norm(x)
        m = np.eye(4)
        m[:3, 0], m[:3, 1], m[:3, 2], m[:3, 3] = x, np.cross(z, x), z, p0
        prof = (self.f.create_entity("IfcCircleProfileDef", ProfileType="AREA", Radius=w / 2) if h is None else
                self.f.create_entity("IfcRectangleProfileDef", ProfileType="AREA", XDim=w, YDim=h))
        return self._put(cls, name, storey, prof, length, m)

    def wall(self, cls, name, storey, x0, y0, x1, y1, z, h, t):
        return self.seg(cls, name, storey, (x0, y0, z + h / 2), (x1, y1, z + h / 2), t, h)

    def write(self, path):
        self.f.write(str(path))


def _m(x, y, z):
    m = np.eye(4)
    m[:3, 3] = (x, y, z)
    return m


def disciplines(project, building, storeys, spaces):
    return {c: Disc(project, building, c, storeys, spaces) for c in DISCIPLINES}


def frange(a, b, step):
    return [round(v, 3) for v in np.arange(a, b + 1e-6, step)]


# ---------------------------------------------------------------- Bastrop Data Center - Hall A

def bastrop_dc():
    W, D, H = 72.0, 40.0, 9.0
    storeys = [("Level 1", 0.0), ("Roof", H)]
    zones = [("Level 1", "Zone A", 0, 0, 48, D, H - 0.6), ("Level 1", "Zone B", 48, 0, W, 20, H - 0.6),
             ("Level 1", "Zone C", 48, 20, W, D, H - 0.6)]
    m = disciplines("Bastrop Data Center - Hall A", "Hall A", storeys, zones)
    A, S, M, E, P, F = (m[k] for k in DISCIPLINES)
    xs, ys = frange(0, W, 12), frange(0, D, 10)
    # Structural: footings, steel columns, roof girders + joists, slab on grade pours, roof deck, chiller dunnage
    for i, x in enumerate(xs):
        for j, y in enumerate(ys):
            g = f"{'ABCDE'[j]}-{i + 1}"
            S.box("IfcFooting", f"Spread footing {g}", "Level 1", x, y, -1.2, 2.4, 2.4, 0.9)
            S.box("IfcColumn", f"Steel column {g} W14x90", "Level 1", x, y, 0, 0.4, 0.4, H - 0.9)
        for j in range(len(ys) - 1):
            S.seg("IfcBeam", f"Roof girder {i + 1}/{'ABCDE'[j]}-{'ABCDE'[j + 1]} W24x68", "Roof", (x, ys[j], H - 0.45), (x, ys[j + 1], H - 0.45), 0.25, 0.6)
    for i in range(len(xs) - 1):
        for y in frange(2.5, D - 2.5, 2.5):
            S.seg("IfcMember", f"Roof joist bay {i + 1} @ {y:.1f} 30K12", "Roof", (xs[i], y, H - 0.4), (xs[i + 1], y, H - 0.4), 0.15, 0.75)
        for j in range(len(ys) - 1):
            S.box("IfcSlab", f"Roof deck bay {i + 1}{'ABCD'[j]}", "Roof", (xs[i] + xs[i + 1]) / 2, (ys[j] + ys[j + 1]) / 2, H, 12, 10, 0.08)
    for k, (cx, cy) in enumerate((x, y) for x in (12, 36, 60) for y in (10, 30)):
        S.box("IfcSlab", f"Slab on grade pour {k + 1}", "Level 1", cx, cy, -0.2, 24, 20, 0.2)
    for k in range(6):
        S.box("IfcSlab", f"Chiller dunnage CH-{k + 1}", "Roof", 8 + k * 11, 30, H + 0.08, 11, 3.0, 0.3)
    # Architectural: tilt-wall panels, CMU rated walls, doors, rack pods (owner), hot-aisle containment
    for k, x in enumerate(frange(0, W - 8, 8)):
        A.wall("IfcWall", f"Tilt-wall panel S-{k + 1:02d}", "Level 1", x, 0, x + 8, 0, 0, H + 1.0, 0.25)
        A.wall("IfcWall", f"Tilt-wall panel N-{k + 1:02d}", "Level 1", x, D, x + 8, D, 0, H + 1.0, 0.25)
    for k, y in enumerate(frange(0, D - 8, 8)):
        A.wall("IfcWall", f"Tilt-wall panel W-{k + 1:02d}", "Level 1", 0, y, 0, y + 8, 0, H + 1.0, 0.25)
        A.wall("IfcWall", f"Tilt-wall panel E-{k + 1:02d}", "Level 1", W, y, W, y + 8, 0, H + 1.0, 0.25)
    A.wall("IfcWall", "2-hr CMU wall hall / electrical", "Level 1", 48, 0, 48, D, 0, H - 0.9, 0.3)
    A.wall("IfcWall", "2-hr CMU wall electrical / mechanical", "Level 1", 48, 20, W, 20, 0, H - 0.9, 0.3)
    for name, cx, cy, dx, dy in (("Door 101 hall to UPS", 48, 10, 0.4, 1.8), ("Door 102 hall to mech", 48, 30, 0.4, 1.8),
                                 ("Door 103 UPS to mech", 60, 20, 1.8, 0.4), ("Overhead door OH-1 loading", 66, D, 4.0, 0.4),
                                 ("Door 104 exterior", 2, 0, 1.0, 0.4), ("Door 105 exterior", 2, D, 1.0, 0.4)):
        A.box("IfcDoor", name, "Level 1", cx, cy, 0, dx, dy, 2.4 if "Overhead" not in name else 4.2)
    rows = []
    for k in range(4):
        y0 = 6 + k * 8
        for side, y in (("a", y0), ("b", y0 + 2.4)):
            row = 2 * k + (1 if side == "a" else 2)
            rows.append((row, y + 0.6))
            for j in range(6):
                A.box("IfcFurniture", f"Row {row:02d} rack pod {j + 1} (racks {j * 10 + 1}-{j * 10 + 10})", "Level 1", 7 + j * 6, y + 0.6, 0, 6.0, 1.2, 2.4)
        for j in range(6):
            A.box("IfcCovering", f"Hot-aisle containment pair {k + 1} section {j + 1}", "Level 1", 7 + j * 6, y0 + 1.8, 2.45, 6.0, 1.2, 0.05)
        A.box("IfcDoor", f"HAC end door pair {k + 1}", "Level 1", 3.9, y0 + 1.8, 0, 0.1, 1.2, 2.4)
    # Mechanical: CRAH fan-wall units, pump skids, chillers, exhaust fans, electrical room cooling
    for k, x in enumerate(frange(4, 44, 5.7)):
        M.box("IfcUnitaryEquipment", f"CRAH-S{k + 1:02d}", "Level 1", x, 1.2, 0, 2.6, 1.0, 2.2)
        M.box("IfcUnitaryEquipment", f"CRAH-N{k + 1:02d}", "Level 1", x, D - 1.2, 0, 2.6, 1.0, 2.2)
    for k in range(4):
        M.box("IfcPump", f"CHWP-{k + 1} chilled water pump", "Level 1", 54 + k * 4, 34, 0, 2.0, 1.2, 1.4)
    for k in range(6):
        M.box("IfcChiller", f"CH-{k + 1} air-cooled chiller 500 t", "Roof", 8 + k * 11, 30, H + 0.38, 10.5, 2.4, 2.6)
    for k in range(4):
        M.box("IfcFan", f"EF-{k + 1} relief fan", "Roof", 10 + k * 12, 10, H + 0.08, 1.5, 1.5, 1.2)
    for k, x in enumerate((52, 58, 64, 70)):
        M.box("IfcUnitaryEquipment", f"ACU-{k + 1} UPS room cooling", "Level 1", x, 18.6, 3.2, 2.2, 1.0, 1.0)
    M.seg("IfcDuctSegment", "Supply duct UPS room", "Level 1", (50, 15, 6.0), (71, 15, 6.0), 1.0, 0.6)
    for k, x in enumerate(frange(52, 68, 4)):
        M.box("IfcAirTerminal", f"Supply diffuser UPS-{k + 1}", "Level 1", x, 15, 5.4, 0.6, 0.6, 0.3)
    # Piping (CHW mains/drops, risers): plumbing_fp by IFC class
    for side, y in (("S", 2.4), ("N", D - 2.4)):
        for name, z in (("CHWS", 7.4), ("CHWR", 6.9)):
            P.seg("IfcPipeSegment", f"{name} header {side} 14in", "Level 1", (1.5, y, z), (52, y, z), 0.36)
    for k, x in enumerate(frange(4, 44, 5.7)):
        for side, y in (("S", 2.4), ("N", D - 2.4)):
            P.seg("IfcPipeSegment", f"CHW drop CRAH-{side}{k + 1:02d}", "Level 1", (x, y, 6.9), (x, y, 2.2), 0.15)
    for name, x in (("CHWS riser", 52), ("CHWR riser", 54)):
        P.seg("IfcPipeSegment", f"{name} 16in", "Roof", (x, 30, 6.9), (x, 30, H + 1.5), 0.4)
    for k, (x, y) in enumerate((x, y) for x in (12, 36, 60) for y in (10, 30)):
        P.box("IfcWasteTerminal", f"Roof drain RD-{k + 1}", "Roof", x, y, H + 0.08, 0.5, 0.5, 0.2)
        P.seg("IfcPipeSegment", f"Storm leader SL-{k + 1}", "Level 1", (x + 0.6, y, H), (x + 0.6, y, 0.3), 0.2)
    for k, name in enumerate(("WC-1", "WC-2", "Lav-1", "Lav-2", "Mop sink")):
        P.box("IfcSanitaryTerminal", f"{name} support restroom", "Level 1", 62 + k * 1.6, 38.5, 0, 0.6, 0.7, 0.9)
    P.seg("IfcPipeSegment", "Domestic cold water main", "Level 1", (50, 37, 3.4), (70, 37, 3.4), 0.08)
    P.box("IfcTank", "WH-1 water heater", "Level 1", 70.5, 37, 0, 0.7, 0.7, 1.6)
    # Electrical: switchgear, UPS, batteries, PDUs, busway, cable tray, generators, transformers, lighting
    for k, y in enumerate((4, 9)):
        E.box("IfcElectricDistributionBoard", f"SWGR-{'AB'[k]} 4000A switchgear lineup", "Level 1", 60, y, 0, 18.0, 1.2, 2.4)
    for k in range(4):
        E.box("IfcElectricFlowStorageDevice", f"UPS-{k + 1} 1250 kVA module", "Level 1", 53 + k * 5, 14, 0, 3.6, 1.0, 2.0)
        E.box("IfcElectricFlowStorageDevice", f"BATT-{k + 1} Li-ion cabinet", "Level 1", 53 + k * 5, 17, 0, 3.6, 0.8, 2.0)
    for row, y in rows:
        E.box("IfcElectricDistributionBoard", f"PDU-{row:02d}", "Level 1", 42.5, y, 0, 1.2, 1.2, 2.0)
        E.seg("IfcCableSegment", f"Busway row {row:02d} 800A", "Level 1", (4, y, 3.0), (42, y, 3.0), 0.3, 0.15)
        E.seg("IfcCableCarrierSegment", f"Cable tray row {row:02d} 24in ladder", "Level 1", (4, y, 3.6), (42, y, 3.6), 0.6, 0.1)
    E.seg("IfcCableCarrierSegment", "Feeder tray electrical to hall", "Level 1", (44, 20, 6.2), (70, 20, 6.2), 0.9, 0.15)
    for k in range(6):
        E.box("IfcElectricGenerator", f"GEN-{k + 1} 3 MW diesel generator", "Level 1", 7 + k * 11.5, -9, 0, 10.0, 3.2, 3.6)
    for k in range(2):
        E.box("IfcTransformer", f"XFMR-{k + 1} 34.5 kV pad mount", "Level 1", W + 5, 6 + k * 8, 0, 3.0, 3.0, 2.4)
    for x in frange(6, 42, 6):
        for y in frange(4, 36, 8):
            E.box("IfcLightFixture", f"LED high bay {x:.0f}-{y:.0f}", "Level 1", x, y, 7.6, 0.6, 0.6, 0.2)
    # Fire protection: pre-action risers, valves, cross main, branch lines, heads
    F.box("IfcValve", "PAV-1 pre-action valve Zone A", "Level 1", 50, 38, 0, 0.8, 0.8, 1.8)
    F.box("IfcValve", "WV-1 wet alarm valve Zones B/C", "Level 1", 52, 38, 0, 0.8, 0.8, 1.8)
    F.seg("IfcPipeSegment", "FP riser pre-action 6in", "Level 1", (50, 38, 1.8), (50, 38, 8.2), 0.17)
    F.seg("IfcPipeSegment", "Pre-action cross main 6in", "Level 1", (2, 20, 8.2), (50, 20, 8.2), 0.17)
    for x in frange(4, 44, 4):
        F.seg("IfcPipeSegment", f"Branch line BL-{x:.0f}", "Level 1", (x, 1.5, 8.0), (x, D - 1.5, 8.0), 0.05)
        for y in frange(3, 37, 4):
            F.box("IfcFireSuppressionTerminal", f"Sprinkler head {x:.0f}-{y:.0f}", "Level 1", x, y, 7.85, 0.12, 0.12, 0.15)
    F.seg("IfcPipeSegment", "Wet main Zones B/C 4in", "Level 1", (52, 2, 8.2), (52, D - 2, 8.2), 0.11)
    for y in frange(4, 36, 8):
        for x in (56, 62, 68):
            F.box("IfcFireSuppressionTerminal", f"Sprinkler head E{x}-{y:.0f}", "Level 1", x, y, 7.85, 0.12, 0.12, 0.15)

    plan = [  # name, trade, level, zone, start offset (days from today), duration, cost USD, bias (+ ahead / - behind)
        ("Level 1 concrete footings and slab on grade", "structural", "Level 1", None, -210, 60, 3.9e6, 0),
        ("Level 1 tilt-wall panels and framing", "arch_interior", "Level 1", None, -170, 45, 4.6e6, 0, ("tilt-wall",)),
        ("Roof structural steel erection and deck", "structural", "Roof", None, -150, 55, 5.2e6, 0),
        ("Level 1 Zone A chilled water piping", "plumbing_fp", "Level 1", "Zone A", -60, 70, 2.8e6, -0.15),
        ("Level 1 Zone A sprinkler pre-action rough-in", "plumbing_fp", "Level 1", "Zone A", -50, 60, 1.1e6, 0),
        ("Level 1 Zone A CRAH mechanical setting", "hvac", "Level 1", "Zone A", -40, 45, 3.4e6, -0.1),
        ("Level 1 Zone A busway and cable tray electrical", "electrical", "Level 1", "Zone A", -45, 60, 4.1e6, 0.05),
        ("Level 1 Zone A partitions, doors and containment framing", "arch_interior", "Level 1", "Zone A", -10, 50, 1.6e6, -0.1),
        ("Level 1 Zone A owner rack pod install", "other", "Level 1", "Zone A", 30, 40, 0.4e6, 0),
        ("Level 1 Zone B switchgear and UPS electrical", "electrical", "Level 1", "Zone B", -75, 65, 9.8e6, 0),
        ("Level 1 Zone B mechanical cooling", "hvac", "Level 1", "Zone B", -30, 40, 0.9e6, 0),
        ("Level 1 Zone B sprinkler rough-in", "plumbing_fp", "Level 1", "Zone B", -25, 30, 0.3e6, 0),
        ("Level 1 Zone B partitions and doors", "arch_interior", "Level 1", "Zone B", -60, 30, 0.6e6, 0),
        ("Level 1 Zone C pumps, plumbing and fire protection riser piping", "plumbing_fp", "Level 1", "Zone C", -55, 50, 1.6e6, 0),
        ("Level 1 Zone C electrical lighting", "electrical", "Level 1", "Zone C", -20, 30, 0.2e6, 0),
        ("Level 1 generator yard electrical", "electrical", "Level 1", None, -35, 50, 12.5e6, 0.1),
        ("Roof chiller setting mechanical", "hvac", "Roof", None, -20, 40, 7.4e6, -0.2),
        ("Roof drains and storm piping", "plumbing_fp", "Roof", None, -90, 30, 0.3e6, 0),
    ]
    deviations = [("CHW drop CRAH-N03", "positional", "high", "CHW drop 180 mm east of design; clashes with row 08 cable tray hanger"),
                  ("Tilt-wall panel N-07", "precover", "medium", "Embed plate for HAC support missing at panel N-07"),
                  ("Busway row 05 800A", "missing", "medium", "Busway section 3 not installed; tap boxes on site")]
    return "Bastrop Data Center - Hall A", "Hyperscale data hall, 72 x 40 m, 24 MW critical IT. Demo data.", m, zones, plan, deviations


# ---------------------------------------------------------------- Cedar Park Medical Office Building

def cedar_mob():
    W, D, FH = 54.0, 24.0, 4.5
    levels = ["Level 1", "Level 2", "Level 3"]
    storeys = [(lv, i * FH) for i, lv in enumerate(levels)] + [("Roof", 3 * FH)]
    zones = [(lv, z, x0, 0, x1, D, FH - 0.3) for lv in levels for z, x0, x1 in (("Zone A", 0, 27), ("Zone B", 27, W))]
    m = disciplines("Cedar Park Medical Office Building", "MOB", storeys, zones)
    A, S, M, E, P, F = (m[k] for k in DISCIPLINES)
    xs, ys = frange(0, W, 9), frange(0, D, 8)
    for i, x in enumerate(xs):
        for j, y in enumerate(ys):
            S.box("IfcFooting", f"Footing {'ABCD'[j]}-{i + 1}", "Level 1", x, y, -1.5, 2.0, 2.0, 0.8)
    for li, (lv, z) in enumerate(storeys):
        top = z + FH - 0.55
        if lv != "Roof":
            for i, x in enumerate(xs):
                for j, y in enumerate(ys):
                    S.box("IfcColumn", f"{lv[0]}{lv[-1]} column {'ABCD'[j]}-{i + 1} W12x53", lv, x, y, z, 0.32, 0.32, FH - 0.55)
        if lv == "Level 1":
            for k, (x0, x1) in enumerate(((0, 27), (27, W))):
                S.box("IfcSlab", f"L1 slab on grade pour {k + 1}", lv, (x0 + x1) / 2, D / 2, -0.15, x1 - x0, D, 0.15)
            continue
        tag = "R" if lv == "Roof" else lv[-1]
        for i in range(len(xs) - 1):
            for j in range(len(ys) - 1):
                S.box("IfcSlab", f"L{tag} composite deck bay {i + 1}{'ABC'[j]}", lv, (xs[i] + xs[i + 1]) / 2, (ys[j] + ys[j + 1]) / 2, z - 0.15, 9, 8, 0.15)
        for j, y in enumerate(ys):
            for i in range(len(xs) - 1):
                S.seg("IfcBeam", f"L{tag} beam {'ABCD'[j]}/{i + 1}-{i + 2} W18x35", lv, (xs[i], y, z - 0.4), (xs[i + 1], y, z - 0.4), 0.2, 0.45)
        for i, x in enumerate(xs):
            for j in range(len(ys) - 1):
                S.seg("IfcBeam", f"L{tag} girder {i + 1}/{'ABCD'[j]}-{'ABCD'[j + 1]} W21x44", lv, (x, ys[j], z - 0.4), (x, ys[j + 1], z - 0.4), 0.2, 0.5)
    for lv, z in storeys[:3]:
        n = lv[-1]
        for i in range(len(xs) - 1):
            A.box("IfcCurtainWall", f"L{n} curtain wall S bay {i + 1}", lv, (xs[i] + xs[i + 1]) / 2, -0.1, z, 9, 0.15, FH)
            A.wall("IfcWall", f"L{n} brick veneer N bay {i + 1}", lv, xs[i], D, xs[i + 1], D, z, FH, 0.3)
            A.box("IfcWindow", f"L{n} window N bay {i + 1}", lv, (xs[i] + xs[i + 1]) / 2, D + 0.05, z + 1.0, 4.0, 0.2, 1.8)
        for x, s in ((0, "W"), (W, "E")):
            A.wall("IfcWall", f"L{n} brick veneer {s}", lv, x, 0, x, D, z, FH, 0.3)
        for x0, x1, zn in ((0, 27, "A"), (27, W, "B")):
            for y in (10.5, 13.5):
                A.wall("IfcWall", f"L{n} corridor wall zone {zn} {'S' if y < 12 else 'N'}", lv, x0 + (9 if zn == "A" else 0), y, x1 - (9 if zn == "B" else 0), y, z, 3.2, 0.13)
        for side, (y0, y1) in (("S", (0, 10.5)), ("N", (13.5, D))):
            for k in range(13):
                x = 9 + 3 * k
                A.wall("IfcWall", f"L{n} partition {side}{k + 1:02d}", lv, x, y0, x, y1, z, 3.2, 0.12)
            for k in range(12):
                cx = 10.5 + 3 * k
                room = f"{n}{'1' if side == 'S' else '2'}{k + 1:02d}"
                A.box("IfcDoor", f"Door exam {room}", lv, cx, y0 + 10.5 if side == "S" else y0, z, 0.95, 0.15, 2.15)
                A.box("IfcCovering", f"ACT ceiling exam {room}", lv, cx, (y0 + y1) / 2, z + 3.0, 2.9, y1 - y0, 0.03)
                M.box("IfcAirTerminal", f"L{n} diffuser exam {room}", lv, cx, (y0 + y1) / 2, z + 2.95, 0.6, 0.6, 0.15)
                M.seg("IfcDuctSegment", f"L{n} branch duct exam {room}", lv, (cx, 12, z + 3.6), (cx, (y0 + y1) / 2, z + 3.6), 0.3, 0.2)
                E.box("IfcLightFixture", f"L{n} 2x4 troffer exam {room} a", lv, cx, (y0 + y1) / 2 - 2, z + 2.95, 0.6, 1.2, 0.1)
                E.box("IfcLightFixture", f"L{n} 2x4 troffer exam {room} b", lv, cx, (y0 + y1) / 2 + 2, z + 2.95, 0.6, 1.2, 0.1)
                P.box("IfcSanitaryTerminal", f"L{n} hand sink exam {room}", lv, cx + 1.0, y1 - 0.4 if side == "S" else y0 + 0.4, z + 0.8, 0.5, 0.45, 0.2)
                F.box("IfcFireSuppressionTerminal", f"L{n} sprinkler exam {room}", lv, cx, (y0 + y1) / 2, z + 2.9, 0.12, 0.12, 0.1)
            for k in range(6):
                M.box("IfcAirTerminalBox", f"L{n} VAV-{n}-{side}{k + 1}", lv, 12 + 6 * k, 12 + (-1.0 if side == "S" else 1.0), z + 3.5, 1.0, 0.7, 0.45)
        for cx, core in ((4.5, "W"), (49.5, "E")):
            A.box("IfcStair", f"L{n} stair {core}", lv, cx, 4, z, 4.0, 6.0, FH)
            if core == "W":
                A.box("IfcTransportElement", f"L{n} elevator car shaft", lv, cx, 19, z, 3.0, 3.0, FH)
            for k, name in enumerate(("WC", "WC", "Lav", "Lav")):
                P.box("IfcSanitaryTerminal", f"L{n} {name}-{k + 1} restroom {core}", lv, cx - 2.5 + k * 1.4, 12.5, z, 0.6, 0.7, 0.85)
            E.box("IfcElectricDistributionBoard", f"Panel L{n}{core} 208/120V", lv, cx + 2.4, 15.5, z + 0.5, 0.6, 0.2, 1.5)
            P.seg("IfcPipeSegment", f"L{n} sanitary stack {core}", lv, (cx - 1.5, 13.8, z), (cx - 1.5, 13.8, z + FH), 0.1)
            F.seg("IfcPipeSegment", f"L{n} standpipe {core}", lv, (cx + 1.6, 1.4, z), (cx + 1.6, 1.4, z + FH), 0.15)
            M.seg("IfcDuctSegment", f"L{n} supply shaft {core}", lv, (cx, 9.0, z), (cx, 9.0, z + FH), 1.0, 0.8)
        for x0, x1, zn in ((4.5, 27, "A"), (27, 49.5, "B")):
            M.seg("IfcDuctSegment", f"L{n} supply main zone {zn} 36x18", lv, (x0, 12, z + 3.6), (x1, 12, z + 3.6), 0.9, 0.45)
            E.seg("IfcCableCarrierSegment", f"L{n} cable tray corridor zone {zn}", lv, (x0, 11.0, z + 3.9), (x1, 11.0, z + 3.9), 0.3, 0.1)
            P.seg("IfcPipeSegment", f"L{n} domestic water main zone {zn}", lv, (x0, 13.1, z + 3.8), (x1, 13.1, z + 3.8), 0.08)
            F.seg("IfcPipeSegment", f"L{n} sprinkler main zone {zn} 4in", lv, (x0, 12.6, z + 4.0), (x1, 12.6, z + 4.0), 0.11)
            for x in frange(x0 + 3, x1 - 3, 6):
                E.box("IfcLightFixture", f"L{n} corridor downlight {x:.0f}", lv, x, 12, z + 2.95, 0.3, 0.3, 0.1)
    for k in range(3):
        M.box("IfcUnitaryEquipment", f"RTU-{k + 1} 40 t rooftop unit", "Roof", 12 + k * 15, 12, 3 * FH, 7.0, 2.4, 2.0)
    for k in range(2):
        M.box("IfcFan", f"EF-{k + 1} exhaust fan", "Roof", 6 + k * 42, 18, 3 * FH, 1.2, 1.2, 1.0)
    A.box("IfcCovering", "Roof membrane zone A", "Roof", 13.5, 12, 3 * FH, 27, 24, 0.05)
    A.box("IfcCovering", "Roof membrane zone B", "Roof", 40.5, 12, 3 * FH, 27, 24, 0.05)
    E.box("IfcElectricDistributionBoard", "MSB 2000A main switchboard", "Level 1", 2.5, 21, 0, 3.0, 0.9, 2.3)
    E.box("IfcTransformer", "XFMR-1 1000 kVA pad mount", "Level 1", -4, 20, 0, 2.4, 2.4, 2.0)
    P.box("IfcTank", "WH-1 water heater 100 gal", "Level 1", 52, 21, 0, 0.8, 0.8, 1.7)
    plan = [("Foundations and slab on grade concrete", "structural", "Level 1", None, -220, 45, 1.8e6, 0),
            ("Structural steel erection and deck Level 2", "structural", "Level 2", None, -180, 30, 1.4e6, 0),
            ("Structural steel erection and deck Level 3", "structural", "Level 3", None, -160, 30, 1.4e6, 0),
            ("Structural steel roof framing and deck", "structural", "Roof", None, -140, 25, 1.1e6, 0)]
    for i, lv in enumerate(levels):
        b = -90 + i * 35
        plan += [(f"{lv} exterior skin curtain wall and veneer framing", "arch_interior", lv, None, b - 10, 50, 1.2e6, 0, ("curtain wall", "veneer", "window")),
                 (f"{lv} HVAC duct and VAV rough-in", "hvac", lv, None, b, 40, 0.9e6, -0.1 if i == 1 else 0),
                 (f"{lv} plumbing rough-in", "plumbing_fp", lv, None, b, 40, 0.5e6, 0),
                 (f"{lv} sprinkler rough-in", "plumbing_fp", lv, None, b + 5, 30, 0.25e6, 0),
                 (f"{lv} electrical rough-in and lighting", "electrical", lv, None, b + 5, 45, 0.7e6, 0.05),
                 (f"{lv} Zone A partitions, drywall, doors and ceilings", "arch_interior", lv, "Zone A", b + 30, 45, 0.8e6, -0.15 if i == 0 else 0),
                 (f"{lv} Zone B partitions, drywall, doors and ceilings", "arch_interior", lv, "Zone B", b + 40, 45, 0.8e6, 0)]
    plan += [("Roof RTU setting mechanical", "hvac", "Roof", None, -45, 20, 1.3e6, 0),
             ("Roofing membrane finish", "arch_interior", "Roof", None, -60, 25, 0.6e6, 0)]
    deviations = [("L2 branch duct exam 2104", "positional", "medium", "Branch duct 150 mm low; conflicts with ACT grid height"),
                  ("L1 partition N06", "precover", "high", "Missing blocking for exam room casework before board"),
                  ("L3 sprinkler exam 3205", "missing", "low", "Head not installed; return before ceiling close-in")]
    return "Cedar Park Medical Office Building", "3-storey 45,000 sf MOB, steel frame, 72 exam rooms. Demo data.", m, zones, plan, deviations


# ---------------------------------------------------------------- Riverside Lofts - Building 2

def riverside_mf():
    W, D = 45.0, 18.0
    z_of = {"Level 1": 0.0, "Level 2": 4.8, "Level 3": 7.9, "Level 4": 11.0, "Level 5": 14.1, "Roof": 17.2}
    storeys = list(z_of.items())
    res = ["Level 2", "Level 3", "Level 4", "Level 5"]
    zones = [(lv, z, x0, 0, x1, D, 3.0) for lv in z_of if lv != "Roof" for z, x0, x1 in (("Zone A", 0, 22.5), ("Zone B", 22.5, W))]
    m = disciplines("Riverside Lofts - Building 2", "Building 2", storeys, zones)
    A, S, M, E, P, F = (m[k] for k in DISCIPLINES)
    # Level 1 concrete podium
    for i, x in enumerate(frange(0, W, 7.5)):
        for j, y in enumerate((0, 9, 18)):
            S.box("IfcFooting", f"Footing {'ABC'[j]}-{i + 1}", "Level 1", x, y, -1.2, 1.8, 1.8, 0.7)
            S.box("IfcColumn", f"L1 concrete column {'ABC'[j]}-{i + 1} 24x24", "Level 1", x, y, 0, 0.6, 0.6, 4.5)
    for k, (x0, x1) in enumerate(((0, 22.5), (22.5, W))):
        S.box("IfcSlab", f"L1 slab on grade pour {k + 1}", "Level 1", (x0 + x1) / 2, D / 2, -0.15, x1 - x0, D, 0.15)
        S.box("IfcSlab", f"L2 PT podium slab pour {k + 1}", "Level 2", (x0 + x1) / 2, D / 2, 4.5, x1 - x0, D, 0.3)
    for k, (x0, x1, use) in enumerate(((0, 15, "Lobby and leasing"), (15, 30, "Amenity"), (30, W, "Retail"))):
        A.box("IfcCurtainWall", f"L1 storefront {use}", "Level 1", (x0 + x1) / 2, -0.1, 0, x1 - x0, 0.15, 4.2)
    A.wall("IfcWall", "L1 CMU wall north", "Level 1", 0, D, W, D, 0, 4.5, 0.25)
    E.box("IfcElectricDistributionBoard", "L1 meter center MC-1", "Level 1", 3, 17, 0, 4.0, 0.4, 2.0)
    E.box("IfcElectricDistributionBoard", "L1 house panel HP-1", "Level 1", 8, 17, 0.5, 0.6, 0.2, 1.5)
    E.box("IfcTransformer", "XFMR-1 750 kVA pad mount", "Level 1", -5, 15, 0, 2.4, 2.4, 2.0)
    F.box("IfcValve", "L1 fire riser room alarm valve", "Level 1", 43, 17, 0, 0.8, 0.8, 1.8)
    F.seg("IfcPipeSegment", "FP riser 4in", "Level 1", (43, 16.5, 1.8), (43, 16.5, 17.0), 0.11)
    P.box("IfcPump", "Domestic booster pump", "Level 1", 40, 17, 0, 1.5, 0.8, 1.2)
    for x in frange(3.75, W - 3.75, 7.5):
        E.box("IfcLightFixture", f"L1 downlight {x:.1f}", "Level 1", x, 9, 4.0, 0.3, 0.3, 0.1)
        F.box("IfcFireSuppressionTerminal", f"L1 sprinkler {x:.1f}", "Level 1", x, 9, 4.1, 0.12, 0.12, 0.1)
    # Level 2-5 wood frame, 12 units per floor along a double-loaded corridor (y 8-10)
    for lv in res:
        z, n = z_of[lv], lv[-1]
        h = 3.1 - 0.35
        for x0, x1, zn in ((0, 22.5, "A"), (22.5, W, "B")):
            S.box("IfcSlab", f"L{n} corridor floor zone {zn}", lv, (x0 + x1) / 2, 9, z - 0.3 if lv != "Level 2" else z, x1 - x0, 2.0, 0.3 if lv != "Level 2" else 0.05)
            M.seg("IfcDuctSegment", f"L{n} corridor supply zone {zn}", lv, (x0, 9, z + 2.6), (x1, 9, z + 2.6), 0.4, 0.25)
            F.seg("IfcPipeSegment", f"L{n} sprinkler main zone {zn}", lv, (x0, 8.6, z + 2.7), (x1, 8.6, z + 2.7), 0.08)
        for y in (8, 10):
            A.wall("IfcWall", f"L{n} corridor wall {'S' if y == 8 else 'N'}", lv, 0, y, W, y, z, h, 0.15)
        for k in range(6):
            x0, x1 = k * 7.5, (k + 1) * 7.5
            cx = (x0 + x1) / 2
            for side, (y0, y1) in (("S", (0, 8)), ("N", (10, D))):
                u = f"{n}{'0' if side == 'S' else '1'}{k + 1}"
                yc, ye = (y0 + y1) / 2, (y0 if side == "S" else y1)
                if lv != "Level 2":
                    S.box("IfcSlab", f"Unit {u} floor assembly (I-joist)", lv, cx, yc, z - 0.35, 7.5, 8, 0.35)
                A.wall("IfcWall", f"Unit {u} exterior wall {side}", lv, x0, ye, x1, ye, z, h, 0.2)
                A.wall("IfcWall", f"Unit {u} demising wall", lv, x1, y0, x1, y1, z, h, 0.2)
                A.box("IfcDoor", f"Unit {u} entry door", lv, x0 + 1.2, y1 if side == "S" else y0, z, 0.95, 0.2, 2.1)
                for wi, wx in enumerate((x0 + 2.0, x0 + 5.5)):
                    A.box("IfcWindow", f"Unit {u} window {wi + 1}", lv, wx, ye, z + 0.9, 1.5, 0.25, 1.5)
                if side == "S":
                    A.box("IfcRailing", f"Unit {u} balcony railing", lv, cx, -1.5, z, 3.5, 0.05, 1.07)
                M.box("IfcUnitaryEquipment", f"Unit {u} FCU fan coil", lv, x0 + 6.5, y1 - 1.5 if side == "S" else y0 + 1.5, z + 2.3, 1.0, 0.6, 0.4)
                M.box("IfcFan", f"Unit {u} bath exhaust fan", lv, x0 + 2.0, y1 - 1.2 if side == "S" else y0 + 1.2, z + 2.6, 0.3, 0.3, 0.2)
                E.box("IfcElectricDistributionBoard", f"Unit {u} load center 125A", lv, x0 + 0.4, y1 - 0.6 if side == "S" else y0 + 0.6, z + 1.2, 0.35, 0.1, 0.6)
                for li, ly in enumerate((yc - 2, yc + 2)):
                    E.box("IfcLightFixture", f"Unit {u} light {li + 1}", lv, cx, ly, z + 2.65, 0.4, 0.4, 0.08)
                by = y1 - 1.0 if side == "S" else y0 + 1.0
                for fi, (fx, fname) in enumerate(((x0 + 1.3, "WC"), (x0 + 2.4, "Lav"), (x0 + 3.8, "Tub/shower"), (x0 + 6.0, "Kitchen sink"))):
                    P.box("IfcSanitaryTerminal", f"Unit {u} {fname}", lv, fx, by if fname != "Kitchen sink" else yc, z, 1.5 if fname == "Tub/shower" else 0.6, 0.75, 0.85)
                P.box("IfcTank", f"Unit {u} water heater 40 gal", lv, x0 + 4.9, by, z, 0.55, 0.55, 1.4)
                if k % 2 == 0:
                    P.seg("IfcPipeSegment", f"L{n} waste stack {u}", lv, (x1, by, z - 0.35), (x1, by, z + 3.1), 0.1)
                F.seg("IfcPipeSegment", f"Unit {u} sprinkler branch", lv, (cx, 8.6 if side == "S" else 9.4, z + 2.7), (cx, yc, z + 2.7), 0.03)
                for si, sy in enumerate((yc - 2, yc + 1.5)):
                    F.box("IfcFireSuppressionTerminal", f"Unit {u} sprinkler {si + 1}", lv, cx + 1.0, sy, z + 2.65, 0.1, 0.1, 0.08)
        for cx, end in ((-1.75, "W"), (W + 1.75, "E")):
            A.box("IfcStair", f"L{n} stair {end}", lv, cx, 9, z, 3.5, 6.0, 3.1)
    for x0, x1, zn in ((0, 22.5, "A"), (22.5, W, "B")):
        S.box("IfcSlab", f"Roof truss and sheathing zone {zn}", "Roof", (x0 + x1) / 2, 9, 17.2, x1 - x0, D, 0.4)
        A.box("IfcCovering", f"Roof membrane zone {zn}", "Roof", (x0 + x1) / 2, 9, 17.6, x1 - x0, D, 0.05)
    M.box("IfcUnitaryEquipment", "MAU-1 corridor makeup air unit", "Roof", 22.5, 9, 17.65, 4.0, 2.0, 1.8)
    M.seg("IfcDuctSegment", "MAU-1 supply riser", "Level 5", (22.5, 9, 4.8), (22.5, 9, 17.65), 0.6, 0.6)
    plan = [("Level 1 foundations and concrete columns", "structural", "Level 1", None, -260, 50, 1.6e6, 0),
            ("Level 2 PT podium slab concrete", "structural", "Level 2", None, -215, 25, 1.1e6, 0),
            ("Level 1 storefront and CMU framing", "arch_interior", "Level 1", None, -150, 40, 0.7e6, -0.1),
            ("Level 1 electrical service and meter center", "electrical", "Level 1", None, -120, 50, 0.6e6, 0),
            ("Level 1 sprinkler riser and fire protection", "plumbing_fp", "Level 1", None, -110, 30, 0.2e6, 0),
            ("Roof truss and sheathing structural", "structural", "Roof", None, -10, 20, 0.5e6, 0),
            ("Roof membrane finish", "arch_interior", "Roof", None, 15, 20, 0.4e6, 0),
            ("Roof MAU mechanical", "hvac", "Roof", None, 25, 10, 0.2e6, 0)]
    for i, lv in enumerate(res):
        b = -150 + i * 30
        plan += [(f"{lv} wood floor structural framing", "structural", lv, None, b, 20, 0.6e6, 0),
                 (f"{lv} exterior and demising wall framing", "arch_interior", lv, None, b + 5, 25, 0.8e6, 0, ("wall",)),
                 (f"{lv} plumbing rough-in and fixtures", "plumbing_fp", lv, None, b + 30, 45, 0.7e6, -0.1 if i == 2 else 0),
                 (f"{lv} sprinkler NFPA 13R rough-in", "plumbing_fp", lv, None, b + 32, 25, 0.25e6, 0),
                 (f"{lv} HVAC duct and fan coil rough-in", "hvac", lv, None, b + 35, 35, 0.5e6, 0),
                 (f"{lv} electrical rough-in, load centers and lighting", "electrical", lv, None, b + 35, 45, 0.5e6, 0.05),
                 (f"{lv} drywall, doors, windows and finishes", "arch_interior", lv, None, b + 70, 60, 1.1e6, -0.15 if i == 1 else 0)]
    deviations = [("Unit 305 demising wall", "precover", "high", "STC insulation missing at demising wall before board"),
                  ("L4 waste stack 413", "positional", "medium", "Stack 120 mm off; conflicts with I-joist web"),
                  ("Unit 212 FCU fan coil", "extra", "low", "FCU installed at unit 212; not in design for this unit type")]
    return "Riverside Lofts - Building 2", "5-storey podium multifamily, 48 units over retail/amenity. Demo data.", m, zones, plan, deviations


PROJECTS = {"bastrop-dc": bastrop_dc, "cedar-mob": cedar_mob, "riverside-mf": riverside_mf}


# ---------------------------------------------------------------- schedule + field progress

def frac_of(start, dur, bias=0.0):
    return max(0.0, min(1.0, (-start) / dur + bias)) if start < 0 else max(0.0, bias if bias > 0 and start < 10 else 0.0)


def xer(code, plan):
    d = lambda n: (TODAY + timedelta(days=n)).isoformat() + " 08:00"
    wbs_ids = {t: 10 + i for i, t in enumerate(WBS)}
    head = ("ERMHDR\t20.12\t2026-10-01\tProject\tadmin\tPM\tdbxDatabaseNoName\tProject Management\tUSD\n"
            f"%T\tPROJECT\n%F\tproj_id\tproj_short_name\n%R\t1\t{code}\n"
            "%T\tPROJWBS\n%F\twbs_id\tparent_wbs_id\twbs_name\n" + "".join(f"%R\t{i}\t\t{WBS[t]}\n" for t, i in wbs_ids.items()))
    rows = []
    for k, (name, trade, _lv, _z, start, dur, cost, bias, *_keys) in enumerate(plan):
        f = frac_of(start, dur, bias)
        act_s = d(start) if start < 0 or f > 0 else ""
        act_f = d(start + dur) if f >= 1.0 else ""
        rows.append(f"%R\t{100 + k}\tA{1000 + k * 10}\t{name}\t{wbs_ids[trade]}\t{d(start)}\t{d(start + dur)}\t{act_s}\t{act_f}\t{round(f * 100)}\t{cost:.0f}")
    return (head + "%T\tTASK\n%F\ttask_id\ttask_code\ttask_name\twbs_id\ttarget_start_date\ttarget_end_date\tact_start_date\t"
            "act_end_date\tphys_complete_pct\ttarget_cost\n" + "\n".join(rows) + "\n%E\n")


def field_state(trade, frac, rng):
    """One element's observed state for an activity `frac` complete. None = no observation (not started)."""
    if frac <= 0:
        return None
    r = rng.random()
    if r < frac * 0.97:
        if r < 0.007:
            return "DEVIATION_FLAGGED"
        return "INSULATED_OR_FINISHED" if trade == "arch_interior" and frac > 0.9 and r < frac * 0.6 else "INSTALLED"
    if r < min(1.0, frac + 0.2) and trade not in ("other",):
        return "FRAMED_OR_ROUGHED"
    return None


def activity_for(el_trade, el_name, level, zone, plan):
    """Best activity for an element: same trade and level; an activity naming element keywords wins, then a zone match."""
    best, rank = None, -1
    for p in plan:
        _n, trade, lv, z, *_rest = p
        keys = p[8] if len(p) > 8 else None
        if trade != el_trade or lv != level or (z is not None and z != zone):
            continue
        if keys and not any(k in el_name.lower() for k in keys):
            continue
        r = 2 if keys else 1 if z else 0
        if r > rank:
            best, rank = p, r
    return best


def zone_of(level, c, zones):
    for lv, z, x0, y0, x1, y1, _h in zones:
        if lv == level and x0 <= c[0] <= x1 and y0 <= c[1] <= y1:
            return z
    return None


# ---------------------------------------------------------------- API

def req(method, path, body=None, files=None):
    headers = {"X-Fauxel-Key": os.environ["FAUXEL_KEY"]}
    data = None
    if files:
        b = uuid.uuid4().hex
        name, content = files
        data = (f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\n"
                "Content-Type: application/octet-stream\r\n\r\n").encode() + content + f"\r\n--{b}--\r\n".encode()
        headers["Content-Type"] = f"multipart/form-data; boundary={b}"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    with urllib.request.urlopen(urllib.request.Request(API + path, data=data, method=method, headers=headers), timeout=600) as r:
        t = r.read()
        return json.loads(t) if t else None


def all_elements(pid):
    out, off = [], 0
    while True:
        page = req("GET", f"/projects/{pid}/elements?limit=1000&offset={off}")
        out += page
        if len(page) < 1000:
            return out
        off += 1000


def build(slug, outdir: Path):
    name, desc, models, zones, plan, devs = PROJECTS[slug]()
    outdir.mkdir(parents=True, exist_ok=True)
    files = []
    code = {"bastrop-dc": "BDC", "cedar-mob": "CPMOB", "riverside-mf": "RLB2"}[slug]
    for disc, mdl in models.items():
        path = outdir / f"{code}-{disc}-{DISCIPLINES[disc].replace(' ', '')}.ifc"
        mdl.write(path)
        files.append((path, mdl.n))
    return name, desc, code, files, zones, plan, devs


def delete_projects(name):
    """The API has no project delete; this script runs in the api container, so remove the row (everything cascades)."""
    import psycopg
    with psycopg.connect(os.environ["FAUXEL_DATABASE_URL"]) as c:
        n = c.execute("DELETE FROM projects WHERE name = %s", (name,)).rowcount
    print(f"{name}: deleted {n} project(s)")


def load(slug, outdir: Path, replace=False):
    name, desc, code, files, zones, plan, devs = build(slug, outdir)
    if any(p["name"] == name for p in req("GET", "/projects")):
        if not replace:
            print(f"{name}: exists, skipped (add --replace to rebuild)")
            return
        delete_projects(name)
    pid = req("POST", "/projects", {"name": name, "description": desc})["id"]
    expected = sum(n for _, n in files)
    for path, n in files:
        req("POST", f"/projects/{pid}/models", files=(path.name, path.read_bytes()))
        print(f"  {path.name}: {n} elements")
    print("  schedule:", req("POST", f"/projects/{pid}/schedule", files=(f"{code}.xer", xer(code, plan).encode())))
    els = all_elements(pid)
    levels = {lv["id"]: lv["name"] for b in req("GET", f"/projects/{pid}/lbs")["buildings"] for lv in b["levels"]}
    rng = random.Random(code)
    counts: dict[str, int] = {}
    by_name = {}
    for e in els:
        by_name[e["name"]] = e
        lv = levels.get(e.get("level_id"), "")
        c = [(a + b) / 2 for a, b in zip(e["aabb_min"], e["aabb_max"])] if e.get("aabb_min") else [0, 0, 0]
        act = activity_for(e["trade"], e.get("name") or "", lv, zone_of(lv, c, zones), plan)
        if not act:
            continue
        st = field_state(e["trade"], frac_of(act[4], act[5], act[7]), rng)
        if st:
            req("POST", f"/elements/{e['element_uid']}/observations", {"state": st, "confidence": 0.95, "source": "human"})
            counts[st] = counts.get(st, 0) + 1
    for el_name, kind, sev, note in devs:
        e = by_name.get(el_name)
        if e:
            req("POST", f"/projects/{pid}/deviations", {"element_uid": e["element_uid"], "kind": kind, "severity": sev, "note": note})
    print(f"{name}: {len(els)}/{expected} elements, observations {counts}, project {pid}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("projects", nargs="*", help=f"any of {', '.join(PROJECTS)} (default: all)")
    ap.add_argument("--ifc-only", metavar="DIR", help="write the IFC files and the XER here; no API calls")
    ap.add_argument("--replace", action="store_true", help="delete an existing demo project of the same name and rebuild it")
    ap.add_argument("--delete", action="store_true", help="delete the demo projects and stop")
    a = ap.parse_args()
    slugs = a.projects or list(PROJECTS)
    if bad := [s for s in slugs if s not in PROJECTS]:
        ap.error(f"unknown project(s) {bad}; choose from {list(PROJECTS)}")
    if a.ifc_only:
        for s in slugs:
            name, _d, code, files, _z, plan, _dv = build(s, Path(a.ifc_only))
            (Path(a.ifc_only) / f"{code}.xer").write_text(xer(code, plan))
            print(name, [(p.name, n) for p, n in files])
        return
    if a.delete:
        for s in slugs:
            delete_projects(PROJECTS[s]()[0])
        return
    for s in slugs:
        t = time.time()
        load(s, Path("/tmp/fauxel-demo") / s, a.replace)
        print(f"  {time.time() - t:.0f}s")


if __name__ == "__main__":
    main()
