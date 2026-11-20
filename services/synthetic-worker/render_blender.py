"""Blender (GPL) side of the synthetic worker. Run out of process:
    blender -b --factory-startup -P render_blender.py -- --work W --out O [--captures 4 --frames 6 --width 1024 --height 512 --samples 16 --seed 0]

Reads W/{scene.obj,elements.json,spaces.json} (prestep.py).  Per *capture* (= one point in time) every labelled element gets a random
install rung of its trade ladder, realised as geometry + procedural materials; then `frames` virtual 360 cameras (Cycles equirectangular)
walk a path through the spaces.  Per frame it writes O/<capture>/f####_{rgb.png, id.png, depth_*.png} and, per capture, capture.json.
id.png encodes the BIM element id (elements.json "id") as 24-bit RGB of a flat emission render of the *BIM* meshes only: the same
"visible pixels of the BIM element" mask the API projection produces, independent of the staged geometry/clutter.
"""
from __future__ import annotations

import json
import math
import os
import random
import sys
from pathlib import Path

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector

LADDERS = {  # percent rungs, identical to fauxel_api.vision.ladders (states looked up by post-step)
    "arch_interior": [0, 25, 50, 65, 80, 95, 100], "hvac": [0, 30, 80, 100], "electrical": [0, 30, 80, 100],
    "plumbing_fp": [0, 30, 80, 100], "structural": [0, 40, 100], "other": [0, 100]}
STATE_OF = {  # (trade, percent) -> state
    **{("arch_interior", p): s for p, s in ((0, "NOT_STARTED"), (25, "FRAMED_OR_ROUGHED"), (50, "FRAMED_OR_ROUGHED"), (65, "FRAMED_OR_ROUGHED"),
                                           (80, "INSTALLED"), (95, "INSTALLED"), (100, "INSULATED_OR_FINISHED"))},
    **{(t, p): s for t in ("hvac", "electrical", "plumbing_fp") for p, s in ((0, "NOT_STARTED"), (30, "FRAMED_OR_ROUGHED"), (80, "INSTALLED"), (100, "INSULATED_OR_FINISHED"))},
    ("structural", 0): "NOT_STARTED", ("structural", 40): "FRAMED_OR_ROUGHED", ("structural", 100): "INSTALLED",
    ("other", 0): "NOT_STARTED", ("other", 100): "INSTALLED"}


def args():
    a = sys.argv[sys.argv.index("--") + 1:]
    d = {"captures": 4, "frames": 6, "width": 1024, "height": 512, "samples": 16, "seed": 0, "work": None, "out": None}
    i = 0
    while i < len(a):
        k = a[i].lstrip("-")
        d[k] = type(d[k])(a[i + 1]) if d[k] is not None else a[i + 1]
        i += 2
    return d


# ------------------------------------------------------------------ materials

def mat(name, color, rough=0.6, metal=0.0, noise=0.0, scale=40.0, dirt=0.0, bump=0.0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    b = nt.nodes["Principled BSDF"]
    b.inputs["Roughness"].default_value, b.inputs["Metallic"].default_value = rough, metal
    col = nt.nodes.new("ShaderNodeRGB")
    col.outputs[0].default_value = (*color, 1)
    out = col.outputs[0]
    if noise or dirt or bump:
        tex = nt.nodes.new("ShaderNodeTexNoise")
        tex.inputs["Scale"].default_value, tex.inputs["Detail"].default_value = scale, 8
        mix = nt.nodes.new("ShaderNodeMixRGB")
        mix.blend_type = "MULTIPLY"
        mix.inputs["Fac"].default_value = noise
        nt.links.new(col.outputs[0], mix.inputs["Color1"])
        nt.links.new(tex.outputs["Color"], mix.inputs["Color2"])
        out = mix.outputs[0]
        if dirt:  # dust / grime: darken + warm by low-frequency noise
            t2 = nt.nodes.new("ShaderNodeTexNoise")
            t2.inputs["Scale"].default_value = 3.0
            dm = nt.nodes.new("ShaderNodeMixRGB")
            dm.inputs["Fac"].default_value = dirt
            dm.inputs["Color2"].default_value = (0.25, 0.2, 0.15, 1)
            nt.links.new(out, dm.inputs["Color1"])
            nt.links.new(t2.outputs["Fac"], dm.inputs["Fac"])
            out = dm.outputs[0]
        if bump:
            bn = nt.nodes.new("ShaderNodeBump")
            bn.inputs["Strength"].default_value = bump
            nt.links.new(tex.outputs["Fac"], bn.inputs["Height"])
            nt.links.new(bn.outputs["Normal"], b.inputs["Normal"])
    nt.links.new(out, b.inputs["Base Color"])
    return m


def seam_mat(name, base, seam, seam_w, rough=0.9, joint=0.0):
    """Drywall: flat board colour with a brick-texture grid of 1.2 x 2.4 m sheets; `seam` colour on joints (taped joints stand out)."""
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    b = nt.nodes["Principled BSDF"]
    b.inputs["Roughness"].default_value = rough
    br = nt.nodes.new("ShaderNodeTexBrick")
    br.inputs["Color1"].default_value = br.inputs["Color2"].default_value = (*base, 1)
    br.inputs["Mortar"].default_value = (*seam, 1)
    br.inputs["Mortar Size"].default_value = seam_w
    br.inputs["Scale"].default_value = 1.0
    br.inputs["Brick Width"].default_value, br.inputs["Row Height"].default_value = 1.2, 2.4
    br.offset = 0.0
    tc = nt.nodes.new("ShaderNodeTexCoord")
    nt.links.new(tc.outputs["Object"], br.inputs["Vector"])
    nt.links.new(br.outputs["Color"], b.inputs["Base Color"])
    return m


def hsv(h, s, v):
    import colorsys
    return colorsys.hsv_to_rgb(h, s, v)


def make_materials(rng):
    M = {}
    M["concrete"] = mat("concrete", (0.5, 0.5, 0.5), 0.9, noise=0.6, scale=rng.uniform(15, 60), dirt=rng.uniform(0.1, 0.5), bump=0.3)
    M["stud"] = mat("stud", (0.62, 0.64, 0.66), 0.35, 0.9)
    M["insul"] = mat("insul", (0.9, 0.6, 0.65) if rng.random() < 0.5 else (0.95, 0.85, 0.35), 0.95, noise=0.5, scale=120, bump=0.4)
    M["board"] = seam_mat("board", (0.72, 0.72, 0.7), (0.66, 0.66, 0.64), 0.004)
    M["taped"] = seam_mat("taped", (0.74, 0.74, 0.72), (0.93, 0.93, 0.9), 0.05)
    M["paint"] = mat("paint", hsv(rng.random(), rng.uniform(0.0, 0.5), rng.uniform(0.55, 0.95)), rng.uniform(0.4, 0.8), noise=0.08, scale=200, bump=0.05)
    M["galv"] = mat("galv", (0.7, 0.72, 0.74), 0.35, 1.0, noise=0.3, scale=30, dirt=rng.uniform(0, 0.3))
    M["duct_insul"] = mat("duct_insul", (0.85, 0.85, 0.8), 0.9, noise=0.3, scale=60, bump=0.3)
    M["conduit"] = mat("conduit", (0.15, 0.15, 0.17), 0.4, 0.6)
    M["pipe_red"] = mat("pipe_red", (0.7, 0.05, 0.03), 0.4, 0.2)
    M["pipe_black"] = mat("pipe_black", (0.02, 0.02, 0.02), 0.9)
    M["rod"] = mat("rod", (0.45, 0.45, 0.47), 0.5, 0.8)
    M["wood"] = mat("wood", (0.55, 0.38, 0.2), 0.8, noise=0.5, scale=8, bump=0.4)
    M["rebar"] = mat("rebar", (0.3, 0.2, 0.15), 0.7, 0.5)
    M["context"] = mat("context", (0.6, 0.6, 0.58), 0.9, noise=0.4, scale=25, dirt=0.3)
    return M


# ------------------------------------------------------------------ geometry helpers

def add_obj(name, bm, material, coll):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    ob.data.materials.append(material)
    coll.objects.link(ob)
    return ob


def box(name, center, dims, rotz, material, coll):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=Vector(dims), verts=bm.verts)
    bmesh.ops.rotate(bm, cent=(0, 0, 0), matrix=Matrix.Rotation(rotz, 3, "Z"), verts=bm.verts)
    bmesh.ops.translate(bm, vec=Vector(center), verts=bm.verts)
    return add_obj(name, bm, material, coll)


def cyl(name, p0, p1, r, material, coll):
    p0, p1 = Vector(p0), Vector(p1)
    d = p1 - p0
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=8, radius1=r, radius2=r, depth=d.length)
    bmesh.ops.rotate(bm, cent=(0, 0, 0), matrix=Vector((0, 0, 1)).rotation_difference(d).to_matrix(), verts=bm.verts)
    bmesh.ops.translate(bm, vec=(p0 + p1) / 2, verts=bm.verts)
    return add_obj(name, bm, material, coll)


def load_obj(path, coll):
    objs, verts, faces, cur = {}, [], {}, None
    for line in open(path):
        if line.startswith("o "):
            cur = line[2:].strip()
            faces[cur] = []
        elif line.startswith("v "):
            verts.append(tuple(float(x) for x in line.split()[1:4]))
        elif line.startswith("f "):
            faces[cur].append(tuple(int(x) - 1 for x in line.split()[1:4]))
    V = np.array(verts, float)
    for name, fs in faces.items():
        used = sorted({i for f in fs for i in f})
        remap = {g: l for l, g in enumerate(used)}
        me = bpy.data.meshes.new(name)
        me.from_pydata([verts[i] for i in used], [], [tuple(remap[i] for i in f) for f in fs])
        me.update()
        ob = bpy.data.objects.new(name, me)
        coll.objects.link(ob)
        objs[name] = (ob, V[used])
    return objs


def frame_axes(el, verts):
    """Centre, unit long axis u (horizontal), unit side axis v, and extents (L along u, T along v, H) of an element from its vertices."""
    P = verts[:, :2]
    c2 = P.mean(0)
    w, Vv = np.linalg.eigh(np.cov((P - c2).T) + 1e-9 * np.eye(2))
    u = Vv[:, 1]
    v = np.array([-u[1], u[0]])
    L = np.ptp((P - c2) @ u)
    T = np.ptp((P - c2) @ v)
    z0, z1 = verts[:, 2].min(), verts[:, 2].max()
    return c2, u, v, L, T, z0, z1


# ------------------------------------------------------------------ element states

def stage_wall(el, ob, verts, rung_pct, M, coll, rng, made):
    c2, u, v, L, T, z0, z1 = frame_axes(el, verts)
    H = z1 - z0
    rz = math.atan2(u[1], u[0])
    ctr = lambda s, z: (c2[0] + u[0] * s, c2[1] + u[1] * s, z)
    ob.hide_render = rung_pct < 80
    if rung_pct >= 80:
        ob.data.materials[0] = {80: M["board"], 95: M["taped"], 100: M["paint"]}[rung_pct]
        return
    if rung_pct >= 25:
        for z in (z0 + 0.02, z1 - 0.02):
            made.append(box(f"track{el['idx']}", ctr(0, z), (L, 0.09, 0.035), rz, M["stud"], coll))
    if rung_pct >= 50:
        n = max(2, int(L / 0.406) + 1)
        for i in range(n):
            s = -L / 2 + 0.02 + i * (L - 0.04) / (n - 1)
            made.append(box(f"stud{el['idx']}", ctr(s, (z0 + z1) / 2), (0.04, 0.09, H - 0.07), rz, M["stud"], coll))
    if rung_pct >= 65:
        h = rng.uniform(0.3, 1.0)  # in-wall MEP: a conduit run + insulation batts between studs
        made.append(cyl(f"cond{el['idx']}", ctr(-L / 2, z0 + h), ctr(L / 2, z0 + h), 0.012, M["conduit"], coll))
        n = max(2, int(L / 0.406) + 1)
        for i in range(n - 1):
            if rng.random() < 0.9:
                s = -L / 2 + 0.02 + (i + 0.5) * (L - 0.04) / (n - 1)
                made.append(box(f"batt{el['idx']}", ctr(s, (z0 + z1) / 2), ((L - 0.04) / (n - 1) - 0.045, 0.08, H - 0.1), rz, M["insul"], coll))


def stage_linear(el, ob, verts, pct, M, coll, rng, made, ceil_z):
    c2, u, v, L, T, z0, z1 = frame_axes(el, verts)
    rz = math.atan2(u[1], u[0])
    zc = (z0 + z1) / 2
    tr = el["trade"]
    ob.hide_render = pct < 80
    if pct >= 30:  # trapeze hangers every ~1.5 m
        n = max(2, int(L / 1.5) + 1)
        for i in range(n):
            s = -L / 2 + 0.2 + i * (L - 0.4) / (n - 1)
            p = (c2[0] + u[0] * s, c2[1] + u[1] * s)
            for side in (-1, 1):
                q = (p[0] + v[0] * side * (T / 2 + 0.05), p[1] + v[1] * side * (T / 2 + 0.05))
                made.append(cyl(f"rod{el['idx']}", (q[0], q[1], z0 - 0.03), (q[0], q[1], ceil_z), 0.006, M["rod"], coll))
            made.append(box(f"trap{el['idx']}", (p[0], p[1], z0 - 0.03), (0.04, T + 0.16, 0.04), rz, M["rod"], coll))
    if pct >= 80:
        ob.data.materials[0] = {"hvac": M["galv"], "electrical": M["conduit"], "plumbing_fp": M["pipe_red"]}[tr]
    if pct >= 100:
        if tr in ("hvac", "plumbing_fp"):  # insulation / wrap: oversize shell around the element
            sh = ob.copy()
            sh.data = ob.data.copy()
            sh.data.materials.clear()
            sh.data.materials.append(M["duct_insul"] if tr == "hvac" else M["pipe_black"])
            coll.objects.link(sh)
            sh.hide_render = False
            sc = 1.0 + 0.12 / max(T, 0.05)
            sh.scale = (1, 1, 1)
            sh.location = Vector((0, 0, 0))
            for vtx in sh.data.vertices:  # grow vertices away from the element axis
                p = Vector(vtx.co)
                a = Vector((c2[0], c2[1], zc))
                d = p - a
                al = Vector((u[0], u[1], 0))
                rad = d - al * d.dot(al)
                vtx.co = p + rad * (sc - 1)
            made.append(sh)
        else:  # electrical: devices / covers on the tray
            for i in range(3):
                s = rng.uniform(-L / 2, L / 2)
                made.append(box(f"dev{el['idx']}", (c2[0] + u[0] * s, c2[1] + u[1] * s, z1 + 0.05), (0.12, 0.12, 0.1), rz, M["paint"], coll))


def stage_structural(el, ob, verts, pct, M, coll, rng, made):
    c2, u, v, L, T, z0, z1 = frame_axes(el, verts)
    rz = math.atan2(u[1], u[0])
    ob.hide_render = pct < 100
    ob.data.materials[0] = M["concrete"]
    if pct == 40:  # formwork + rebar grid
        zc = (z0 + z1) / 2
        for side in (-1, 1):
            made.append(box(f"form{el['idx']}", (c2[0] + v[0] * side * (T / 2 + 0.02), c2[1] + v[1] * side * (T / 2 + 0.02), zc),
                            (L + 0.04, 0.03, z1 - z0 + 0.04), rz, M["wood"], coll))
        for i in range(8):
            s = -L / 2 + (i + 0.5) * L / 8
            made.append(cyl(f"rebar{el['idx']}", (c2[0] + u[0] * s, c2[1] + u[1] * s, z0 + 0.03), (c2[0] + u[0] * s, c2[1] + u[1] * s, z1 - 0.03), 0.008, M["rebar"], coll))
        for z in np.linspace(z0 + 0.05, z1 - 0.05, 4):
            made.append(cyl(f"rebar{el['idx']}", (c2[0] - u[0] * L / 2, c2[1] - u[1] * L / 2, z), (c2[0] + u[0] * L / 2, c2[1] + u[1] * L / 2, z), 0.008, M["rebar"], coll))


# ------------------------------------------------------------------ scene

def set_cam_pano(cam):
    cam.type = "PANO"
    try:
        cam.panorama_type = "EQUIRECTANGULAR"
    except AttributeError:
        cam.cycles.panorama_type = "EQUIRECTANGULAR"
    cam.clip_start, cam.clip_end = 0.05, 200


def lights(rng, space, coll):
    out = []
    for i in range(rng.randint(1, 3)):
        li = bpy.data.lights.new(f"L{i}", rng.choice(["AREA", "POINT"]))
        li.energy = rng.uniform(30, 600)
        li.color = (1.0, rng.uniform(0.8, 1.0), rng.uniform(0.6, 1.0))
        if li.type == "AREA":
            li.size = rng.uniform(0.5, 2.0)
        ob = bpy.data.objects.new(f"L{i}", li)
        ob.location = (rng.uniform(space["min"][0] + .3, space["max"][0] - .3), rng.uniform(space["min"][1] + .3, space["max"][1] - .3),
                       space["max"][2] - rng.uniform(0.1, 0.5))
        coll.objects.link(ob)
        out.append(ob)
    return out


def clutter(rng, space, M, coll, n):
    out = []
    for i in range(n):
        x, y = rng.uniform(space["min"][0] + .4, space["max"][0] - .4), rng.uniform(space["min"][1] + .4, space["max"][1] - .4)
        z0 = space["min"][2]
        m = rng.choice([M["wood"], M["conduit"], M["paint"], M["rod"], M["context"]])
        if rng.random() < 0.6:
            d = (rng.uniform(.2, 1.2), rng.uniform(.2, 1.2), rng.uniform(.1, 1.0))
            out.append(box(f"clut{i}", (x, y, z0 + d[2] / 2), d, rng.uniform(0, 3.14), m, coll))
        else:
            out.append(cyl(f"clut{i}", (x, y, z0), (x, y, z0 + rng.uniform(.2, 1.0)), rng.uniform(.1, .3), m, coll))
    return out


def id_material(i):
    m = bpy.data.materials.new(f"id{i}")
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Color"].default_value = (((i >> 16) & 255) / 255, ((i >> 8) & 255) / 255, (i & 255) / 255, 1)
    em.inputs["Strength"].default_value = 1.0
    o = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(em.outputs[0], o.inputs["Surface"])
    return m


def path_points(spaces, n, rng):
    pts = []
    for sp in spaces or [{"min": [0, 0, 0], "max": [5, 5, 3]}]:
        k = max(1, math.ceil(n / max(len(spaces), 1)))
        for j in range(k):
            t = (j + 0.5) / k
            pts.append((sp["min"][0] + 0.8 + t * (sp["max"][0] - sp["min"][0] - 1.6) + rng.uniform(-.3, .3),
                        sp["min"][1] + (sp["max"][1] - sp["min"][1]) * rng.uniform(0.35, 0.65), sp["min"][2] + rng.uniform(1.4, 1.7)))
    return pts[:n]


def main():
    A = args()
    work, out = Path(A["work"]), Path(A["out"])
    rng = random.Random(A["seed"])
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    elements = json.loads((work / "elements.json").read_text())
    spaces = json.loads((work / "spaces.json").read_text())
    bim = bpy.data.collections.new("bim")
    sc.collection.children.link(bim)
    objs = load_obj(work / "scene.obj", bim)
    by_idx = {e["idx"]: e for e in elements}
    allmin = np.min([e["min"] for e in elements], 0)
    allmax = np.max([e["max"] for e in elements], 0)
    if not spaces:
        spaces = [{"name": "all", "min": allmin.tolist(), "max": allmax.tolist()}]
    ceil_z = float(allmax[2])
    cam_d = bpy.data.cameras.new("pano")
    set_cam_pano(cam_d)
    cam = bpy.data.objects.new("pano", cam_d)
    sc.collection.objects.link(cam)
    sc.camera = cam
    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    sc.render.resolution_x, sc.render.resolution_y, sc.render.resolution_percentage = A["width"], A["height"], 100
    sc.render.image_settings.file_format, sc.render.image_settings.color_mode = "PNG", "RGB"
    sc.render.dither_intensity = 0.0  # 8-bit dither would corrupt the id image
    sc.world = bpy.data.worlds.new("w")
    sc.world.use_nodes = True
    id_mats = {e["idx"]: id_material(e["id"]) for e in elements}
    sc.view_layers[0].use_pass_z = True
    sc.use_nodes = True
    nt = sc.node_tree
    nt.nodes.clear()
    rl = nt.nodes.new("CompositorNodeRLayers")
    fo = nt.nodes.new("CompositorNodeOutputFile")
    fo.format.file_format, fo.format.color_depth, fo.format.color_mode = "PNG", "16", "BW"  # depth as uint16 millimetres (65535 = no hit)
    fo.file_slots[0].path = "depth_"
    mm = nt.nodes.new("CompositorNodeMath")
    mm.operation, mm.inputs[1].default_value = "MULTIPLY", 1.0 / 65.535
    nt.links.new(rl.outputs["Depth"], mm.inputs[0])
    nt.links.new(mm.outputs[0], fo.inputs[0])

    for ci in range(A["captures"]):
        cid = f"{A.get('name', 'syn')}-s{A['seed']}-c{ci}"
        cdir = out / cid
        cdir.mkdir(parents=True, exist_ok=True)
        M = make_materials(rng)
        stage = bpy.data.collections.new(f"stage{ci}")
        sc.collection.children.link(stage)
        made, labels = [], {}
        progress = rng.random()
        for idx, (ob, verts) in objs.items():
            el = by_idx[int(idx[1:])]
            ob.hide_render = False
            ob.data.materials.clear()
            ob.data.materials.append(M["context"])
            tr, cls = el["trade"], el["ifc_class"]
            if tr == "other" or (tr == "arch_interior" and not cls.startswith("IfcWall")) or (tr == "structural" and not cls.startswith(("IfcBeam", "IfcColumn", "IfcSlab"))):
                continue  # visible context only, no label
            ladder = LADDERS[tr]
            k = min(len(ladder) - 1, max(0, round((progress + rng.gauss(0, 0.25)) * (len(ladder) - 1))))
            if rng.random() < 0.25:
                k = rng.randrange(len(ladder))  # keep every rung represented
            pct = ladder[k]
            if tr == "arch_interior":
                stage_wall(el, ob, verts, pct, M, stage, rng, made)
            elif tr == "structural":
                stage_structural(el, ob, verts, pct, M, stage, rng, made)
            elif tr in ("hvac", "electrical", "plumbing_fp"):
                stage_linear(el, ob, verts, pct, M, stage, rng, made, ceil_z)
            labels[el["idx"]] = {"guid": el["guid"], "id": el["id"], "trade": tr, "ifc_class": cls, "percent": pct, "state": STATE_OF[(tr, pct)], "rung": k}
        sp0 = spaces[0]
        made.append(box("ground", ((allmin[0] + allmax[0]) / 2, (allmin[1] + allmax[1]) / 2, allmin[2] - 0.05),
                        (allmax[0] - allmin[0] + 40, allmax[1] - allmin[1] + 40, 0.1), 0, M["concrete"], stage))
        L = lights(rng, sp0, stage) + clutter(rng, sp0, M, stage, rng.randint(3, 12))
        wc = rng.uniform(0.05, 0.5)
        sc.world.node_tree.nodes["Background"].inputs["Color"].default_value = (wc, wc, wc * rng.uniform(.9, 1.1), 1)
        sc.world.node_tree.nodes["Background"].inputs["Strength"].default_value = rng.uniform(0.1, 0.6)
        frames = []
        for fi, p in enumerate(path_points(spaces, A["frames"], rng)):
            cam.location = p
            yaw = rng.uniform(0, 2 * math.pi)
            cam.rotation_euler = (math.pi / 2, 0, yaw)
            # beauty
            sc.render.use_compositing = False
            sc.cycles.samples, sc.cycles.use_denoising, sc.cycles.filter_width = A["samples"], True, 1.5
            sc.view_settings.view_transform, sc.view_settings.look = "Standard", "None"
            sc.render.filepath = str(cdir / f"f{fi:04d}_rgb.png")
            bpy.ops.render.render(write_still=True)
            # id + depth: BIM meshes only, flat emission, raw colour, no AA
            saved = {}
            for o in stage.objects:
                o.hide_render = True
            for idx, (ob, _) in objs.items():
                saved[idx] = (ob.hide_render, ob.data.materials[0])
                ob.hide_render = False
                ob.data.materials[0] = id_mats[int(idx[1:])]
            sc.render.use_compositing = True
            fo.base_path = str(cdir)
            fo.file_slots[0].path = f"f{fi:04d}_depth_"
            sc.cycles.samples, sc.cycles.use_denoising, sc.cycles.filter_width = 1, False, 0.01
            sc.view_settings.view_transform = "Raw"
            sc.world.node_tree.nodes["Background"].inputs["Color"].default_value = (0, 0, 0, 1)
            sc.world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.0
            sc.render.filepath = str(cdir / f"f{fi:04d}_id.png")
            bpy.ops.render.render(write_still=True)
            for idx, (ob, _) in objs.items():
                ob.hide_render, ob.data.materials[0] = saved[idx]
            for o in stage.objects:
                o.hide_render = False
            sc.world.node_tree.nodes["Background"].inputs["Color"].default_value = (wc, wc, wc, 1)
            sc.world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.3
            frames.append({"index": fi, "camera": list(p), "yaw": yaw, "rgb": f"f{fi:04d}_rgb.png", "id": f"f{fi:04d}_id.png"})
        (cdir / "capture.json").write_text(json.dumps({"capture_id": cid, "seed": A["seed"], "width": A["width"], "height": A["height"],
                                                       "progress_bias": progress, "elements": labels, "frames": frames}, indent=1))
        for o in list(stage.objects):
            bpy.data.objects.remove(o)
        bpy.data.collections.remove(stage)
        print(f"capture {cid}: {len(frames)} frames, {len(labels)} labelled elements")


main()
