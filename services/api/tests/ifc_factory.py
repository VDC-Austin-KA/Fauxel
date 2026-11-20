"""Builds a tiny IFC4 file with ifcopenshell.api: 1 storey, 2 walls, 1 duct segment, 1 space."""
from __future__ import annotations

import numpy as np


def _matrix(x, y, z):
    m = np.eye(4)
    m[:3, 3] = (x, y, z)
    return m


def make_ifc(path: str, unit: float = 1.0, mm: bool = False) -> None:
    """unit multiplies every length passed to the file (1000 with mm=True writes a millimetre model)."""
    import ifcopenshell
    import ifcopenshell.api as api

    f = ifcopenshell.file(schema="IFC4")
    project = api.run("root.create_entity", f, ifc_class="IfcProject", name="Test Project")
    if mm:
        api.run("unit.assign_unit", f, length={"is_metric": True, "raw": "MILLIMETRE"})
    else:
        api.run("unit.assign_unit", f, length={"is_metric": True, "raw": "METRE"})
    ctx = api.run("context.add_context", f, context_type="Model")
    body = api.run("context.add_context", f, context_type="Model", context_identifier="Body", target_view="MODEL_VIEW", parent=ctx)
    site = api.run("root.create_entity", f, ifc_class="IfcSite", name="Site")
    building = api.run("root.create_entity", f, ifc_class="IfcBuilding", name="Main Building")
    storey = api.run("root.create_entity", f, ifc_class="IfcBuildingStorey", name="Level 1")
    storey.Elevation = 0.0
    api.run("aggregate.assign_object", f, products=[site], relating_object=project)
    api.run("aggregate.assign_object", f, products=[building], relating_object=site)
    api.run("aggregate.assign_object", f, products=[storey], relating_object=building)

    u = unit
    for name, y in (("Wall A", 1.0), ("Wall B", 3.0)):
        wall = api.run("root.create_entity", f, ifc_class="IfcWall", name=name)
        api.run("geometry.assign_representation", f, product=wall,
                representation=api.run("geometry.add_wall_representation", f, context=body, length=5 * u, height=3 * u, thickness=0.2 * u))
        api.run("geometry.edit_object_placement", f, product=wall, matrix=_matrix(0, y * u, 0))
        api.run("spatial.assign_container", f, products=[wall], relating_structure=storey)
        if name == "Wall A":
            qto = api.run("pset.add_qto", f, product=wall, name="Qto_WallBaseQuantities")
            api.run("pset.edit_qto", f, qto=qto, properties={"NetSideArea": 15.0 * u * u, "Length": 5.0 * u})

    duct = api.run("root.create_entity", f, ifc_class="IfcDuctSegment", name="Duct 1")
    profile = f.create_entity("IfcRectangleProfileDef", ProfileType="AREA", XDim=0.4 * u, YDim=0.3 * u)
    api.run("geometry.assign_representation", f, product=duct,
            representation=api.run("geometry.add_profile_representation", f, context=body, profile=profile, depth=1.0 * u))
    api.run("geometry.edit_object_placement", f, product=duct, matrix=_matrix(2 * u, 2 * u, 1.5 * u))
    api.run("spatial.assign_container", f, products=[duct], relating_structure=storey)

    space = api.run("root.create_entity", f, ifc_class="IfcSpace", name="Room 101")
    api.run("geometry.assign_representation", f, product=space,
            representation=api.run("geometry.add_wall_representation", f, context=body, length=5 * u, height=3 * u, thickness=4 * u))
    api.run("geometry.edit_object_placement", f, product=space, matrix=_matrix(0, 0, 0))
    api.run("aggregate.assign_object", f, products=[space], relating_object=storey)
    f.write(path)
