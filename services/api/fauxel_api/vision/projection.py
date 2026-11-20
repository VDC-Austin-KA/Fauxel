"""Equirectangular z-buffer projection of BIM elements into a 360 frame.

Conventions (match fauxel_capture.alignment): camera frame is OpenCV (+X right, +Y down, +Z forward),
u = W*(atan2(x,z)/2pi+0.5)-0.5, v = H*(asin(y/|p|)/pi+0.5)-0.5, pixel centres at integer (u,v).
T_bim_from_cam maps camera axes into the project frame (+X E, +Y N, +Z up); translation = camera position.

Method: cull elements by bounding sphere, subdivide every triangle so no edge exceeds ~PX_STEP pixels
(straight 3D edges then curve correctly in (u,v)), project, unwrap the azimuth seam per sub-triangle,
rasterise with barycentric tests (vectorised over sub-triangles, looped over bbox offsets) and resolve
per-pixel nearest range with one sort. Depth is range |p| interpolated linearly in (u,v).
Limitations: sub-pixel elements can vanish; triangles over the poles are not seam-handled.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

PX_STEP = 8.0
MAX_SUBDIV = 32
_DQ = 1 << 24

_BOX_FACES = np.array([(0, 1, 3), (0, 3, 2), (4, 5, 7), (4, 7, 6), (0, 1, 5), (0, 5, 4),
                       (2, 3, 7), (2, 7, 6), (0, 2, 6), (0, 6, 4), (1, 3, 7), (1, 7, 5)])


@dataclass
class ElementGeom:
    element_uid: str
    aabb_min: tuple
    aabb_max: tuple
    triangles: Optional[np.ndarray] = None  # (N,3,3) meters, project frame


@dataclass
class Visible:
    element_uid: str
    mask: np.ndarray  # bool HxW
    polygon: list  # [[u,v]] normalized 0..1, convex hull on the shorter azimuth wrap (jumps at the seam if crossing)
    occlusion_ratio: float
    view_quality: float
    pixel_bbox: tuple  # (x0,y0,x1,y1); x0 > x1 means the box wraps across the seam
    distance_m: float
    seam: bool = False


def box_triangles(mn, mx) -> np.ndarray:
    mn, mx = np.asarray(mn, float), np.asarray(mx, float)
    idx = np.arange(8)
    corners = np.where(np.stack([idx & 1, (idx >> 1) & 1, (idx >> 2) & 1], 1) == 1, mx, mn)
    return corners[_BOX_FACES]


def _template(n: int) -> np.ndarray:
    """Barycentric weights (S,3,3) of the n^2 sub-triangles of a triangle."""
    def w(i, j):
        return (1 - (i + j) / n, i / n, j / n)
    out = []
    for i in range(n):
        for j in range(n - i):
            out.append([w(i, j), w(i + 1, j), w(i, j + 1)])
            if i + j <= n - 2:
                out.append([w(i + 1, j), w(i + 1, j + 1), w(i, j + 1)])
    return np.array(out)


def _subdivide(P, eid, cosinc, W):
    """P (M,3,3) camera-frame triangles -> sub-triangles (K,3,3) with inherited eid/cos."""
    r = np.linalg.norm(P, axis=2)
    d = P / r[..., None]
    ang = np.max([np.arccos(np.clip((d[:, a] * d[:, b]).sum(1), -1, 1)) for a, b in ((0, 1), (1, 2), (2, 0))], axis=0)
    n = np.clip(np.ceil(ang * W / (2 * np.pi) / PX_STEP), 1, MAX_SUBDIV).astype(int)
    Ps, es, cs = [], [], []
    for k in np.unique(n):
        sel = n == k
        B = _template(int(k))
        Ps.append(np.einsum("sab,mbc->msac", B, P[sel]).reshape(-1, 3, 3))
        es.append(np.repeat(eid[sel], len(B)))
        cs.append(np.repeat(cosinc[sel], len(B)))
    return np.concatenate(Ps), np.concatenate(es), np.concatenate(cs)


def _rasterise(Q, eid, cosinc, W, H):
    """Return candidate pixels (pix, depth, eid, cos) for sub-triangles Q (K,3,3) in camera frame."""
    r = np.linalg.norm(Q, axis=2)
    u = W * (np.arctan2(Q[..., 0], Q[..., 2]) / (2 * np.pi) + 0.5) - 0.5
    v = H * (np.arcsin(np.clip(Q[..., 1] / r, -1, 1)) / np.pi + 0.5) - 0.5
    wrap = (u.max(1) - u.min(1)) > W / 2  # seam: shift the left-side vertices right by W
    u = np.where(wrap[:, None] & (u < W / 2), u + W, u)
    x0 = np.ceil(u.min(1)).astype(int)
    y0 = np.maximum(np.ceil(v.min(1)).astype(int), 0)
    bw = np.floor(u.max(1)).astype(int) - x0 + 1
    bh = np.minimum(np.floor(v.max(1)).astype(int), H - 1) - y0 + 1
    den = (v[:, 1] - v[:, 2]) * (u[:, 0] - u[:, 2]) + (u[:, 2] - u[:, 1]) * (v[:, 0] - v[:, 2])
    ok = (bw > 0) & (bh > 0) & (np.abs(den) > 1e-9)
    idx = np.nonzero(ok)[0]
    cnt = (bw[idx] * bh[idx]).astype(np.int64)
    cum = np.cumsum(cnt)
    out = []
    lo = 0
    while lo < len(idx):  # chunk so each batch enumerates <= ~2M bbox pixels
        hi = max(int(np.searchsorted(cum, (cum[lo - 1] if lo else 0) + 2_000_000, "right")), lo + 1)
        t, c = idx[lo:hi], cnt[lo:hi]
        lo = hi
        k = np.repeat(np.arange(len(t)), c)
        off = np.arange(len(k)) - np.repeat(np.cumsum(c) - c, c)
        tw = bw[t][k]
        px = (x0[t][k] + off % tw).astype(float)
        py = (y0[t][k] + off // tw).astype(float)
        tk = t[k]
        U, V, R_, D = u[tk], v[tk], r[tk], den[tk]
        w0 = ((V[:, 1] - V[:, 2]) * (px - U[:, 2]) + (U[:, 2] - U[:, 1]) * (py - V[:, 2])) / D
        w1 = ((V[:, 2] - V[:, 0]) * (px - U[:, 2]) + (U[:, 0] - U[:, 2]) * (py - V[:, 2])) / D
        w2 = 1 - w0 - w1
        ins = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
        if not ins.any():
            continue
        tm = tk[ins]
        dep = (w0 * R_[:, 0] + w1 * R_[:, 1] + w2 * R_[:, 2])[ins]
        pix = py[ins].astype(np.int64) * W + (px[ins].astype(np.int64) % W)
        out.append((pix, dep.astype(np.float32), eid[tm], cosinc[tm]))
    if not out:
        return None
    return tuple(np.concatenate(a) for a in zip(*out))


def _hull(rows, cols, W, H):
    u_cols = np.unique(cols)
    gaps = np.diff(np.r_[u_cols, u_cols[0] + W])
    g = int(np.argmax(gaps))
    start = int(u_cols[(g + 1) % len(u_cols)])  # first column after the widest empty azimuth gap
    rel = (cols - start) % W
    pts = np.stack([rel, rows], 1).astype(np.float32)
    h = cv2.convexHull(pts)[:, 0, :]
    poly = [[float(((x + start) % W + 0.5) / W), float((y + 0.5) / H)] for x, y in h]
    span = int(rel.max())
    seam = start + span >= W
    bbox = (start, int(rows.min()), (start + span) % W, int(rows.max()))
    return poly, bbox, seam


def project_elements(T_bim_from_cam, elements, width, height, max_dist_m=15.0) -> list:
    W, H = int(width), int(height)
    T = np.asarray(T_bim_from_cam, float)
    R, t = T[:3, :3], T[:3, 3]
    n_el = len(elements)
    if n_el == 0:
        return []
    mn = np.array([e.aabb_min for e in elements], float)
    mx = np.array([e.aabb_max for e in elements], float)
    dist = np.linalg.norm((mn + mx) / 2 - t, axis=1)
    keep = np.nonzero(dist - np.linalg.norm(mx - mn, axis=1) / 2 <= max_dist_m)[0]
    if len(keep) == 0:
        return []
    tris, eids = [], []
    for i in keep:
        e = elements[i]
        tr = np.asarray(e.triangles, float) if e.triangles is not None else box_triangles(e.aabb_min, e.aabb_max)
        tris.append(tr)
        eids.append(np.full(len(tr), i, np.int32))
    P = (np.concatenate(tris) - t) @ R  # R^T (p - t)
    eid = np.concatenate(eids)
    good = np.linalg.norm(P, axis=2).min(1) > 1e-3
    P, eid = P[good], eid[good]
    nrm = np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0])
    cen = P.mean(1)
    cosinc = np.abs((nrm * cen).sum(1)) / (np.linalg.norm(nrm, axis=1) * np.linalg.norm(cen, axis=1) + 1e-12)
    Q, qe, qc = _subdivide(P, eid, cosinc, W)
    cand = _rasterise(Q, qe, qc, W, H)
    if cand is None:
        return []
    pix, dep, ce, cc = cand
    WH = W * H
    # total projected pixels per element (unique element/pixel pairs, ignoring occlusion)
    tot = np.bincount(np.unique(ce.astype(np.int64) * WH + pix) // WH, minlength=n_el)
    # nearest element per pixel
    dq = np.clip(dep / (max_dist_m * 4), 0, 1) * (_DQ - 1)
    order = np.argsort((pix << 24) | dq.astype(np.int64), kind="stable")
    ps = pix[order]
    first = order[np.r_[True, ps[1:] != ps[:-1]]]
    wp, we, wd, wc = pix[first], ce[first], dep[first], cc[first]
    vis = np.bincount(we, minlength=n_el)
    by_el = np.argsort(we, kind="stable")
    bounds = np.searchsorted(we[by_el], np.arange(n_el + 1))
    out = []
    for i in np.nonzero(vis)[0]:
        s = by_el[bounds[i]:bounds[i + 1]]
        p = wp[s]
        mask = np.zeros(WH, bool)
        mask[p] = True
        poly, bbox, seam = _hull(p // W, p % W, W, H)
        d = float(np.median(wd[s]))
        q = (1 - 0.7 * min(d / max_dist_m, 1)) * (0.3 + 0.7 * float(wc[s].mean()))
        out.append(Visible(elements[i].element_uid, mask.reshape(H, W), poly,
                           float(max(0.0, 1 - vis[i] / tot[i])), float(np.clip(q, 0, 1)), bbox, d, seam))
    return out
