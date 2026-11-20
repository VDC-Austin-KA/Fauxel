# Coordinate Alignment Math

Implementation: [`services/capture-ingestion/fauxel_capture/alignment.py`](../../services/capture-ingestion/fauxel_capture/alignment.py) (tests in `services/capture-ingestion/tests/`).

## A. Equirectangular Spherical Projection Model

An Insta360 stitched panorama of resolution $W \times H$ ($W = 2H$) maps pixel coordinates $(u, v) \in [0, W) \times [0, H)$ to spherical azimuth $\theta \in [-\pi, \pi)$ and elevation $\phi \in [-\frac{\pi}{2}, \frac{\pi}{2}]$.

Using standard OpenCV camera conventions ($+X$ right, $+Y$ down, $+Z$ forward along the optical center $(W/2, H/2)$):

$$\theta = \left(\frac{u + 0.5}{W} - 0.5\right) 2\pi, \qquad \phi = \left(\frac{v + 0.5}{H} - 0.5\right) \pi$$

The unit bearing ray $\mathbf{r}_c \in \mathbb{S}^2$ in the equirectangular camera frame is:

$$\mathbf{r}_c = \begin{bmatrix} x_c \\ y_c \\ z_c \end{bmatrix} = \begin{bmatrix} \cos\phi \sin\theta \\ \sin\phi \\ \cos\phi \cos\theta \end{bmatrix}$$

Conversely, any 3D point $\mathbf{p}_c = [x_c, y_c, z_c]^T$ in the camera frame projects back to equirectangular pixel coordinates via:

$$u = W \left(\frac{\operatorname{atan2}(x_c, z_c)}{2\pi} + 0.5\right) - 0.5, \qquad v = H \left(\frac{\arcsin(y_c / \Vert\mathbf{p}_c\Vert)}{\pi} + 0.5\right) - 0.5$$

## B. Tangent-Plane Rectilinear Unwrapping for AprilTag Detection

Fiducial detectors assume perspective (pinhole) geometry and fail under equirectangular distortion. To detect AprilTags reliably anywhere around the camera, project the sphere onto $K$ overlapping virtual pinhole cameras (e.g., 6 horizontal views at $60^\circ$ yaw increments + downward pitch views for floor-mounted tags).

For a virtual camera with focal length $f_v = \frac{W_v}{2 \tan(\text{FOV}_v / 2)}$, principal point $(c_x, c_y) = (W_v/2, H_v/2)$, and orientation $R_{c \leftarrow v} \in SO(3)$ relative to the panorama center:

$$\mathbf{r}_c(u_v, v_v) = R_{c \leftarrow v} \, K_v^{-1} \begin{bmatrix} u_v \\ v_v \\ 1 \end{bmatrix}, \qquad K_v = \begin{bmatrix} f_v & 0 & c_x \\ 0 & f_v & c_y \\ 0 & 0 & 1 \end{bmatrix}$$

Normalizing $\mathbf{r}_c$ and applying the inverse spherical mapping yields a lookup table $(u, v)$ for fast bilinear remapping (`cv2.remap`).

## C. 6-DoF Fiducial Pose Estimation

Given a detected AprilTag of physical edge length $s$ (meters) with 3D tag-frame corners $\mathcal{P}_{tag} = \{(\pm s/2, \pm s/2, 0)\}$ and detected 2D virtual pixel corners $\mathcal{U}_v$:

1. Solve planar PnP with `SOLVEPNP_IPPE_SQUARE` to obtain $T_{v \leftarrow tag} \in SE(3)$.
2. Lift into the equirectangular camera frame: $T_{c \leftarrow tag} = \begin{bmatrix} R_{c \leftarrow v} & \mathbf{0} \\ \mathbf{0}^T & 1 \end{bmatrix} T_{v \leftarrow tag}$
3. Given the VSLAM pose $T_{slam \leftarrow c}^{(k)}$ at keyframe $k$: $\mathbf{x}_i = R_{slam \leftarrow c}^{(k)} \mathbf{t}_{c \leftarrow tag} + \mathbf{t}_{slam \leftarrow c}^{(k)}$

## D. Closed-Form Umeyama Alignment (Sim(3) and SE(3))

Monocular spherical VSLAM has arbitrary scale $s$, rotation $R$ and translation $\mathbf{t}$ (Sim(3)). Calibrated VIO preserves metric scale ($s = 1$, SE(3)).

Given $N \ge 3$ non-collinear tag observations $\{\mathbf{x}_i\}$ in VSLAM coordinates and surveyed BIM coordinates $\{\mathbf{y}_i\}$:

$$\min_{s > 0,\, R \in SO(3),\, \mathbf{t} \in \mathbb{R}^3} \frac{1}{N} \sum_{i=1}^N \left\Vert \mathbf{y}_i - (s R \mathbf{x}_i + \mathbf{t}) \right\Vert_2^2$$

1. Centroids and variance: $\boldsymbol{\mu}_x = \frac{1}{N}\sum \mathbf{x}_i$, $\boldsymbol{\mu}_y = \frac{1}{N}\sum \mathbf{y}_i$, $\sigma_x^2 = \frac{1}{N}\sum \Vert\mathbf{x}_i - \boldsymbol{\mu}_x\Vert^2$
2. Cross-covariance and SVD: $\Sigma_{xy} = \frac{1}{N}\sum (\mathbf{y}_i - \boldsymbol{\mu}_y)(\mathbf{x}_i - \boldsymbol{\mu}_x)^T = U D V^T$
3. Reflection-safe solution: $S = \operatorname{diag}(1, 1, \det(U)\det(V))$, $R = U S V^T$, $s = \operatorname{tr}(DS)/\sigma_x^2$ (Sim(3)) or $1$ (SE(3)), $\mathbf{t} = \boldsymbol{\mu}_y - s R \boldsymbol{\mu}_x$

The camera pose in the BIM frame: $T_{bim \leftarrow c} = \begin{bmatrix} sR & \mathbf{t} \\ \mathbf{0}^T & 1 \end{bmatrix} T_{slam \leftarrow c}$, with rotation kept orthonormal (scale applies to translation only).

RANSAC over 3-point samples (5 cm inlier threshold by default) rejects bad tag detections or bad survey points, followed by a refit on all inliers.

## E. Revit and IFC Frame Reconciliation

- **OpenCV / spherical camera:** $+X$ right, $+Y$ down, $+Z$ forward (meters)
- **IFC world (`IfcSite` / `IfcBuildingStorey`):** right-handed $+X$ East, $+Y$ North, $+Z$ Up (check `IfcUnitAssignment`)
- **Revit:** right-handed $+X$ East, $+Y$ North, $+Z$ Up, internal units **decimal feet** ($1\,\text{m} = 3.280839895013123\,\text{ft}$), with an Internal Origin, a Project Base Point rotated from True North by $\alpha_{TN}$, and a Survey Point

Optical to architectural basis change:

$$M_{arch \leftarrow cv} = \begin{bmatrix} 1 & 0 & 0 \\ 0 & 0 & 1 \\ 0 & -1 & 0 \end{bmatrix}$$

Internal to shared (reference formula): $\mathbf{p}_{shared} = R_z(-\alpha_{TN})(\mathbf{p}_{int} - \mathbf{p}_{pbp}) + \mathbf{p}_{sp}$

> **Decision log:** inside the Revit plugin, use `ProjectLocation.GetTotalTransform()` for internal→shared, and use the Python transformer as the test oracle. Each surveyed tag set must declare its frame (`internal` | `shared` | `ifc_site`), because `to_revit_internal_feet` assumes the tag survey frame equals Revit internal coordinates.

## Field Calibration Rules

- **Tag placement:** at least 4 AprilTags (`tag36h11`, at least 20 × 20 cm) per level at non-collinear column faces or surveyed grid intersections. Coplanar tags on one wall cause rotational ambiguity around the wall normal, so span both building axes.
- **Metric VIO vs. pure visual SLAM:** when fusing `.insv` IMU through VIO, use `estimate_scale=False` (SE(3)) after gravity alignment, or constrain $s \in [0.97, 1.03]$, to avoid false scale stretch on long corridors.
- **Multi-keyframe pooling:** don't align on single-frame detections. Average or bundle-adjust $\mathbf{x}_i$ across keyframes 1.5–4.0 m from the tag with incidence angle < 50°, and discard detections beyond 6 m, where PnP depth variance grows quadratically.
