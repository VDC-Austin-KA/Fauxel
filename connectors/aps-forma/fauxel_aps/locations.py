"""Locations API v2 (Forma/ACC): the project's Location Breakdown Structure (LBS).

Verified: GET https://developer.api.autodesk.com/construction/locations/v2/projects/{projectId}/trees/default/nodes
(3-legged only), limit/offset pagination, nodes {id,parentId,type,name,description,barcode,order,areaDefined}.
Docs: https://aps.autodesk.com/en/docs/acc/v1/tutorials/locations/configure-locations-tree and
https://aps.autodesk.com/blog/new-endpoint-get-locations-bim-360acc
"""
from __future__ import annotations

from .datamanagement import strip_b
from .http import ApsSession


class Locations:
    def __init__(self, s: ApsSession, project_id: str):
        self.s = s
        self.path = f"/construction/locations/v2/projects/{strip_b(project_id)}/trees/default/nodes"

    def nodes(self) -> list[dict]:
        return list(self.s.paginate_offset(self.path, limit=100))


def classify(nodes: list[dict]) -> dict:
    """Map the LBS onto Fauxel buildings / levels / zones.

    Node `type` is honoured when it says Building / Level|Floor / Zone|Room|Space. Otherwise depth under the root
    decides (1 = building, 2 = level, 3+ = zone; the exact LBS type names for Forma are not in the reference, so this is a
    heuristic to confirm against a live tree). Nodes below zone depth are folded into their zone. Returns {"buildings":[{id,name}], "levels":[{id,name,building_id}], "zones":[{id,name,level_id}]}.
    """
    by_id = {n["id"]: n for n in nodes}
    kids: dict[str | None, list[dict]] = {}
    for n in nodes:
        kids.setdefault(n.get("parentId"), []).append(n)
    for lst in kids.values():
        lst.sort(key=lambda n: (n.get("order") or 0, n.get("name") or ""))
    root = next((n for n in nodes if (n.get("type") or "").lower() == "root" or n.get("parentId") is None), None)
    out = {"buildings": [], "levels": [], "zones": []}
    if not root:
        return out

    def kind_of(n, depth):
        t = (n.get("type") or "").lower()
        if "building" in t:
            return "building"
        if "level" in t or "floor" in t:
            return "level"
        if t in ("zone", "room", "space"):
            return "zone"
        return ("building", "level", "zone")[min(depth - 1, 2)]  # untyped / 'Area': by depth under the root

    def walk(n, depth, building, level):
        for c in kids.get(n["id"], []):
            k = kind_of(c, depth)
            if k == "building":
                out["buildings"].append({"id": c["id"], "name": c["name"]})
                walk(c, depth + 1, c, None)
            elif k == "level":
                out["levels"].append({"id": c["id"], "name": c["name"], "building_id": building["id"] if building else None})
                walk(c, depth + 1, building, c)
            else:
                out["zones"].append({"id": c["id"], "name": c["name"], "level_id": level["id"] if level else None})
                # deeper nodes fold into this zone: do not recurse as separate zones

    walk(root, 1, None, None)
    return out
