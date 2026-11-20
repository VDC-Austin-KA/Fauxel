import shutil
import struct
import subprocess

import cv2
import numpy as np
import pytest

from fauxel_capture import insta360 as I

TILE = 480  # fisheye tile edge for tests (frame 960x480)


def pattern(lon, lat):
    """Smooth analytic equirect test pattern, BGR float in [0, 255]."""
    b = 0.5 + 0.5 * np.sin(3 * lon + 2 * np.sin(2 * lat))
    g = 0.5 + 0.5 * np.cos(2 * lat + np.sin(2 * lon))
    r = 0.5 + 0.5 * np.sin(lon + 4 * lat)
    return np.stack([b, g, r], -1) * 255


def render_dual_fisheye(cal, tile=TILE, gains=(1.0, 1.0)):
    """Own forward model: fisheye pixel -> lens ray (inverse radial) -> world ray -> analytic pattern."""
    out = np.zeros((tile, 2 * tile, 3), np.float32)
    yy, xx = np.mgrid[0:tile, 0:tile].astype(np.float64)
    for k in range(2):
        l = cal.lenses[k]
        dx, dy = xx - l.cx * tile, yy - l.cy * tile
        r = np.hypot(dx, dy)
        rmax = l.radius * tile
        s = r / rmax
        half = np.deg2rad(l.fov_deg) / 2
        if cal.model == "equidistant":
            theta = s * half
        else:
            theta = 2 * np.arcsin(np.clip(s * np.sin(half / 2), -1, 1))
        phi = np.arctan2(dy, dx)
        dl = np.stack([np.sin(theta) * np.cos(phi), np.sin(theta) * np.sin(phi), np.cos(theta)], -1)
        d = dl @ I.lens_rotation(cal, k).T
        lon, lat = np.arctan2(d[..., 0], d[..., 2]), np.arcsin(np.clip(d[..., 1], -1, 1))
        img = pattern(lon, lat) * gains[k]
        img[s > 1.0] = 0
        out[:, k * tile:(k + 1) * tile] = img
    return np.clip(out, 0, 255).astype(np.uint8)


@pytest.mark.parametrize("model", ["equidistant", "equisolid"])
def test_stitch_recovers_pattern_away_from_seams(model):
    cal = I.Calibration(model=model)
    st = I.Stitcher(cal, (2 * TILE, TILE), out_width=720)
    out = st.stitch(render_dual_fisheye(cal))
    H, W = st.H, st.W
    lon = ((np.arange(W) + .5) / W * 2 * np.pi - np.pi)[None].repeat(H, 0)
    lat = ((np.arange(H) + .5) / H * np.pi - np.pi / 2)[:, None].repeat(W, 1)
    ref = pattern(lon, lat)
    mask = ~(np.abs(np.abs(lon) - np.pi / 2) < np.deg2rad(18)) & (np.abs(lat) < np.deg2rad(75))
    err = np.abs(out.astype(float) - ref)[mask]
    assert err.mean() < 2.5 and np.percentile(err, 99) < 15
    # even in the feathered seam the result stays close (both lenses see the same scene)
    seam_m = (np.abs(np.abs(lon) - np.pi / 2) < np.deg2rad(8)) & (np.abs(lat) < np.deg2rad(75))
    assert np.abs(out.astype(float) - ref)[seam_m].mean() < 6


def test_exposure_compensation_matches_lenses():
    cal = I.Calibration()
    st = I.Stitcher(cal, (2 * TILE, TILE), out_width=720)
    fr = render_dual_fisheye(cal, gains=(1.0, 0.7))
    out, g = st.stitch(fr, return_gains=True)
    assert g[1].mean() / g[0].mean() == pytest.approx(1 / 0.7, rel=0.05)
    no = I.Stitcher(cal, (2 * TILE, TILE), out_width=720, exposure=False).stitch(fr)
    col = 720 // 4  # seam at lon=-90deg
    step = lambda im: abs(im[:, col - 12:col - 4].astype(float).mean() - im[:, col + 4:col + 12].astype(float).mean())
    assert step(out) < 0.5 * step(no)


def test_nadir_mask_and_lut_shapes():
    cal = I.Calibration(nadir_mask_deg=30)
    st = I.Stitcher(cal, (2 * TILE, TILE), out_width=360)
    out = st.stitch(render_dual_fisheye(cal))
    assert out.shape == (180, 360, 3)
    assert st.nadir_mask[-1].all() and not st.nadir_mask[0].any()
    assert out[st.nadir_mask].max() == 0


def test_calibration_json_roundtrip(tmp_path):
    cal = I.Calibration()
    cal.lenses[1].yaw_deg = 1.25
    cal.save(tmp_path / "c.json")
    assert I.Calibration.load(tmp_path / "c.json") == cal


def test_refine_calibration_recovers_lens_offsets():
    # textured scene: smooth pattern plus band-limited noise in the equirect domain
    rng = np.random.default_rng(1)
    noise = cv2.GaussianBlur(rng.random((256, 512)).astype(np.float32), (0, 0), 4)
    noise = (noise - noise.min()) / np.ptp(noise)

    def tex(lon, lat):
        u = ((lon / (2 * np.pi) + .5) * 512 - .5).astype(np.float32)
        v = ((lat / np.pi + .5) * 256 - .5).astype(np.float32)
        n = cv2.remap(noise, u, v, cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP)
        return np.repeat(n[..., None] * 255, 3, -1)

    truth = I.Calibration()
    truth.lenses[1].yaw_deg, truth.lenses[1].pitch_deg, truth.lenses[1].roll_deg = 1.5, -1.0, 0.8
    truth.lenses[1].fov_deg = 203.0
    global pattern
    old, pattern = pattern, tex
    try:
        frame = render_dual_fisheye(truth, tile=720)
    finally:
        pattern = old
    start = I.Calibration()
    cal, rep = I.refine_calibration([frame], start, scales=(0.25, 0.5, 1.0))
    assert rep["rms_after"] < 0.6 * rep["rms_before"]
    l = cal.lenses[1]
    assert abs(l.yaw_deg - 1.5) < 0.5 and abs(l.pitch_deg + 1.0) < 0.5 and abs(l.roll_deg - 0.8) < 0.5


# ---- telemetry
def build_trailer(records, version=3):
    """records: list of (format, id, payload) in write order; last written is walked first."""
    body = b"".join(p + struct.pack("<BBI", f, i, len(p)) for f, i, p in records)
    size = len(body) + I.FOOTER
    return body + b"\0" * 32 + struct.pack("<II", size, version) + I.MAGIC


def test_trailer_parse_f64_gyro_exposure_gps(tmp_path):
    n = 100
    t_ms = 5000 + np.arange(n) * 5
    imu = np.zeros(n, np.dtype([("t", "<u8"), ("v", "<f8", 6)]))
    imu["t"], imu["v"][:, 2], imu["v"][:, 3] = t_ms, 1.0, np.linspace(0, 1, n)
    exp = np.zeros(10, np.dtype([("t", "<u8"), ("e", "<f8")]))
    exp["t"], exp["e"] = 5000 + np.arange(10) * 33, 1 / 120
    gps = struct.pack("<QHcdcdcddd", 1700000000, 250, b"A", 30.25, b"N", 97.75, b"W", 2.0, 90.0, 150.0)
    meta = bytes([0xC0, 0x01, 0x80, 0x10])  # tag 24 varint 2048... (24<<3|0 = 192 -> 0xC0 0x01), value 0x80 0x10 = 2048
    blob = b"\xff\xd8 fake jpeg \xff\xd9" + build_trailer(
        [(1, 1, meta), (0, 7, gps), (0, 4, exp.tobytes()), (0, 3, imu.tobytes())])
    p = tmp_path / "x.insp"
    p.write_bytes(blob)
    tel = I.parse_trailer(p)
    assert tel.has_trailer and tel.version == 3
    assert tel.t_imu.shape == (n,) and tel.t_imu[0] == 0 and tel.t_imu[-1] == pytest.approx(0.495)
    assert np.allclose(tel.accel[:, 2], 1.0) and tel.gyro[-1, 0] == pytest.approx(1.0)
    assert tel.exposure_s[0] == pytest.approx(1 / 120) and len(tel.t_exposure) == 10
    assert tel.gps[0, 2] == pytest.approx(30.25) and tel.gps[0, 3] == pytest.approx(-97.75)
    assert tel.meta[24] == 2048
    out = tmp_path / "t.csv"
    I.write_telemetry_csv(tel, out)
    assert out.read_text().count("\n") == n + 1 and (tmp_path / "t.exposure.csv").exists()
    assert I.parse_trailer(blob).gyro.shape == (n, 3)  # bytes input


def test_trailer_raw_gyro_scaling():
    n = 20
    a = np.zeros(n, np.dtype([("t", "<u8"), ("v", "<u2", 6)]))
    a["t"] = 100 + np.arange(n) * 4
    a["v"][:, :3] = 32768 + 2048   # +1 g at +-16 g range
    a["v"][:, 3:] = 32768 + 16384  # +1000 deg/s
    tel = I.parse_trailer(b"JUNK" + build_trailer([(0, 3, a.tobytes())]))
    assert tel.accel[0, 0] == pytest.approx(1.0) and tel.gyro[0, 0] == pytest.approx(np.deg2rad(1000))


def test_trailer_absent_or_garbled_degrades(tmp_path):
    assert not I.parse_trailer(b"nothing here" * 20).has_trailer
    assert not I.parse_trailer(tmp_path / "missing.insv").has_trailer
    bad = b"x" * 10 + b"\0" * 32 + struct.pack("<II", 10**9, 1) + I.MAGIC  # absurd size
    assert not I.parse_trailer(bad).has_trailer
    trunc = build_trailer([(0, 3, b"\x01" * 5)])  # not a valid gyro payload
    tel = I.parse_trailer(trunc)
    assert tel.has_trailer and len(tel.t_imu) == 0


# ---- CLI on a synthetic .insv (ffmpeg)
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_cli_stitch_and_telemetry(tmp_path):
    cal = I.Calibration()
    fr = render_dual_fisheye(cal)
    vid = tmp_path / "VID_test.mp4"
    w = cv2.VideoWriter(str(vid), cv2.VideoWriter_fourcc(*"mp4v"), 10, (2 * TILE, TILE))
    if not w.isOpened():
        pytest.skip("no mp4 writer")
    for i in range(30):
        f = fr.copy()
        if i in (10, 11, 12):  # blurry frames around t=1.0 s: the sharp ones in the window must win
            f = cv2.GaussianBlur(f, (0, 0), 6)
        w.write(f)
    w.release()
    insv = tmp_path / "VID_test.insv"
    insv.write_bytes(vid.read_bytes() + build_trailer([(0, 3, np.zeros(2, np.dtype([("t", "<u8"), ("v", "<f8", 6)])).tobytes())]))
    out = tmp_path / "out"
    assert I.main(["stitch", str(insv), str(out), "--every", "1.0", "--width", "480"]) == 0
    jpgs = sorted(out.glob("*.jpg"))
    assert len(jpgs) >= 2 and cv2.imread(str(jpgs[0])).shape == (240, 480, 3)
    assert I.main(["telemetry", str(insv), str(tmp_path / "t.csv")]) == 0


def test_api_wrappers(tmp_path):
    cal = I.Calibration()
    fr = render_dual_fisheye(cal)
    (tmp_path / "IMG_x.insp").write_bytes(cv2.imencode(".jpg", fr)[1].tobytes())
    gyro = np.zeros(3, np.dtype([("t", "<u8"), ("v", "<f8", 6)]))
    gyro["t"] = [0, 5, 10]
    p = tmp_path / "IMG_x.insp"
    p.write_bytes(p.read_bytes() + build_trailer([(0, 3, gyro.tobytes())]))
    outs = I.stitch_file(p, tmp_path / "o", width=480)
    assert len(outs) == 1 and cv2.imread(outs[0]).shape == (240, 480, 3)
    tel = I.read_telemetry(p)
    assert tel["gyro"].shape == (3, 3) and tel["t"].shape == (3,)
    assert I.read_telemetry(outs[0]) is None
