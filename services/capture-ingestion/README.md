# capture-ingestion

Roadmap M2.4 (Insta360 raw stitching + telemetry) and tag auto-alignment. Math: `docs/math/coordinate-alignment.md`.

| Module | What |
|---|---|
| `fauxel_capture/alignment.py` | equirect projector, AprilTag localizer, Umeyama Sim3/SE3 (unchanged) |
| `fauxel_capture/insta360.py` | `.insp`/`.insv` dual-fisheye -> 2:1 equirect, calibration + refinement, trailer telemetry |
| `fauxel_capture/tags.py` | pool tag observations -> RANSAC Umeyama -> `T_bim_from_slam`; printable tag36h11 sheets |

## Install / test
```
pip install -e ".[apriltag,test]"
python -m pytest            # from services/capture-ingestion
```

## Insta360 (reference camera: ONE X2; X3 / RS 1-Inch layouts handled)
```
python -m fauxel_capture.insta360 stitch  VID_x.insv out/ --every 1.0 [--width 5760] [--calib cal.json] [--nadir 25]
python -m fauxel_capture.insta360 telemetry VID_x.insv imu.csv      # + imu.exposure.csv / imu.gps.csv when present
python -m fauxel_capture.insta360 refine  VID_x.insv cal.json --frames 6
```
* Inputs: `.insp`; `.insv` with both lenses side by side; two video streams in one file; or the `_00_` / `_10_` pair (give either file).
* Frames: the sharpest frame within +-0.25 s of every `--every` seconds (Laplacian variance, plus a running-median relative reject). `frames.csv` lists t and sharpness.
* Calibration JSON (`Calibration`): lens model (`equidistant`|`equisolid`), per lens `cx, cy, radius` (fractions of the tile), `fov_deg`, `yaw/pitch/roll_deg`, `blend_deg`, `swap_lenses`, `nadir_mask_deg`. Defaults are nominal X2 values: run `refine` on real footage first.
* API for other services: `stitch_file(path, out_dir, every_s, width) -> [jpg paths]`, `read_telemetry(path) -> dict | None` (`t`, `gyro` rad/s, `accel` g, exposure, gps).
* Telemetry trailer format: see the module docstring (magic `8db42d69...`, records walked back to front; ids 1 meta, 3 gyro, 4 exposure, 7 GPS). Written from the MIT/Apache telemetry-parser format description; no GPL code copied.

## Tag auto-alignment
```
python -m fauxel_capture.tags sheet 101 102 103 104 -o sheets/ --size-mm 200   # PNG per tag + PDF
```
```python
from fauxel_capture.tags import auto_align
res = auto_align([(equirect_bgr, pose_slam_from_cam), ...], "survey.json", tag_size_m=0.2, estimate_scale=False)
res.rmse_m, res.residuals_m, res.T_bim_from_slam, res.transformer()
```
Survey JSON: `{"frame": "internal|shared|ifc_site", "tags": {"101": [x, y, z], ...}}` (meters). Observations are kept at 1.5-4 m and < 50 deg incidence, pooled per tag by median. `AlignmentRefused` (a `ValueError`) is raised for RMSE > 5 cm, fewer than 3 tags or collinear tags. Print sheets at 100 %: the 100 mm bar must measure 100 mm.

Note: pupil-apriltags 1.0.4 showed intermittent heap corruption; `detect_frames` runs the detector single threaded.

## Limitations
Not validated on real X2 files (lens order, image circle radius, FOV, trailer record details); the `Offsets` trailer index (X4/X5) is not parsed; no vignetting correction; nadir is masked, not inpainted.
