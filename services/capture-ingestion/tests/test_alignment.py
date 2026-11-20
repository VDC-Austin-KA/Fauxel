import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from fauxel_capture.alignment import (
    METERS_TO_FEET, BimCoordinateTransformer, EquirectangularProjector, Pose3D, UmeyamaAligner,
)

TAGS_BIM = np.array([[12.4, 4.2, 0.0], [24.8, 4.2, 0.0], [24.8, 16.5, 0.0], [12.4, 16.5, 0.0], [18.6, 10.3, 3.0]])
S, R, T = 1.42, Rotation.from_euler("z", 37.5, degrees=True).as_matrix(), np.array([5.10, -8.30, 1.45])


def _slam_obs():
    return ((TAGS_BIM - T) @ R) / S  # inverse of p_bim = s R p_slam + t


def test_umeyama_recovers_sim3():
    s, r, t, inliers, rmse = UmeyamaAligner.align_with_ransac(_slam_obs(), TAGS_BIM)
    assert s == pytest.approx(S, rel=1e-9)
    assert np.allclose(r, R) and np.allclose(t, T)
    assert inliers.all() and rmse < 1e-9


def test_ransac_rejects_outlier():
    src = _slam_obs()
    dst = TAGS_BIM.copy()
    dst[2] += [0.5, 0.0, 0.0]  # one bad survey point
    extra = np.array([[30.0, 25.0, 0.0], [5.0, 22.0, 3.0]])
    src = np.vstack([src, ((extra - T) @ R) / S])
    dst = np.vstack([dst, extra])
    s, r, t, inliers, rmse = UmeyamaAligner.align_with_ransac(src, dst)
    assert not inliers[2] and inliers.sum() == len(dst) - 1
    assert s == pytest.approx(S, rel=1e-6) and rmse < 1e-6


def test_se3_mode_keeps_unit_scale():
    s, *_ = UmeyamaAligner.solve_closed_form(_slam_obs() * S, TAGS_BIM, estimate_scale=False)
    assert s == 1.0


def test_too_few_points_raises():
    with pytest.raises(ValueError):
        UmeyamaAligner.align_with_ransac(TAGS_BIM[:2], TAGS_BIM[:2])


def test_revit_shared_identity_is_feet_conversion():
    tf = BimCoordinateTransformer(1.0, np.eye(3), np.zeros(3))
    pose = Pose3D(np.eye(3), np.array([1.0, 2.0, 3.0]))
    shared = tf.to_revit_shared_feet(pose, np.zeros(3), np.zeros(3), 0.0)
    assert np.allclose(shared.translation, [METERS_TO_FEET, 2 * METERS_TO_FEET, 3 * METERS_TO_FEET])


def test_equirect_forward_view_samples_panorama_center():
    proj = EquirectangularProjector(512, 256, virt_size=64)
    eq = np.zeros((256, 512), np.uint8)
    eq[118:138, 246:266] = 255  # bright patch at panorama center (+Z forward)
    rect, _ = proj.extract_rectilinear_view(eq, 0.0, 0.0)
    assert rect[32, 32] == 255 and rect[0, 0] == 0
