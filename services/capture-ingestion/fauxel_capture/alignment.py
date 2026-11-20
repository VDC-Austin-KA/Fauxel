"""Equirect projection, AprilTag localization, Umeyama Sim(3)/SE(3) alignment, and BIM frame transforms.

Math: docs/math/coordinate-alignment.md
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
import cv2
import numpy as np
from scipy.spatial.transform import Rotation

# Optional: pip install pupil-apriltags
try:
    from pupil_apriltags import Detector as AprilTagDetector
except ImportError:
    AprilTagDetector = None


METERS_TO_FEET = 3.280839895013123


@dataclass(frozen=True)
class Pose3D:
    """Rigid 6-DoF pose T_{target <- source} in metric units."""
    rotation: np.ndarray      # (3, 3) SO(3) matrix
    translation: np.ndarray   # (3,) vector in meters

    def as_matrix(self) -> np.ndarray:
        T = np.eye(4, dtype=np.float64)
        T[:3, :3] = self.rotation
        T[:3, 3] = self.translation
        return T

    @classmethod
    def from_matrix(cls, T: np.ndarray) -> Pose3D:
        return cls(rotation=T[:3, :3].copy(), translation=T[:3, 3].copy())

    def inverse(self) -> Pose3D:
        R_inv = self.rotation.T
        t_inv = -R_inv @ self.translation
        return Pose3D(rotation=R_inv, translation=t_inv)

    def __matmul__(self, other: Pose3D) -> Pose3D:
        return Pose3D.from_matrix(self.as_matrix() @ other.as_matrix())


@dataclass(frozen=True)
class TagObservation:
    """Detected AprilTag localized in VSLAM world coordinates."""
    tag_id: int
    keyframe_idx: int
    pose_cam_from_tag: Pose3D
    position_slam: np.ndarray  # (3,) center of tag in VSLAM frame
    decision_margin: float


class EquirectangularProjector:
    """Projects 360 equirectangular panoramas onto rectilinear virtual cameras."""

    def __init__(self, eq_width: int, eq_height: int, virt_size: int = 1024, fov_deg: float = 95.0):
        assert eq_width == 2 * eq_height, "Equirectangular frames must enforce 2:1 aspect ratio."
        self.eq_w = eq_width
        self.eq_h = eq_height
        self.v_size = virt_size

        focal = (virt_size / 2.0) / np.tan(np.deg2rad(fov_deg) / 2.0)
        cx = cy = virt_size / 2.0
        self.K_virt = np.array([
            [focal, 0.0, cx],
            [0.0, focal, cy],
            [0.0, 0.0, 1.0],
        ], dtype=np.float64)

        # Precompute normalized pixel grid in virtual camera frame
        u_grid, v_grid = np.meshgrid(
            np.arange(virt_size, dtype=np.float64),
            np.arange(virt_size, dtype=np.float64),
        )
        ones = np.ones_like(u_grid)
        pix_hom = np.stack([u_grid, v_grid, ones], axis=-1).reshape(-1, 3).T
        rays = np.linalg.inv(self.K_virt) @ pix_hom
        self._base_rays = rays / np.linalg.norm(rays, axis=0, keepdims=True)
        self._remap_cache: Dict[Tuple[float, float], Tuple[np.ndarray, np.ndarray]] = {}

    def _build_remap_lut(self, yaw_deg: float, pitch_deg: float) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        # Rotate virtual camera in OpenCV frame: yaw around +Y (down), pitch around +X (right)
        R_cam_from_virt = Rotation.from_euler("yx", [yaw_deg, pitch_deg], degrees=True).as_matrix()
        key = (round(yaw_deg, 2), round(pitch_deg, 2))

        if key not in self._remap_cache:
            rays_c = R_cam_from_virt @ self._base_rays  # (3, N)
            x, y, z = rays_c[0], rays_c[1], rays_c[2]

            theta = np.arctan2(x, z)                     # [-pi, pi]
            phi = np.arcsin(np.clip(y, -1.0, 1.0))       # [-pi/2, pi/2]

            map_u = (self.eq_w * (theta / (2.0 * np.pi) + 0.5) - 0.5).reshape(self.v_size, self.v_size)
            map_v = (self.eq_h * (phi / np.pi + 0.5) - 0.5).reshape(self.v_size, self.v_size)
            self._remap_cache[key] = (map_u.astype(np.float32), map_v.astype(np.float32))

        map_u, map_v = self._remap_cache[key]
        return map_u, map_v, R_cam_from_virt

    def extract_rectilinear_view(
        self, equirect_img: np.ndarray, yaw_deg: float, pitch_deg: float
    ) -> Tuple[np.ndarray, np.ndarray]:
        map_u, map_v, R_cam_from_virt = self._build_remap_lut(yaw_deg, pitch_deg)
        rect_img = cv2.remap(
            equirect_img,
            map_u,
            map_v,
            interpolation=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_WRAP,
        )
        return rect_img, R_cam_from_virt


class AprilTagLocalizer:
    """Detects AprilTags across virtual tangent planes and lifts 6-DoF poses into VSLAM space."""

    # Ring of 6 horizontal views + 2 downward angled views for floor targets
    SCAN_ANGLES: List[Tuple[float, float]] = [
        (0.0, 0.0), (60.0, 0.0), (120.0, 0.0),
        (180.0, 0.0), (-120.0, 0.0), (-60.0, 0.0),
        (0.0, 45.0), (180.0, 45.0),
    ]

    def __init__(self, projector: EquirectangularProjector, tag_size_m: float, family: str = "tag36h11"):
        if AprilTagDetector is None:
            raise RuntimeError("Install pupil-apriltags: `pip install pupil-apriltags`")
        self.proj = projector
        self.tag_size = tag_size_m
        self.detector = AprilTagDetector(families=family, nthreads=4, quad_decimate=1.0, refine_edges=1)

        half = tag_size_m / 2.0
        # pupil-apriltags corner order: bottom-left, bottom-right, top-right, top-left
        self.obj_points = np.array([
            [-half,  half, 0.0],
            [ half,  half, 0.0],
            [ half, -half, 0.0],
            [-half, -half, 0.0],
        ], dtype=np.float64)

    def detect_in_keyframe(
        self, equirect_img: np.ndarray, keyframe_idx: int, pose_slam_from_cam: Pose3D
    ) -> List[TagObservation]:
        gray_eq = cv2.cvtColor(equirect_img, cv2.COLOR_BGR2GRAY) if equirect_img.ndim == 3 else equirect_img
        best_per_tag: Dict[int, TagObservation] = {}

        for yaw, pitch in self.SCAN_ANGLES:
            rect_gray, R_cam_from_virt = self.proj.extract_rectilinear_view(gray_eq, yaw, pitch)
            detections = self.detector.detect(rect_gray, estimate_tag_pose=False)

            for det in detections:
                if det.decision_margin < 35.0:
                    continue

                img_pts = np.asarray(det.corners, dtype=np.float64)
                ok, rvec, tvec = cv2.solvePnP(
                    self.obj_points,
                    img_pts,
                    self.proj.K_virt,
                    distCoeffs=None,
                    flags=cv2.SOLVEPNP_IPPE_SQUARE,
                )
                if not ok:
                    continue

                R_vt, _ = cv2.Rodrigues(rvec)
                T_virt_from_tag = Pose3D(rotation=R_vt, translation=tvec.reshape(3))
                T_cam_from_virt = Pose3D(rotation=R_cam_from_virt, translation=np.zeros(3))
                T_cam_from_tag = T_cam_from_virt @ T_virt_from_tag

                # Project tag center into VSLAM world frame
                p_slam = (
                    pose_slam_from_cam.rotation @ T_cam_from_tag.translation
                    + pose_slam_from_cam.translation
                )

                obs = TagObservation(
                    tag_id=int(det.tag_id),
                    keyframe_idx=keyframe_idx,
                    pose_cam_from_tag=T_cam_from_tag,
                    position_slam=p_slam,
                    decision_margin=float(det.decision_margin),
                )

                # Keep highest-confidence view if tag appears in overlapping virtual cameras
                if obs.tag_id not in best_per_tag or obs.decision_margin > best_per_tag[obs.tag_id].decision_margin:
                    best_per_tag[obs.tag_id] = obs

        return list(best_per_tag.values())


class UmeyamaAligner:
    """Closed-form Sim(3)/SE(3) Umeyama solver with RANSAC outlier rejection."""

    @staticmethod
    def solve_closed_form(
        src_pts: np.ndarray, dst_pts: np.ndarray, estimate_scale: bool = True
    ) -> Tuple[float, np.ndarray, np.ndarray]:
        """
        Aligns src_pts (VSLAM) -> dst_pts (BIM) minimizing ||dst - (s * R @ src + t)||^2.
        Args:
            src_pts: (N, 3) array
            dst_pts: (N, 3) array
            estimate_scale: True for Sim(3) monocular VSLAM, False for metric SE(3) VIO.
        """
        assert src_pts.shape == dst_pts.shape and src_pts.shape[0] >= 3
        n = src_pts.shape[0]

        mu_x = np.mean(src_pts, axis=0)
        mu_y = np.mean(dst_pts, axis=0)

        x_centered = src_pts - mu_x
        y_centered = dst_pts - mu_y

        var_x = np.sum(x_centered ** 2) / n
        if var_x < 1e-12:
            raise ValueError("Degenerate source point configuration (zero variance).")

        cov_xy = (y_centered.T @ x_centered) / n
        U, D, Vt = np.linalg.svd(cov_xy)

        S = np.eye(3, dtype=np.float64)
        if np.linalg.det(U) * np.linalg.det(Vt) < 0:
            S[2, 2] = -1.0

        R = U @ S @ Vt
        scale = float(np.trace(np.diag(D) @ S) / var_x) if estimate_scale else 1.0
        t = mu_y - scale * (R @ mu_x)

        return scale, R, t

    @classmethod
    def align_with_ransac(
        cls,
        src_pts: np.ndarray,
        dst_pts: np.ndarray,
        estimate_scale: bool = True,
        inlier_threshold_m: float = 0.05,
        max_iters: int = 500,
        seed: int = 42,
    ) -> Tuple[float, np.ndarray, np.ndarray, np.ndarray, float]:
        """Returns (scale, R, t, inlier_mask, rmse_m)."""
        n = src_pts.shape[0]
        if n < 3:
            raise ValueError("At least 3 AprilTag correspondences are required for 3D alignment.")

        if n == 3:
            s, R, t = cls.solve_closed_form(src_pts, dst_pts, estimate_scale)
            preds = s * (src_pts @ R.T) + t
            rmse = float(np.sqrt(np.mean(np.sum((dst_pts - preds) ** 2, axis=1))))
            return s, R, t, np.ones(n, dtype=bool), rmse

        rng = np.random.default_rng(seed)
        best_inliers = np.zeros(n, dtype=bool)
        best_count = 0

        for _ in range(max_iters):
            sample_idx = rng.choice(n, size=3, replace=False)
            try:
                s, R, t = cls.solve_closed_form(src_pts[sample_idx], dst_pts[sample_idx], estimate_scale)
            except ValueError:
                continue

            preds = s * (src_pts @ R.T) + t
            errors = np.linalg.norm(dst_pts - preds, axis=1)
            inliers = errors < inlier_threshold_m
            count = int(np.sum(inliers))

            if count > best_count:
                best_count = count
                best_inliers = inliers

        if best_count < 3:
            best_inliers = np.ones(n, dtype=bool)

        # Refine over all inliers
        s, R, t = cls.solve_closed_form(src_pts[best_inliers], dst_pts[best_inliers], estimate_scale)
        preds = s * (src_pts[best_inliers] @ R.T) + t
        rmse = float(np.sqrt(np.mean(np.sum((dst_pts[best_inliers] - preds) ** 2, axis=1))))

        return s, R, t, best_inliers, rmse


class BimCoordinateTransformer:
    """Converts aligned VSLAM poses into IFC (meters) and Revit (feet / Shared Coordinates)."""

    # Converts OpenCV camera optical axes (+X right, +Y down, +Z forward)
    # to Architectural axes (+X East/Right, +Y North/Forward, +Z Up)
    M_ARCH_FROM_CV = np.array([
        [1.0,  0.0, 0.0, 0.0],
        [0.0,  0.0, 1.0, 0.0],
        [0.0, -1.0, 0.0, 0.0],
        [0.0,  0.0, 0.0, 1.0],
    ], dtype=np.float64)

    def __init__(self, scale: float, R_bim_from_slam: np.ndarray, t_bim_from_slam: np.ndarray):
        self.scale = scale
        self.R = R_bim_from_slam
        self.t = t_bim_from_slam

    def to_ifc_pose(self, pose_slam_from_cam: Pose3D) -> Pose3D:
        """
        Transforms a VSLAM camera pose into IFC project coordinates (meters, +Z Up).
        Note: Scale scales translation, while camera orientation remains orthonormal in SO(3).
        """
        pos_ifc = self.scale * (self.R @ pose_slam_from_cam.translation) + self.t
        rot_ifc = self.R @ pose_slam_from_cam.rotation @ self.M_ARCH_FROM_CV[:3, :3].T
        return Pose3D(rotation=rot_ifc, translation=pos_ifc)

    def to_revit_internal_feet(self, pose_slam_from_cam: Pose3D) -> Pose3D:
        """Converts aligned pose into Revit Internal Origin coordinates (decimal feet)."""
        ifc_pose = self.to_ifc_pose(pose_slam_from_cam)
        return Pose3D(
            rotation=ifc_pose.rotation,
            translation=ifc_pose.translation * METERS_TO_FEET,
        )

    def to_revit_shared_feet(
        self,
        pose_slam_from_cam: Pose3D,
        project_base_point_ft: np.ndarray,
        survey_point_ft: np.ndarray,
        angle_to_true_north_deg: float,
    ) -> Pose3D:
        """
        Transforms Revit Internal Origin coordinates into Revit Shared/Survey coordinates.
        """
        internal_pose = self.to_revit_internal_feet(pose_slam_from_cam)
        R_tn = Rotation.from_euler("z", -angle_to_true_north_deg, degrees=True).as_matrix()

        pos_shared = R_tn @ (internal_pose.translation - project_base_point_ft) + survey_point_ft
        rot_shared = R_tn @ internal_pose.rotation
        return Pose3D(rotation=rot_shared, translation=pos_shared)


# --- Example End-to-End Execution ---
if __name__ == "__main__":
    # 1. Known BIM coordinates (in meters) of physical AprilTags mounted on site columns/floor
    bim_tag_survey_m: Dict[int, np.ndarray] = {
        101: np.array([12.40,  4.20, 0.00]),
        102: np.array([24.80,  4.20, 0.00]),
        103: np.array([24.80, 16.50, 0.00]),
        104: np.array([12.40, 16.50, 0.00]),
    }

    # 2. Simulate VSLAM tag observations (unscaled + rotated arbitrary SLAM origin)
    true_scale = 1.42
    true_R = Rotation.from_euler("z", 37.5, degrees=True).as_matrix()
    true_t = np.array([5.10, -8.30, 1.45])

    obs_slam, obs_bim = [], []
    for tag_id, p_bim in bim_tag_survey_m.items():
        # Invert p_bim = s * R @ p_slam + t
        p_slam = (true_R.T @ (p_bim - true_t)) / true_scale
        obs_slam.append(p_slam)
        obs_bim.append(p_bim)

    # 3. Solve Sim(3) Umeyama alignment
    s_est, R_est, t_est, inliers, rmse = UmeyamaAligner.align_with_ransac(
        np.array(obs_slam), np.array(obs_bim), estimate_scale=True
    )
    print(f"Estimated Scale: {s_est:.5f} | RMSE: {rmse * 1000:.2f} mm")

    # 4. Transform a sample VSLAM keyframe pose into IFC and Revit Shared Coordinates
    transformer = BimCoordinateTransformer(s_est, R_est, t_est)
    sample_slam_pose = Pose3D(rotation=np.eye(3), translation=np.array([2.0, 3.0, 0.0]))

    ifc_pose = transformer.to_ifc_pose(sample_slam_pose)
    revit_shared = transformer.to_revit_shared_feet(
        sample_slam_pose,
        project_base_point_ft=np.array([0.0, 0.0, 0.0]),
        survey_point_ft=np.array([1000.0, 2000.0, 500.0]),
        angle_to_true_north_deg=14.25,
    )
    print("IFC Position (m):", np.round(ifc_pose.translation, 4))
    print("Revit Shared Position (ft):", np.round(revit_shared.translation, 4))
