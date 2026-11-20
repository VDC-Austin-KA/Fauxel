"""IFC -> scene.obj (one `o E<idx>` object per product, world coords, metres, Z up) + elements.json + spaces.json for render_blender.py.
Runs in the worker's system Python (ifcopenshell), not inside Blender.   python3 prestep.py model.ifc workdir"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import ifcopenshell
import ifcopenshell.geom
import numpy as np

TRADE_BY_CLASS = (  # first match wins (prefix match on the IFC class)
    ("IfcWall", "arch_interior"),
    ("IfcDuct", "hvac"), ("IfcAirTerminal", "hvac"), ("IfcFlowTerminal", "hvac"),
    ("IfcCableCarrier", "electrical"), ("IfcCableSegment", "electrical"), ("IfcCableFitting", "electrical"), ("IfcLightFixture", "electrical"),
    ("IfcPipe", "plumbing_fp"), ("IfcFireSuppression", "plumbing_fp"), ("IfcSanitaryTerminal", "plumbing_fp"),
    ("IfcBeam", "structural"), ("IfcColumn", "structural"), ("IfcSlab", "structural"), ("IfcFooting", "structural"),
)


def trade_of(ifc_class: str) -> str:
    return next((t for p, t in TRADE_BY_CLASS if ifc_class.startswith(p)), "other")


def main(ifc_path: str, work: str) -> dict:
    out = Path(work)
    out.mkdir(parents=True, exist_ok=True)
    model = ifcopenshell.open(ifc_path)
    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)
    it = ifcopenshell.geom.iterator(settings, model, max(1, __import__("os").cpu_count() or 1))
    elements, spaces, vbase = [], [], 1
    with open(out / "scene.obj", "w") as fh:
        if it.initialize():
            while True:
                sh = it.get()
                prod = model.by_id(sh.id)
                v = np.array(sh.geometry.verts, float).reshape(-1, 3)
                f = np.array(sh.geometry.faces, int).reshape(-1, 3)
                if len(v) and len(f):
                    mn, mx = v.min(0).tolist(), v.max(0).tolist()
                    if prod.is_a("IfcSpace"):
                        spaces.append({"name": prod.Name, "min": mn, "max": mx})
                    elif not prod.is_a("IfcOpeningElement"):
                        idx = len(elements)
                        elements.append({"idx": idx, "id": idx + 1, "guid": prod.GlobalId, "ifc_class": prod.is_a(), "name": prod.Name,
                                         "trade": trade_of(prod.is_a()), "min": mn, "max": mx})
                        fh.write(f"o E{idx}\n")
                        fh.write("".join(f"v {a:.6f} {b:.6f} {c:.6f}\n" for a, b, c in v))
                        fh.write("".join(f"f {a + vbase} {b + vbase} {c + vbase}\n" for a, b, c in f))
                        vbase += len(v)
                if not it.next():
                    break
    (out / "elements.json").write_text(json.dumps(elements, indent=1))
    (out / "spaces.json").write_text(json.dumps(spaces, indent=1))
    print(f"prestep: {len(elements)} elements, {len(spaces)} spaces -> {out}")
    return {"elements": len(elements), "spaces": len(spaces)}


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
