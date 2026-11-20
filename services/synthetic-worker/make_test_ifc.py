"""Generates a small IFC4 test model: one 6 x 5 m room (4 walls, slab, space) with a duct, a sprinkler pipe, a cable tray and a beam.
Run: python3 make_test_ifc.py out.ifc   (needs ifcopenshell)"""
from __future__ import annotations

import sys

import numpy as np


def _m(x, y, z, rot_axis=None, deg=0.0):
    m = np.eye(4)
    if rot_axis is not None:
        a = np.radians(deg)
        c, s = np.cos(a), np.sin(a)
        m[:3, :3] = {"y": [[c, 0, s], [0, 1, 0], [-s, 0, c]], "z": [[c, -s, 0], [s, c, 0], [0, 0, 1]], "x": [[1, 0, 0], [0, c, -s], [0, s, c]]}[rot_axis]
    m[:3, 3] = (x, y, z)
    return m


def make(path: str) -> None:
    import ifcopenshell
    import ifcopenshell.api as api

    f = ifcopenshell.file(schema="IFC4")
    project = api.run("root.create_entity", f, ifc_class="IfcProject", name="Synthetic Test")
    api.run("unit.assign_unit", f, length={"is_metric": True, "raw": "METRE"})
    ctx = api.run("context.add_context", f, context_type="Model")
    body = api.run("context.add_context", f, context_type="Model", context_identifier="Body", target_view="MODEL_VIEW", parent=ctx)
    site = api.run("root.create_entity", f, ifc_class="IfcSite", name="Site")
    building = api.run("root.create_entity", f, ifc_class="IfcBuilding", name="B")
    storey = api.run("root.create_entity", f, ifc_class="IfcBuildingStorey", name="Level 1")
    api.run("aggregate.assign_object", f, products=[site], relating_object=project)
    api.run("aggregate.assign_object", f, products=[building], relating_object=site)
    api.run("aggregate.assign_object", f, products=[storey], relating_object=building)

    def place(prod, rep, matrix, contain=True):
        api.run("geometry.assign_representation", f, product=prod, representation=rep)
        api.run("geometry.edit_object_placement", f, product=prod, matrix=matrix)
        if contain:
            api.run("spatial.assign_container", f, products=[prod], relating_structure=storey)

    W, D, H, T = 6.0, 5.0, 3.0, 0.2
    walls = [("Wall S", 0, 0, W, 0), ("Wall N", 0, D, W, 0), ("Wall W", 0, 0, D, 90), ("Wall E", W, 0, D, 90)]
    for name, x, y, length, ang in walls:
        w = api.run("root.create_entity", f, ifc_class="IfcWall", name=name)
        place(w, api.run("geometry.add_wall_representation", f, context=body, length=length, height=H, thickness=T), _m(x, y, 0, "z", ang))
    slab = api.run("root.create_entity", f, ifc_class="IfcSlab", name="Slab")
    place(slab, api.run("geometry.add_wall_representation", f, context=body, length=W, height=0.2, thickness=D), _m(0, 0, -0.2))
    duct = api.run("root.create_entity", f, ifc_class="IfcDuctSegment", name="Duct 1")
    prof = f.create_entity("IfcRectangleProfileDef", ProfileType="AREA", XDim=0.5, YDim=0.3)
    place(duct, api.run("geometry.add_profile_representation", f, context=body, profile=prof, depth=4.0), _m(1.0, 2.5, 2.6, "y", 90))
    pipe = api.run("root.create_entity", f, ifc_class="IfcPipeSegment", name="FP Main")
    place(pipe, api.run("geometry.add_profile_representation", f, context=body, profile=f.create_entity(
        "IfcCircleProfileDef", ProfileType="AREA", Radius=0.05), depth=4.5), _m(0.8, 1.2, 2.8, "y", 90))
    tray = api.run("root.create_entity", f, ifc_class="IfcCableCarrierSegment", name="Tray 1")
    place(tray, api.run("geometry.add_profile_representation", f, context=body, profile=f.create_entity(
        "IfcRectangleProfileDef", ProfileType="AREA", XDim=0.3, YDim=0.08), depth=4.0), _m(1.0, 3.8, 2.7, "y", 90))
    beam = api.run("root.create_entity", f, ifc_class="IfcBeam", name="Beam 1")
    place(beam, api.run("geometry.add_profile_representation", f, context=body, profile=f.create_entity(
        "IfcRectangleProfileDef", ProfileType="AREA", XDim=0.3, YDim=0.4), depth=D), _m(3.0, 0.0, 2.6, "x", -90))
    space = api.run("root.create_entity", f, ifc_class="IfcSpace", name="Room 101")
    place(space, api.run("geometry.add_wall_representation", f, context=body, length=W, height=H, thickness=D), _m(0, 0, 0), contain=False)
    api.run("aggregate.assign_object", f, products=[space], relating_object=storey)
    f.write(path)


if __name__ == "__main__":
    make(sys.argv[1] if len(sys.argv) > 1 else "test.ifc")
