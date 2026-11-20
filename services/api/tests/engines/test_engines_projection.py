import time

import numpy as np

from fauxel_api.vision.projection import ElementGeom, project_elements

W, H = 1024, 512
# camera at origin looking north (+Y), up = +Z, right = +X
R_NORTH = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], float)


def pose(R=R_NORTH, t=(0, 0, 0)):
    T = np.eye(4)
    T[:3, :3] = R
    T[:3, 3] = t
    return T


def box(uid, c, s=1.0):
    c = np.array(c, float)
    return ElementGeom(uid, tuple(c - s / 2), tuple(c + s / 2))


def test_box_ahead_is_centered():
    vis = project_elements(pose(), [box("a", (0, 5, 0))], W, H)
    assert len(vis) == 1
    v = vis[0]
    ys, xs = np.nonzero(v.mask)
    assert abs(xs.mean() - W / 2) < 3 and abs(ys.mean() - H / 2) < 3
    assert v.occlusion_ratio < 0.01
    assert abs(v.distance_m - 5) < 0.6
    assert 0 < v.view_quality <= 1
    pu = np.mean([p[0] for p in v.polygon])
    assert abs(pu - 0.5) < 0.01


def test_box_behind_another_is_occluded():
    near = box("near", (0, 4, 0), 2.0)
    far = box("far", (0, 8, 0), 1.0)
    vis = {v.element_uid: v for v in project_elements(pose(), [far, near], W, H)}
    assert vis["near"].occlusion_ratio < 0.01
    assert "far" not in vis or vis["far"].occlusion_ratio > 0.9


def test_seam_crossing_box():
    # camera looks north, box straight behind (south) -> azimuth +-pi -> columns 0 and W-1
    vis = project_elements(pose(), [box("s", (0, -5, 0))], W, H)
    assert len(vis) == 1
    v = vis[0]
    assert v.seam and v.mask[:, 0].any() and v.mask[:, -1].any()
    xs = np.array([p[0] for p in v.polygon])
    # shorter wrap: polygon vertices stay near the seam, none near the image centre
    assert np.all((xs < 0.1) | (xs > 0.9))
    assert v.pixel_bbox[0] > v.pixel_bbox[2]
    # angular width of a 1 m box at 5 m is ~11.4 deg -> ~32 px
    ys, xs = np.nonzero(v.mask)
    assert 25 < v.mask.any(0).sum() < 45


def test_distance_culling():
    els = [box("in", (0, 10, 0)), box("out", (0, 30, 0))]
    uids = {v.element_uid for v in project_elements(pose(), els, W, H, max_dist_m=15)}
    assert uids == {"in"}


def test_triangle_mesh_and_edge_curvature():
    # a wide wall 10 m across at 2 m distance: its top edge must curve in v
    tri = np.array([[[-5, 2, 1], [5, 2, 1], [5, 2, -1]], [[-5, 2, 1], [5, 2, -1], [-5, 2, -1]]], float)
    e = ElementGeom("wall", (-5, 2, -1), (5, 2, 1), tri)
    v = project_elements(pose(), [e], W, H)[0]
    xc = W // 2 + 150
    top = [np.nonzero(v.mask[:, x])[0].min() for x in (W // 2, xc)]
    th = (xc / W - 0.5) * 2 * np.pi  # azimuth of that column
    x_m = 2 * np.tan(th)
    exp_v = H * (np.arcsin(1 / np.sqrt(x_m**2 + 4 + 1)) / -np.pi + 0.5) - 0.5
    assert top[1] > top[0] + 20  # edge sags toward the horizon away from the centre
    assert abs(top[1] - exp_v) < 3
    assert v.mask.sum() > 5000


def test_performance_2000_boxes():
    rng = np.random.default_rng(0)
    cs = rng.uniform(-14, 14, (2000, 3))
    cs[:, 2] = rng.uniform(-1.5, 3, 2000)
    els = [box(f"e{i}", c, rng.uniform(0.3, 1.5)) for i, c in enumerate(cs)]
    t0 = time.time()
    vis = project_elements(pose(t=(0, 0, 1.5)), els, W, H)
    dt = time.time() - t0
    print(f"project_elements 2000 boxes: {dt:.2f}s, visible={len(vis)}")
    assert len(vis) > 100
    assert dt < 8
