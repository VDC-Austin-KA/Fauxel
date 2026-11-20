"""Insta360 raw media support (roadmap M2.4): ONE X2 reference camera, X3 / RS 1-Inch compatible.

* .insp  dual-fisheye JPEG, two circular fisheyes side by side in one image
* .insv  dual-fisheye H.264/H.265 MP4: either both lenses side by side in one stream (ONE X2),
         two video streams in one file (X3/X4 style), or two files ``*_00_*.insv`` / ``*_10_*.insv``
* Dual-fisheye -> 2:1 equirect stitcher (equidistant / equisolid lens model, ~200 deg per lens, per-lens
  calibration JSON, linear seam feathering, overlap-ring gain compensation, nadir mask, cached remap LUTs)
* Calibration refinement (scipy.optimize) from the photometric error in the overlap
* Trailer telemetry (gyro / accel / exposure / GPS)

Equirect convention = ``alignment.EquirectangularProjector``: ray (x right, y down, z forward),
theta = atan2(x, z), phi = asin(y); image centre column looks along +Z. Lens 0 looks along +Z, lens 1 along -Z.

Telemetry trailer: format reverse engineered by the open source parsers
  * AdrianEddy/telemetry-parser ``src/insta360/{mod,record}.rs`` (MIT OR Apache-2.0)
  * gyroflow (GPL-3.0, only read for the format description, nothing copied)
Layout implemented here (own code): the file ends with a 72 byte footer
``[32 pad][u32 trailer_size][u32 version][32 magic "8db42d694ccc418790edff439fe026bf"]``. Records are stored
back to front: immediately before the footer sits ``[data][u8 format][u8 id][u32 size]`` (little endian), the
next record ends where the previous data starts. Ids used: 1 metadata (protobuf), 3 gyro, 4 exposure, 7 GPS.
Not implemented: the ``Offsets`` (id 0) index used by X4/X5 trailers.

CLI:  python -m fauxel_capture.insta360 stitch in.insv out_dir --every 1.0
      python -m fauxel_capture.insta360 telemetry in.insv out.csv
      python -m fauxel_capture.insta360 refine in.insv calib.json
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import os
import re
import struct
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterator, List, Optional, Tuple, Union

import cv2
import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation

MAGIC = b"8db42d694ccc418790edff439fe026bf"
FOOTER = 72
IMAGE_EXT = {".insp", ".jpg", ".jpeg", ".png"}


# ----------------------------------------------------------------------------- calibration
@dataclass
class LensCal:
    """Per-lens parameters. cx, cy, radius are fractions of the lens tile (cx of its width, cy/radius of its
    height), so one file works at any resolution. Angles are offsets from the nominal lens axis."""
    cx: float = 0.5
    cy: float = 0.5
    radius: float = 0.49
    fov_deg: float = 200.0
    yaw_deg: float = 0.0
    pitch_deg: float = 0.0
    roll_deg: float = 0.0


@dataclass
class Calibration:
    """Defaults are nominal ONE X2 values (image circle fills the tile, ~200 deg lenses). Refine on real footage."""
    camera: str = "Insta360 ONE X2"
    model: str = "equidistant"          # or "equisolid"
    lenses: List[LensCal] = field(default_factory=lambda: [LensCal(), LensCal()])
    blend_deg: float = 10.0             # half width of the linear feather band around the 90 deg seam
    swap_lenses: bool = False           # set if the left tile is the rear lens
    nadir_mask_deg: float = 0.0         # black out this many degrees around the nadir (stick / tripod)

    @classmethod
    def from_dict(cls, d: dict) -> "Calibration":
        d = dict(d)
        d["lenses"] = [LensCal(**l) for l in d.get("lenses", [{}, {}])]
        return cls(**d)

    @classmethod
    def load(cls, path) -> "Calibration":
        return cls.from_dict(json.loads(Path(path).read_text()))

    def save(self, path) -> None:
        Path(path).write_text(json.dumps(asdict(self), indent=2))


def lens_rotation(cal: Calibration, k: int) -> np.ndarray:
    """R_cam_from_lens: lens 1 is nominally turned 180 deg about +Y, then yaw/pitch/roll offsets (Y, X, Z)."""
    l = cal.lenses[k]
    off = Rotation.from_euler("YXZ", [l.yaw_deg, l.pitch_deg, l.roll_deg], degrees=True)
    return (Rotation.from_euler("y", 180.0 * k, degrees=True) * off).as_matrix()


def _g(theta, model):
    return theta if model == "equidistant" else np.sin(theta / 2.0)


def project_lens(d: np.ndarray, cal: Calibration, k: int, tile_w: int, tile_h: int, dtype=np.float32):
    """Camera-frame unit rays (...,3) -> pixel (u, v) inside tile k plus angle theta off the lens axis."""
    l = cal.lenses[k]
    dl = d.astype(dtype) @ lens_rotation(cal, k).astype(dtype)  # row-vector form of R^T d
    theta = np.arccos(np.clip(dl[..., 2], -1, 1))
    phi = np.arctan2(dl[..., 1], dl[..., 0])
    r = l.radius * tile_h * _g(theta, cal.model) / _g(np.deg2rad(l.fov_deg) / 2, cal.model)
    return l.cx * tile_w + r * np.cos(phi), l.cy * tile_h + r * np.sin(phi), theta


def equirect_rays(h0: int, h1: int, width: int, height: int, dtype=np.float32):
    lon = ((np.arange(width) + 0.5) / width * 2 * np.pi - np.pi).astype(dtype)
    lat = ((np.arange(h0, h1) + 0.5) / height * np.pi - np.pi / 2).astype(dtype)
    cl = np.cos(lat)[:, None]
    return np.stack([cl * np.sin(lon)[None], np.broadcast_to(np.sin(lat)[:, None], (h1 - h0, width)),
                     cl * np.cos(lon)[None]], -1), lat


# ----------------------------------------------------------------------------- stitcher
class Stitcher:
    """Dual-fisheye (side by side, in_w x in_h) -> equirect (out_w x out_w/2). LUTs are built once."""

    def __init__(self, cal: Optional[Calibration] = None, in_size: Tuple[int, int] = (5760, 2880),
                 out_width: int = 5760, exposure: bool = True, gain_smooth: float = 0.0, chunk: int = 256):
        self.cal = cal or Calibration()
        self.in_w, self.in_h = in_size
        self.W, self.H = out_width, out_width // 2
        self.exposure, self.gain_smooth = exposure, gain_smooth
        self._gain: Optional[np.ndarray] = None
        tw, th = self.in_w // 2, self.in_h
        c, blend, edge = self.cal, np.deg2rad(self.cal.blend_deg), np.deg2rad(3.0)
        m1 = [np.empty((self.H, self.W, 2), np.int16) for _ in range(2)]
        m2 = [np.empty((self.H, self.W), np.uint16) for _ in range(2)]
        W0 = np.empty((self.H, self.W), np.uint8)
        W1 = np.empty_like(W0)
        self.nadir_mask = np.zeros((self.H, self.W), bool)
        rr, cc = [], []
        for r0 in range(0, self.H, chunk):
            r1 = min(self.H, r0 + chunk)
            d, lat = equirect_rays(r0, r1, self.W, self.H)
            uv = [project_lens(d, c, k, tw, th) for k in range(2)]
            for k in range(2):
                x0 = (1 - k if c.swap_lenses else k) * tw
                m1[k][r0:r1], m2[k][r0:r1] = cv2.convertMaps((uv[k][0] + x0).astype(np.float32),
                                                             uv[k][1].astype(np.float32), cv2.CV_16SC2)
            t0, t1 = uv[0][2], uv[1][2]
            raw = np.clip(0.5 + (t1 - t0) / (4 * blend), 0, 1)
            v = [np.clip((np.deg2rad(c.lenses[k].fov_deg) / 2 - uv[k][2]) / edge, 0, 1) for k in range(2)]
            w0, w1 = raw * v[0], (1 - raw) * v[1]
            s = w0 + w1
            ok = s > 1e-6
            w0 = np.where(ok, w0 / np.where(ok, s, 1), 0)
            nad = np.broadcast_to(lat[:, None] > np.pi / 2 - np.deg2rad(c.nadir_mask_deg), w0.shape) \
                if c.nadir_mask_deg > 0 else np.zeros(w0.shape, bool)
            a = np.rint(w0 * 255).astype(np.uint8)
            W0[r0:r1] = np.where(ok & ~nad, a, 0)
            W1[r0:r1] = np.where(ok & ~nad, 255 - a, 0)
            self.nadir_mask[r0:r1] = nad
            band = (raw > 0.05) & (raw < 0.95) & (v[0] >= 1) & (v[1] >= 1) & ~nad
            ys, xs = np.nonzero(band[::3, ::3])
            rr.append(ys * 3 + r0)
            cc.append(xs * 3)
        self._maps = list(zip(m1, m2))
        self._w = [cv2.merge([W] * 3) for W in (W0, W1)]
        self.ring = (np.concatenate(rr), np.concatenate(cc))

    def _gains(self, a: np.ndarray, b: np.ndarray) -> np.ndarray:
        """Per-channel gains (g0, g1) that make both lenses equal to their geometric mean over the overlap ring."""
        if not self.exposure or len(self.ring[0]) < 100:
            return np.ones((2, 3), np.float32)
        ma = a[self.ring].astype(np.float64).mean(0) + 1e-3
        mb = b[self.ring].astype(np.float64).mean(0) + 1e-3
        g = np.clip(np.stack([np.sqrt(mb / ma), np.sqrt(ma / mb)]), 0.5, 2.0).astype(np.float32)
        if self.gain_smooth > 0 and self._gain is not None:
            g = self.gain_smooth * self._gain + (1 - self.gain_smooth) * g
        self._gain = g
        return g

    def stitch(self, frame: np.ndarray, return_gains: bool = False):
        if frame.shape[1] != self.in_w or frame.shape[0] != self.in_h:
            raise ValueError(f"frame is {frame.shape[1]}x{frame.shape[0]}, stitcher built for {self.in_w}x{self.in_h}")
        imgs = [cv2.remap(frame, m1, m2, cv2.INTER_LINEAR) for m1, m2 in self._maps]
        g = self._gains(*imgs)
        ramp = np.arange(256, dtype=np.float32)[:, None]
        out = None
        for img, w, gk in zip(imgs, self._w, g):
            lut = np.clip(ramp * gk[None], 0, 255).astype(np.uint8).reshape(1, 256, 3)
            part = cv2.multiply(cv2.LUT(img, lut), w, scale=1 / 255.0)
            out = part if out is None else cv2.add(out, part)
        return (out, g) if return_gains else out


# ----------------------------------------------------------------------------- calibration refinement
def refine_calibration(frames: List[np.ndarray], cal: Optional[Calibration] = None,
                       scales=(0.125, 0.25, 0.5), n_az: int = 720, n_rad: int = 5,
                       free_lens0: bool = False, verbose: bool = False) -> Tuple[Calibration, dict]:
    """Optimise lens-1 yaw/pitch/roll and both fovs (plus lens-0 yaw/pitch/roll when ``free_lens0``) to minimise the
    photometric difference between the two fisheyes on a ring of rays around the 90 deg seam (coarse to fine).
    Each frame/lens is z-scored on the ring, which removes per-lens exposure gain and offset. Lens 0 orientation is
    otherwise the gauge (fixed)."""
    cal = copy.deepcopy(cal or Calibration())
    grays = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) if f.ndim == 3 else f for f in frames]
    th = np.deg2rad(np.linspace(85.0, 95.0, n_rad))
    az = np.linspace(0, 2 * np.pi, n_az, endpoint=False)
    T, A = np.meshgrid(th, az)
    d_l0 = np.stack([np.sin(T) * np.cos(A), np.sin(T) * np.sin(A), np.cos(T)], -1).reshape(-1, 3)
    d = (d_l0 @ lens_rotation(cal, 0).T).astype(np.float32)  # fixed rays, defined from the initial lens-0 frame

    def pack(c):
        l0, l1 = c.lenses
        x = [l1.yaw_deg, l1.pitch_deg, l1.roll_deg, l0.fov_deg, l1.fov_deg]
        return np.array(x + ([l0.yaw_deg, l0.pitch_deg, l0.roll_deg] if free_lens0 else []))

    def unpack(x):
        c = copy.deepcopy(cal)
        c.lenses[1].yaw_deg, c.lenses[1].pitch_deg, c.lenses[1].roll_deg = x[:3]
        c.lenses[0].fov_deg, c.lenses[1].fov_deg = x[3:5]
        if free_lens0:
            c.lenses[0].yaw_deg, c.lenses[0].pitch_deg, c.lenses[0].roll_deg = x[5:8]
        return c

    def sample(img, c, k, tw, th_):
        u, v, _ = project_lens(d, c, k, tw, th_)
        x0 = (1 - k if c.swap_lenses else k) * tw
        return cv2.remap(img, (u + x0).astype(np.float32)[None], v.astype(np.float32)[None],
                         cv2.INTER_LINEAR)[0].astype(np.float64)

    def residuals(x, imgs):
        c = unpack(x)
        out = []
        for img in imgs:
            tw, th_ = img.shape[1] // 2, img.shape[0]
            a, b = sample(img, c, 0, tw, th_), sample(img, c, 1, tw, th_)
            a, b = (a - a.mean()) / (a.std() + 1e-6), (b - b.mean()) / (b.std() + 1e-6)
            out.append((a - b) / np.sqrt(len(a)))
        return np.concatenate(out)

    lo = [-15, -15, -15, 192, 192] + [-15] * 3 * free_lens0
    hi = [15, 15, 15, 225, 225] + [15] * 3 * free_lens0
    x = pack(cal)
    r_init = None
    for s in scales:
        imgs = [cv2.GaussianBlur(cv2.resize(g, None, fx=s, fy=s, interpolation=cv2.INTER_AREA), (0, 0), 1.2)
                .astype(np.float32) for g in grays]
        if r_init is None:
            r_init = float(np.sqrt(np.sum(residuals(x, imgs) ** 2) / len(imgs)))
        sol = least_squares(residuals, np.clip(x, lo, hi), args=(imgs,), bounds=(lo, hi), loss="soft_l1",
                            f_scale=0.5, x_scale=[2, 2, 2, 3, 3] + [2] * 3 * free_lens0, diff_step=1e-3)
        x = sol.x
        if verbose:
            print(f"scale {s}: cost {sol.cost:.5f} x={np.round(x, 3)}", file=sys.stderr)
    imgs = [cv2.GaussianBlur(g, (0, 0), 1.2).astype(np.float32) for g in grays] if max(scales) >= 1 else imgs
    cal = unpack(x)
    r_fin = float(np.sqrt(np.sum(residuals(x, imgs) ** 2) / len(imgs)))
    return cal, {"params": x.tolist(), "rms_before": r_init, "rms_after": r_fin, "n_frames": len(frames)}


# ----------------------------------------------------------------------------- sources
def _pair_path(p: Path) -> Optional[Path]:
    for a, b in (("_00_", "_10_"), ("_10_", "_00_")):
        if a in p.name:
            q = p.with_name(p.name.replace(a, b, 1))
            if q.exists():
                return q
    return None


def laplacian_var(img: np.ndarray, width: int = 1440) -> float:
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    if g.shape[1] > width:
        g = cv2.resize(g, (width, int(g.shape[0] * width / g.shape[1])), interpolation=cv2.INTER_AREA)
    return float(cv2.Laplacian(g, cv2.CV_64F).var())


class RawSource:
    """Frames of a raw .insp/.insv as one side-by-side dual-fisheye BGR image (lens-0 tile left)."""

    def __init__(self, path, partner=None):
        self.path = Path(path)
        pair = Path(partner) if partner else _pair_path(self.path)
        if self.path.suffix.lower() in IMAGE_EXT:
            imgs = [cv2.imdecode(np.fromfile(str(p), np.uint8), cv2.IMREAD_COLOR) for p in ([self.path] + ([pair] if pair else []))]
            if any(i is None for i in imgs):
                raise IOError(f"cannot decode {path}")
            self._image = imgs[0] if len(imgs) == 1 or imgs[0].shape[1] >= 1.5 * imgs[0].shape[0] else None
            if self._image is None:
                self._image = np.hstack(sorted_pair(self.path, pair, imgs))
            self.fps, self.count, self._caps = 1.0, 1, []
        else:
            self._image = None
            caps = [cv2.VideoCapture(str(self.path))]
            if not caps[0].isOpened():
                raise IOError(f"cannot open {path}")
            self._square = abs(caps[0].get(cv2.CAP_PROP_FRAME_WIDTH) / max(1, caps[0].get(cv2.CAP_PROP_FRAME_HEIGHT)) - 1) < 0.1
            if self._square:  # one lens per stream or per file
                c2 = cv2.VideoCapture(str(self.path), cv2.CAP_FFMPEG, [cv2.CAP_PROP_VIDEO_STREAM, 1])
                if not c2.isOpened() and pair:
                    c2 = cv2.VideoCapture(str(pair))
                    caps = [cv2.VideoCapture(str(p)) for p in sorted([self.path, pair], key=lambda p: "_10_" in p.name)]
                if not c2.isOpened():
                    raise IOError(f"{path} holds a single square lens stream and no second stream or _00_/_10_ partner was found")
                caps = caps if len(caps) == 2 else [caps[0], c2]
            self._caps = caps
            self.fps = caps[0].get(cv2.CAP_PROP_FPS) or 30.0
            self.count = int(min(c.get(cv2.CAP_PROP_FRAME_COUNT) for c in caps))
        self.size = self.read_size()

    def read_size(self) -> Tuple[int, int]:
        if self._image is not None:
            return self._image.shape[1], self._image.shape[0]
        w = int(sum(c.get(cv2.CAP_PROP_FRAME_WIDTH) for c in self._caps))
        return w, int(self._caps[0].get(cv2.CAP_PROP_FRAME_HEIGHT))

    def grab(self) -> bool:
        return self._image is None and all([c.grab() for c in self._caps]) if self._caps else False

    def retrieve(self) -> Optional[np.ndarray]:
        if self._image is not None:
            return self._image
        fr = [c.retrieve()[1] for c in self._caps]
        return None if any(f is None for f in fr) else (fr[0] if len(fr) == 1 else np.hstack(fr))

    def release(self):
        for c in self._caps:
            c.release()


def sorted_pair(path: Path, pair: Optional[Path], imgs):
    if pair is None:
        return imgs
    return imgs if "_10_" not in path.name else imgs[::-1]


def sample_frames(src: RawSource, every: float = 1.0, window: float = 0.25, min_sharpness: float = 0.0,
                  rel_sharpness: float = 0.35) -> Iterator[Tuple[float, np.ndarray, float]]:
    """Yield (t_sec, dual-fisheye frame, laplacian variance): the sharpest frame within +-window of each
    multiple of ``every``; rejects frames below ``min_sharpness`` or ``rel_sharpness`` x running median."""
    if src._image is not None:
        yield 0.0, src._image, laplacian_var(src._image)
        return
    k, i, best, hist = 0, 0, None, []
    while True:
        t, target = i / src.fps, k * every
        if t > target + window and best is None:
            k += 1  # no candidate found (e.g. window shorter than one frame); move on
            if src.count and i >= src.count:
                break
            continue
        if not src.grab():
            break
        if abs(t - target) <= window or (window == 0 and t >= target - 0.5 / src.fps):
            fr = src.retrieve()
            if fr is not None:
                s = laplacian_var(fr)
                if best is None or s > best[2]:
                    best = (t, fr, s)
        i += 1
        if best is not None and i / src.fps > target + window:
            t_b, fr, s = best
            best, k = None, k + 1
            ref = float(np.median(hist)) if len(hist) >= 5 else 0.0
            hist.append(s)
            if s >= min_sharpness and s >= rel_sharpness * ref:
                yield t_b, fr, s
            else:
                print(f"reject t={t_b:.2f}s sharpness {s:.1f}", file=sys.stderr)
    if best is not None:
        yield best


# ----------------------------------------------------------------------------- telemetry
@dataclass
class Telemetry:
    has_trailer: bool = False
    version: int = 0
    record_sizes: dict = field(default_factory=dict)
    t_imu: np.ndarray = field(default_factory=lambda: np.zeros(0))
    gyro: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))     # rad/s
    accel: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))    # g
    t_exposure: np.ndarray = field(default_factory=lambda: np.zeros(0))
    exposure_s: np.ndarray = field(default_factory=lambda: np.zeros(0))
    gps: np.ndarray = field(default_factory=lambda: np.zeros((0, 6)))      # unix_t, valid, lat, lon, speed_kmh, alt
    meta: dict = field(default_factory=dict)                               # selected protobuf varint fields by tag


def _pb_fields(b: bytes, limit: int = 2000) -> dict:
    """Tiny protobuf wire reader: tag -> list of (int | float | bytes). Stops quietly on malformed data."""
    out, i = {}, 0

    def varint(i):
        v = s = 0
        while i < len(b):
            c = b[i]
            i += 1
            v |= (c & 0x7F) << s
            s += 7
            if not c & 0x80:
                return v, i
        raise ValueError

    try:
        for _ in range(limit):
            if i >= len(b):
                break
            key, i = varint(i)
            tag, wt = key >> 3, key & 7
            if wt == 0:
                v, i = varint(i)
            elif wt == 1:
                v, i = struct.unpack_from("<d", b, i)[0], i + 8
            elif wt == 2:
                n, i = varint(i)
                v, i = b[i:i + n], i + n
            elif wt == 5:
                v, i = struct.unpack_from("<f", b, i)[0], i + 4
            else:
                break
            out.setdefault(tag, []).append(v)
    except (ValueError, struct.error):
        pass
    return out


def _decode_imu(data: bytes, gyro_range: float = 2000.0, acc_range: float = 16.0):
    """Gyro record: either 56 B/sample (u64 ms, f64 acc xyz [g], f64 gyro xyz [rad/s]) or the raw 20 B/sample form
    (u64 ms, u16 acc xyz, u16 gyro xyz, offset 32768, scaled by range; gyro deg/s). Format is picked by size and
    timestamp plausibility."""
    def ok(ts):
        d = np.diff(ts)
        return len(ts) < 2 or bool(np.all((d >= 0) & (d < 1000)))

    cands = []
    if len(data) % 56 == 0 and data:
        a = np.frombuffer(data, np.dtype([("t", "<u8"), ("v", "<f8", 6)]))
        cands.append((a["t"][:50], lambda a=a: (a["t"] / 1e3, a["v"][:, :3], a["v"][:, 3:])))
    if len(data) % 20 == 0 and data:
        a = np.frombuffer(data, np.dtype([("t", "<u8"), ("v", "<u2", 6)]))
        cands.append((a["t"][:50], lambda a=a: (a["t"] / 1e3, (a["v"][:, :3] - 32768.0) * acc_range / 32768.0,
                                                np.deg2rad((a["v"][:, 3:] - 32768.0) * gyro_range / 32768.0))))
    for ts, make in cands:
        if ok(ts.astype(np.float64)):
            t, acc, gyr = make()
            return t.astype(np.float64), np.asarray(acc, np.float64), np.asarray(gyr, np.float64)
    return np.zeros(0), np.zeros((0, 3)), np.zeros((0, 3))


def parse_trailer(src: Union[str, Path, bytes]) -> Telemetry:
    """Parse the Insta360 trailer of an .insv/.insp (path or raw bytes). Never raises on missing/garbled data."""
    tel = Telemetry()
    try:
        if isinstance(src, (bytes, bytearray)):
            n, rd = len(src), (lambda off, ln: bytes(src[off:off + ln]))
            f = None
        else:
            f = open(src, "rb")
            n = os.fstat(f.fileno()).st_size
            rd = lambda off, ln: (f.seek(off), f.read(ln))[1]
        try:
            if n < FOOTER or rd(n - 32, 32) != MAGIC:
                return tel
            size, version = struct.unpack("<II", rd(n - FOOTER + 32, 8))
            if not FOOTER <= size <= n:
                return tel
            buf = rd(n - size, size)
        finally:
            if f:
                f.close()
    except OSError:
        return tel
    tel.has_trailer, tel.version = True, version
    recs, pos = {}, size - FOOTER
    for _ in range(256):
        if pos < 6:
            break
        _fmt, rid, sz = struct.unpack("<BBI", buf[pos - 6:pos])
        start = pos - 6 - sz
        if start < 0:
            break
        recs.setdefault(rid, buf[start:pos - 6])
        pos = start
    tel.record_sizes = {k: len(v) for k, v in recs.items()}
    if 1 in recs:
        f_ = _pb_fields(recs[1])
        tel.meta = {t: v[0] for t, v in f_.items() if isinstance(v[0], int) and t in (24, 36, 185)}
    if 3 in recs:
        t, tel.accel, tel.gyro = _decode_imu(recs[3])
        tel.t_imu = t - t[0] if len(t) else t
    if 4 in recs and len(recs[4]) % 16 == 0 and recs[4]:
        a = np.frombuffer(recs[4], np.dtype([("t", "<u8"), ("e", "<f8")]))
        tel.t_exposure, tel.exposure_s = a["t"] / 1e3 - a["t"][0] / 1e3, a["e"].astype(np.float64)
    if 7 in recs and len(recs[7]) % 53 == 0 and recs[7]:
        g = []
        for o in range(0, len(recs[7]), 53):
            s, ms, fix, lat, ld, lon, lod, spd, _trk, alt = struct.unpack_from("<QHcdcdcddd", recs[7], o)
            g.append([s + ms / 1e3, fix == b"A", lat * (-1 if ld == b"S" else 1), lon * (-1 if lod == b"W" else 1), spd * 3.6, alt])
        tel.gps = np.array(g, np.float64)
    return tel


def write_telemetry_csv(tel: Telemetry, path) -> None:
    path = Path(path)
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["t_s", "ax_g", "ay_g", "az_g", "gx_rad_s", "gy_rad_s", "gz_rad_s"])
        for t, a, g in zip(tel.t_imu, tel.accel, tel.gyro):
            w.writerow([f"{t:.6f}", *map(repr, a), *map(repr, g)])
    if len(tel.exposure_s):
        np.savetxt(path.with_suffix(".exposure.csv"), np.c_[tel.t_exposure, tel.exposure_s], delimiter=",",
                   header="t_s,shutter_s", comments="")
    if len(tel.gps):
        np.savetxt(path.with_suffix(".gps.csv"), tel.gps, delimiter=",", header="unix_t,valid,lat,lon,speed_kmh,alt",
                   comments="")


# ----------------------------------------------------------------------------- API used by services/api
def stitch_file(path, out_dir, every_s: float = 1.0, width: int = 5760, calib=None, window: float = 0.25,
                quality: int = 92) -> List[str]:
    """Stitch a raw .insv/.insp (either file of a _00_/_10_ pair) to equirect JPGs; returns the written paths."""
    src = RawSource(path)
    cal = Calibration.load(calib) if calib else Calibration()
    st = Stitcher(cal, src.size, width, gain_smooth=0.9)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    try:
        for t, fr, _s in sample_frames(src, every_s, window):
            p = out / f"{Path(path).stem}_{int(round(t * 1000)):08d}ms.jpg"
            cv2.imwrite(str(p), st.stitch(fr), [cv2.IMWRITE_JPEG_QUALITY, quality])
            paths.append(str(p))
    finally:
        src.release()
    return paths


def read_telemetry(path) -> Optional[dict]:
    """{'t': (N,), 'gyro': (N,3) rad/s, 'accel': (N,3) g, plus exposure/gps/meta} or None when no IMU data."""
    tel = parse_trailer(path)
    if not tel.has_trailer or not len(tel.t_imu):
        return None
    return {"t": tel.t_imu, "gyro": tel.gyro, "accel": tel.accel, "t_exposure": tel.t_exposure,
            "exposure_s": tel.exposure_s, "gps": tel.gps, "meta": tel.meta}


# ----------------------------------------------------------------------------- CLI
def _load_cal(a) -> Calibration:
    cal = Calibration.load(a.calib) if a.calib else Calibration()
    if getattr(a, "nadir", 0):
        cal.nadir_mask_deg = a.nadir
    return cal


def cmd_stitch(a) -> int:
    src = RawSource(a.input, a.partner)
    st = Stitcher(_load_cal(a), src.size, a.width, gain_smooth=0.9)
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(out / "frames.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["file", "t_sec", "sharpness"])
        for t, fr, s in sample_frames(src, a.every, a.window, a.min_sharpness):
            name = f"{Path(a.input).stem}_{int(round(t * 1000)):08d}ms.jpg"
            cv2.imwrite(str(out / name), st.stitch(fr), [cv2.IMWRITE_JPEG_QUALITY, a.quality])
            w.writerow([name, f"{t:.3f}", f"{s:.2f}"])
            n += 1
    src.release()
    print(f"wrote {n} equirect frames to {out}")
    return 0 if n else 1


def cmd_telemetry(a) -> int:
    tel = parse_trailer(a.input)
    if not tel.has_trailer:
        print("no Insta360 trailer found (pre-stitched or exported file?)", file=sys.stderr)
        return 1
    write_telemetry_csv(tel, a.output)
    print(f"{len(tel.t_imu)} IMU samples, {len(tel.exposure_s)} exposure, {len(tel.gps)} GPS -> {a.output}")
    return 0


def cmd_refine(a) -> int:
    src = RawSource(a.input, a.partner)
    frames = []
    if src._image is not None:
        frames = [src._image]
    else:
        step = max(1, src.count // a.frames)
        for i in range(src.count):
            if not src.grab():
                break
            if i % step == 0 and len(frames) < a.frames:
                frames.append(src.retrieve())
    cal, rep = refine_calibration(frames, Calibration.load(a.calib_in) if a.calib_in else None, verbose=True)
    cal.save(a.output)
    print(json.dumps(rep))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="fauxel_capture.insta360")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("stitch")
    s.add_argument("input"), s.add_argument("out_dir")
    s.add_argument("--every", type=float, default=1.0, help="seconds between frames")
    s.add_argument("--window", type=float, default=0.25, help="search +-s around each time for the sharpest frame")
    s.add_argument("--min-sharpness", type=float, default=0.0, help="Laplacian variance floor (0 = relative filter only)")
    s.add_argument("--width", type=int, default=5760)
    s.add_argument("--calib"), s.add_argument("--partner", help="the other _00_/_10_ file if not auto-detected")
    s.add_argument("--nadir", type=float, default=0.0, help="degrees around nadir to mask")
    s.add_argument("--quality", type=int, default=92)
    s.set_defaults(fn=cmd_stitch)
    t = sub.add_parser("telemetry")
    t.add_argument("input"), t.add_argument("output")
    t.set_defaults(fn=cmd_telemetry)
    r = sub.add_parser("refine")
    r.add_argument("input"), r.add_argument("output")
    r.add_argument("--frames", type=int, default=6), r.add_argument("--calib-in"), r.add_argument("--partner")
    r.set_defaults(fn=cmd_refine)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
