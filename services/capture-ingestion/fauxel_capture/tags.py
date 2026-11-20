"""End-to-end AprilTag auto-alignment: SLAM-posed equirect keyframes + tag survey -> T_bim_from_slam.

Reuses alignment.AprilTagLocalizer (detection) and alignment.UmeyamaAligner (RANSAC Sim3/SE3).
Also writes printable tag36h11 sheets for the field (``python -m fauxel_capture.tags sheet 101 102 -o out/``).

Survey JSON: {"frame": "internal|shared|ifc_site", "tags": {"101": [x, y, z], ...}} in meters. ``frame`` is the BIM
frame the surveyed xyz live in (Revit internal origin, Revit shared coordinates, IFC site); it is passed through to the
result so the caller knows which frame ``T_bim_from_slam`` targets.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import cv2
import numpy as np

from .alignment import (AprilTagDetector, AprilTagLocalizer, BimCoordinateTransformer, EquirectangularProjector, Pose3D,
                        TagObservation, UmeyamaAligner)

FRAMES = ("internal", "shared", "ifc_site")
MAX_RMSE_M = 0.05
MIN_DIST_M, MAX_DIST_M, MAX_INCIDENCE_DEG = 1.5, 4.0, 50.0


class AlignmentRefused(ValueError):
    """Alignment is not trustworthy (too few / collinear tags, or RMSE above the gate)."""


@dataclass
class TagSurvey:
    frame: str
    tags: Dict[int, np.ndarray]

    @classmethod
    def load(cls, src) -> "TagSurvey":
        d = src if isinstance(src, dict) else json.loads(Path(src).read_text())
        frame = d.get("frame")
        if frame not in FRAMES:
            raise ValueError(f"survey 'frame' must be one of {FRAMES}, got {frame!r}")
        tags = {}
        for k, v in d.get("tags", {}).items():
            v = np.asarray(v, float)
            if v.shape != (3,) or not np.isfinite(v).all():
                raise ValueError(f"tag {k}: expected three finite meters, got {v}")
            tags[int(k)] = v
        return cls(frame, tags)


@dataclass
class AlignmentResult:
    scale: float
    R_bim_from_slam: np.ndarray
    t_bim_from_slam: np.ndarray
    rmse_m: float
    residuals_m: Dict[int, float]      # every surveyed tag seen, inliers and rejected
    inliers: Dict[int, bool]
    n_observations: Dict[int, int]     # kept keyframe observations pooled per tag
    frame: str
    estimate_scale: bool

    @property
    def T_bim_from_slam(self) -> np.ndarray:
        T = np.eye(4)
        T[:3, :3], T[:3, 3] = self.scale * self.R_bim_from_slam, self.t_bim_from_slam
        return T

    def transformer(self) -> BimCoordinateTransformer:
        return BimCoordinateTransformer(self.scale, self.R_bim_from_slam, self.t_bim_from_slam)


def observation_ok(o: TagObservation, min_dist=MIN_DIST_M, max_dist=MAX_DIST_M, max_incidence_deg=MAX_INCIDENCE_DEG) -> bool:
    """Keyframe gate: tag 1.5-4 m away, viewing angle off the tag normal < 50 deg (normal sign is ambiguous, so |cos|)."""
    t = o.pose_cam_from_tag.translation
    dist = float(np.linalg.norm(t))
    if not min_dist <= dist <= max_dist:
        return False
    n = o.pose_cam_from_tag.rotation[:, 2]
    cos = abs(float(n @ t)) / dist
    return cos >= np.cos(np.deg2rad(max_incidence_deg))


def pool_observations(obs: Iterable[TagObservation], **gate) -> Tuple[Dict[int, np.ndarray], Dict[int, int]]:
    """Median SLAM position per tag over the observations that pass the distance/incidence gate."""
    per: Dict[int, List[np.ndarray]] = {}
    for o in obs:
        if observation_ok(o, **gate):
            per.setdefault(o.tag_id, []).append(np.asarray(o.position_slam, float))
    return {k: np.median(v, axis=0) for k, v in per.items()}, {k: len(v) for k, v in per.items()}


def _noncollinear(pts: np.ndarray, tol: float = 1e-2) -> bool:
    """True when the points span a plane/volume: second singular value > tol * first."""
    if len(pts) < 3:
        return False
    s = np.linalg.svd(pts - pts.mean(0), compute_uv=False)
    return bool(s[0] > 0 and s[1] / s[0] > tol)


def align_observations(obs: Iterable[TagObservation], survey: TagSurvey, estimate_scale: bool = True,
                       max_rmse_m: float = MAX_RMSE_M, **gate) -> AlignmentResult:
    """Pool -> RANSAC Umeyama (Sim3 when ``estimate_scale`` else SE3). Raises AlignmentRefused when unusable."""
    pooled, counts = pool_observations(obs, **gate)
    ids = sorted(k for k in pooled if k in survey.tags)
    if len(ids) < 3:
        raise AlignmentRefused(f"only {len(ids)} surveyed tags observed with good geometry (need >= 3): {ids}")
    src = np.array([pooled[k] for k in ids])
    dst = np.array([survey.tags[k] for k in ids])
    if not _noncollinear(dst) or not _noncollinear(src):
        raise AlignmentRefused("tags are collinear; need >= 3 non-collinear tags")
    s, R, t, mask, rmse = UmeyamaAligner.align_with_ransac(src, dst, estimate_scale=estimate_scale,
                                                           inlier_threshold_m=max_rmse_m)
    if mask.sum() < 3 or not _noncollinear(dst[mask]):
        raise AlignmentRefused(f"only {int(mask.sum())} consistent, non-collinear tags after outlier rejection")
    if rmse > max_rmse_m:
        raise AlignmentRefused(f"RMSE {rmse * 1000:.1f} mm exceeds {max_rmse_m * 1000:.0f} mm")
    res = np.linalg.norm(dst - (s * src @ R.T + t), axis=1)
    return AlignmentResult(s, R, t, rmse, {k: float(r) for k, r in zip(ids, res)},
                           {k: bool(m) for k, m in zip(ids, mask)}, counts, survey.frame, estimate_scale)


def detect_frames(frames: Sequence[Tuple[np.ndarray, Pose3D]], tag_size_m: float, family: str = "tag36h11") -> List[TagObservation]:
    """frames: (equirect BGR/gray image, pose_slam_from_cam) per keyframe."""
    h, w = frames[0][0].shape[:2]
    loc = AprilTagLocalizer(EquirectangularProjector(w, h), tag_size_m, family)
    # pupil-apriltags 1.0.4 heap-corrupts with nthreads>1 next to numpy/LAPACK calls (seen as malloc aborts); run single threaded
    loc.detector = AprilTagDetector(families=family, nthreads=1, quad_decimate=1.0, refine_edges=1)
    out: List[TagObservation] = []
    for i, (img, pose) in enumerate(frames):
        out += loc.detect_in_keyframe(img, i, pose)
    return out


def auto_align(frames: Sequence[Tuple[np.ndarray, Pose3D]], survey, tag_size_m: float = 0.2,
               estimate_scale: bool = True, **kw) -> AlignmentResult:
    survey = survey if isinstance(survey, TagSurvey) else TagSurvey.load(survey)
    return align_observations(detect_frames(frames, tag_size_m), survey, estimate_scale, **kw)


# ----------------------------------------------------------------------------- printable tag sheets
PAGES_MM = {"letter": (215.9, 279.4), "tabloid": (279.4, 431.8), "a4": (210.0, 297.0), "a3": (297.0, 420.0)}


def tag_image(tag_id: int, cell_px: int = 40) -> np.ndarray:
    """tag36h11 as an 8x8-cell grayscale image (outer black border included = AprilTag 'tag size')."""
    d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
    if not 0 <= tag_id < d.bytesList.shape[0]:
        raise ValueError(f"tag36h11 ids are 0..{d.bytesList.shape[0] - 1}")
    return cv2.aruco.generateImageMarker(d, tag_id, 8 * cell_px, borderBits=1)


def tag_sheet(tag_id: int, tag_mm: float = 200.0, page: str = "auto", dpi: int = 300) -> Tuple[np.ndarray, float]:
    """One page: tag with a >= 1 cell quiet zone, ID label and a 100 mm scale bar. Returns (image, effective_dpi).
    The effective dpi is tuned so the black square prints exactly ``tag_mm`` when the PDF/PNG is printed at 100%."""
    cell = max(1, round(tag_mm / 8 / 25.4 * dpi))
    eff_dpi = cell * 8 * 25.4 / tag_mm
    need = tag_mm + 2 * tag_mm / 8 + 40  # quiet zone + label room
    names = [page] if page != "auto" else list(PAGES_MM)
    for n in sorted(names, key=lambda n: min(PAGES_MM[n])):
        w_mm, h_mm = PAGES_MM[n]
        if min(w_mm, h_mm) >= tag_mm + 2 * tag_mm / 8 and max(w_mm, h_mm) >= need:
            break
    else:
        raise ValueError(f"{tag_mm:.0f} mm tag does not fit {names}")
    px = lambda mm: int(round(mm / 25.4 * eff_dpi))
    img = np.full((px(h_mm), px(w_mm)), 255, np.uint8)
    t = tag_image(tag_id, cell)
    y0, x0 = px(15), (img.shape[1] - t.shape[0]) // 2
    img[y0:y0 + t.shape[0], x0:x0 + t.shape[1]] = t
    ty = y0 + t.shape[0] + px(tag_mm / 8) + px(10)
    scale = max(1.0, tag_mm / 100.0)
    cv2.putText(img, f"ID {tag_id}   tag36h11   {tag_mm:.0f} mm", (x0, ty), cv2.FONT_HERSHEY_SIMPLEX,
                scale * 1.4, 0, int(4 * scale), cv2.LINE_AA)
    by = ty + px(12)
    cv2.line(img, (x0, by), (x0 + px(100), by), 0, max(3, px(1.0)))
    cv2.putText(img, "scale check: this bar = 100 mm (print at 100%)", (x0, by + px(9)),
                cv2.FONT_HERSHEY_SIMPLEX, scale * 0.8, 0, int(2 * scale), cv2.LINE_AA)
    return img, eff_dpi


def write_sheets(ids: Sequence[int], out_dir, tag_mm: float = 200.0, page: str = "auto", pdf: bool = True) -> List[Path]:
    """PNG per tag (+ one multi-page PDF when Pillow is installed). Returns written paths."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths, pil = [], []
    for i in ids:
        img, dpi = tag_sheet(i, tag_mm, page)
        p = out / f"tag36h11_{i:04d}_{tag_mm:.0f}mm.png"
        cv2.imwrite(str(p), img)
        paths.append(p)
        pil.append((img, dpi))
    if pdf and pil:
        try:
            from PIL import Image
            ims = [Image.fromarray(a).convert("L") for a, _ in pil]
            f = out / f"tag36h11_{'_'.join(map(str, ids[:3]))}{'_etc' if len(ids) > 3 else ''}_{tag_mm:.0f}mm.pdf"
            ims[0].save(f, "PDF", save_all=True, append_images=ims[1:], resolution=pil[0][1])
            paths.append(f)
        except ImportError:
            pass
    return paths


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="fauxel_capture.tags")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sheet", help="write printable tag36h11 sheets")
    s.add_argument("ids", type=int, nargs="+")
    s.add_argument("-o", "--out", default="tag_sheets")
    s.add_argument("--size-mm", type=float, default=200.0)
    s.add_argument("--page", default="auto", choices=["auto", *PAGES_MM])
    a = p.parse_args(argv)
    for f in write_sheets(a.ids, a.out, a.size_mm, a.page):
        print(f)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
