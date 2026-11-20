# Phase 2 — capture-ingestion

> Original design brief. Decision log updates: the reference camera is the Insta360 ONE X2; poses are stored as quaternions; stella_vslam (BSD-2) replaces OpenVSLAM; OpenSpace API is a second capture source (roadmap M2.5). Working alignment code: `services/capture-ingestion/fauxel_capture/alignment.py`.

Act as a Senior Computer Vision & SLAM Engineer. Build the `capture-ingestion` pipeline for Fauxel optimized for Insta360 cameras (Insta360 X3, X4, ONE RS 1-Inch 360, Pro 2).

**Implement the end-to-end ingestion workflow:**
1. **Raw Media & Telemetry Extraction:**
   - Ingest raw `.insv` (dual-fisheye video), `.insp` (dual-fisheye photo), and pre-stitched equirectangular `.mp4`/`.jpg` files.
   - Interface with the **Insta360 Media SDK** (C++ wrapper with Python bindings) and fallback **OpenCV** dual-fisheye lens calibration stitcher to output calibrated 2:1 equirectangular frames (e.g., 5760x2880 or 7680x3840) with exposure compensation and zenith/nadir tripod masking.
   - Extract embedded gyroscope, accelerometer (IMU), and GPS metadata streams from the `.insv` container.

2. **Spherical Visual-Inertial Odometry (VIO) & VSLAM:**
   - Implement a spherical camera SLAM pipeline (wrapping **ORB-SLAM3** / **OpenVSLAM** configured for equirectangular camera models) fused with IMU pre-integration to reconstruct metric-scale 6-DoF camera trajectories `(x, y, z, roll, pitch, yaw)` across indoor construction sites without GPS.
   - Detect **AprilTag / ArUco floor markers** placed at known building grid intersections for automatic drift correction, loop closure, and multi-floor alignment.

3. **BIM Floorplan Registration & Keyframe Selection:**
   - Implement a semi-automated **3-Point / Grid-Anchor Registration Solver** (Umeyama/Kabsch alignment algorithm) that locks the VSLAM trajectory onto the 2D/3D BIM floorplan coordinate system.
   - Sample adaptive keyframes every 1.0–1.5 meters of travel or 15 degrees of rotation, filtering out motion-blurred frames via Laplacian variance scoring.
   - Output standardized panorama tiles (Cubemap + multi-resolution equirectangular pyramids) to S3/MinIO object storage and register `trajectory_nodes` in PostgreSQL.
