"""Speckle adapter (pure): extraction, bim-export mapping, diff, and the progress bundle built with real specklepy."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from fauxel_openbim import speckle as sp

CONTRACTS = Path(__file__).resolve().parents[3] / "contracts"
G = "2O2Fr$t4X7Zf8NOew3FLOH"
RV = "a1b2c3d4-0000-4000-8000-000000000001-0000abcd"


class FakeMesh:
    def __init__(self, v, units="mm"):
        self.vertices, self.units = v, units


class FakeGeom:
    def __init__(self, mesh):
        self._m = mesh

    def decode_mesh(self):
        return self._m


class FakeObj:
    def __init__(self, app_id, name, props, geoms=(), level=None):
        self.application_id, self.name, self.geometries, self.level = app_id, name, list(geoms), level
        self._p = props

    def get_string(self, path):
        return self._p.get(path)


def box_mm(mn, mx):
    (x0, y0, z0), (x1, y1, z1) = mn, mx
    return FakeGeom(FakeMesh([x0, y0, z0, x1, y1, z1, x0, y1, z0]))


def model():
    return SimpleNamespace(units="m", objects=[
        FakeObj(G, "Wall", {"category": "Walls", "IfcGuid": G}, [box_mm((0, 0, 0), (5000, 200, 3000))], SimpleNamespace(name="L1")),
        FakeObj(RV, "Duct", {"category": "Ducts"}, [box_mm((0, 0, 4000), (2000, 400, 4300))], SimpleNamespace(name="L2")),
        FakeObj("opaque-id", "Mystery", {"category": "Walls"}, [box_mm((0, 0, 0), (1, 1, 1))]),
        FakeObj(G[::-1], "NoGeom", {"category": "Doors"}, []),
    ])


def test_extract_converts_units_and_reads_identity():
    els = sp.extract_elements(model())
    w = els[0]
    assert w["ifc_guid"] == G and w["level"] == "L1" and w["bbox"] == ([0, 0, 0], [5.0, 0.2, 3.0])
    assert els[3]["bbox"] is None


def test_to_bim_export_is_contract_valid_and_reports_skips():
    jsonschema = pytest.importorskip("jsonschema")
    payload, rep = sp.to_bim_export("speckle:m1", sp.extract_elements(model()))
    jsonschema.Draft202012Validator(json.loads((CONTRACTS / "bim-export.schema.json").read_text())).validate(payload)
    assert (rep["elements"], rep["skipped_no_id"], rep["skipped_no_geometry"]) == (2, 1, 1)
    by = {e["name"]: e for e in payload["elements"]}
    assert by["Wall"]["ifc_global_id"] == G and by["Duct"]["revit_unique_id"] == RV and by["Duct"]["trade"]
    assert {l["name"] for l in payload["levels"]} == {"L1", "L2"}


def test_classify_id():
    assert sp.classify_id(RV, None)["revit_unique_id"] == RV
    assert sp.classify_id(G, None)["ifc_global_id"] == G
    assert not any(sp.classify_id("nope", "short").values())


def test_diff():
    cur = [{"ifc_global_id": "A", "name": "a", "level": "L1", "aabb_min": [0, 0, 0], "aabb_max": [1, 1, 1]},
           {"ifc_global_id": "B", "name": "b", "level": "L1", "aabb_min": [0, 0, 0], "aabb_max": [2, 2, 2]},
           {"ifc_global_id": "N", "name": "n", "level": None, "aabb_min": [0, 0, 0], "aabb_max": [1, 1, 1]}]
    prev = {"A": {"name": "a", "level": "L1", "aabb_min": [0, 0, 0], "aabb_max": [1, 1, 1]},
            "B": {"name": "b", "level": "L1", "aabb_min": [0, 0, 0], "aabb_max": [1, 1, 1]},
            "X": {"name": "x", "level": None, "aabb_min": [0, 0, 0], "aabb_max": [1, 1, 1]}}
    d = sp.diff_elements(prev, cur)
    assert d == {"added": ["N"], "removed": ["X"], "changed": ["B"], "unchanged": 1}


def test_progress_bundle_builds_with_specklepy(tmp_path):
    pytest.importorskip("specklepy.bundle")
    rows = [{"application_id": G, "name": "Wall", "state": "INSTALLED", "percent": 100, "color": "#2ECC71", "confidence": 0.9,
             "last_verified": None, "trade": "arch_interior", "level": "L1", "level_elevation": 0.0,
             "aabb_min": [0, 0, 0], "aabb_max": [5, 0.2, 3]},
            {"application_id": RV, "name": "Duct", "state": "DEVIATION_FLAGGED", "percent": 50, "color": "#E74C3C", "confidence": None,
             "last_verified": None, "trade": "hvac", "level": None, "level_elevation": None, "aabb_min": None, "aabb_max": None}]
    b = sp.build_progress_bundle(rows, output_dir=str(tmp_path))
    assert b is not None
    assert sp.hex_to_argb("#2ECC71") == 0xFF2ECC71
    m = sp.box_mesh([0, 0, 0], [1, 1, 1])
    assert len(m.vertices) == 24 and len(m.faces) == 30
