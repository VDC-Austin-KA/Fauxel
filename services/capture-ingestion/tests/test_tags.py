import json

import cv2
import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from fauxel_capture import tags as T
from fauxel_capture.alignment import AprilTagLocalizer, EquirectangularProjector, Pose3D, TagObservation

S, R = 1.0, Rotation.from_euler("z", 37.5, degrees=True).as_matrix()
t0 = np.array([5.1, -8.3, 1.45])
BIM = {101: [12.4, 4.2, 0.0], 102: [24.8, 4.2, 0.0], 103: [24.8, 16.5, 0.0], 104: [12.4, 16.5, 0.0], 105: [18.6, 10.3, 3.0]}
SURVEY = T.TagSurvey.load({"frame": "shared", "tags": {str(k): v for k, v in BIM.items()}})


def obs(tag_id, p_slam, dist=2.5, incidence_deg=20.0, idx=0):
    """Synthetic observation: tag at `dist` m from the camera, viewed `incidence_deg` off its normal."""
    a = np.deg2rad(incidence_deg)
    Rct = Rotation.from_euler("y", incidence_deg, degrees=True).as_matrix()  # normal tilted by incidence
    return TagObservation(tag_id, idx, Pose3D(Rct, np.array([0, 0, dist])), np.asarray(p_slam, float), 60.0)


def slam_of(p_bim, s=S):
    return ((np.asarray(p_bim) - t0) @ R) / s


def all_obs(s=S, noise=0.0, seed=0, frames=3):
    rng = np.random.default_rng(seed)
    return [obs(k, slam_of(v, s) + rng.normal(0, noise, 3), idx=i) for k in BIM for i in range(frames)
            for v in [BIM[k]]]


@pytest.mark.parametrize("scale_free", [True, False])
def test_recovers_transform(scale_free):
    s = 1.42 if scale_free else 1.0
    res = T.align_observations(all_obs(s, noise=0.004), SURVEY, estimate_scale=scale_free)
    assert res.rmse_m < 0.02 and res.frame == "shared"
    assert res.scale == pytest.approx(s, rel=5e-3)
    assert all(res.inliers.values()) and set(res.residuals_m) == set(BIM)
    # transformer round-trip
    p = res.transformer().to_ifc_pose(Pose3D(np.eye(3), slam_of(BIM[101], s))).translation
    assert np.allclose(p, BIM[101], atol=0.03)


def test_outlier_tag_flagged_and_excluded():
    o = all_obs(noise=0.003)
    o = [x if x.tag_id != 103 else TagObservation(103, x.keyframe_idx, x.pose_cam_from_tag, x.position_slam + [0.8, 0, 0], 60.0) for x in o]
    res = T.align_observations(o, SURVEY, estimate_scale=False)
    assert res.inliers[103] is False and res.residuals_m[103] > 0.5
    assert sum(res.inliers.values()) == 4 and res.rmse_m < 0.02


def test_refuses_high_rmse_and_too_few_and_collinear():
    noisy = [obs(k, slam_of(v) + np.random.default_rng(k).normal(0, 0.15, 3)) for k, v in BIM.items()]
    with pytest.raises(T.AlignmentRefused, match="RMSE|consistent"):
        T.align_observations(noisy, SURVEY, estimate_scale=False)
    with pytest.raises(T.AlignmentRefused, match=">= 3"):
        T.align_observations([obs(k, slam_of(BIM[k])) for k in (101, 102)], SURVEY)
    line = T.TagSurvey("internal", {1: np.array([0, 0, 0.]), 2: np.array([1, 0, 0.]), 3: np.array([2, 0, 0.])})
    with pytest.raises(T.AlignmentRefused, match="collinear"):
        T.align_observations([obs(k, v) for k, v in line.tags.items()], line)
    assert issubclass(T.AlignmentRefused, ValueError)


def test_geometry_gate_drops_far_and_grazing_observations():
    good = all_obs()
    bad = [obs(k, slam_of(BIM[k]) + [5, 5, 5], dist=6.0) for k in BIM] + \
          [obs(k, slam_of(BIM[k]) + [5, 5, 5], dist=2.5, incidence_deg=70) for k in BIM] + \
          [obs(k, slam_of(BIM[k]) + [5, 5, 5], dist=1.0) for k in BIM]
    res = T.align_observations(good + bad, SURVEY, estimate_scale=False)
    assert res.rmse_m < 1e-9 and all(n == 3 for n in res.n_observations.values())


def test_median_pooling_rejects_single_bad_view():
    o = all_obs(frames=5)
    o[0] = TagObservation(o[0].tag_id, 0, o[0].pose_cam_from_tag, o[0].position_slam + [0.4, 0.4, 0], 60.0)
    assert T.align_observations(o, SURVEY, estimate_scale=False).rmse_m < 1e-9


def test_survey_validation(tmp_path):
    with pytest.raises(ValueError):
        T.TagSurvey.load({"frame": "world", "tags": {}})
    with pytest.raises(ValueError):
        T.TagSurvey.load({"frame": "ifc_site", "tags": {"1": [0, 0]}})
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"frame": "ifc_site", "tags": {"7": [1, 2, 3]}}))
    assert T.TagSurvey.load(p).tags[7].tolist() == [1, 2, 3]


# ---- tag sheet + real detection through AprilTagLocalizer
def test_sheet_dimensions_and_detectable(tmp_path):
    img, dpi = T.tag_sheet(42, 200.0)
    assert img.shape[0] > img.shape[1] and abs(8 * round(200 / 8 / 25.4 * 300) / dpi * 25.4 - 200) < 1e-6
    from pupil_apriltags import Detector
    # detect on a modest crop (pupil-apriltags 1.0.4 intermittently corrupts the heap on page-sized inputs)
    small = np.ascontiguousarray(cv2.resize(img[:img.shape[1] + 200], (500, 500 + 120), interpolation=cv2.INTER_AREA))
    det = Detector(families="tag36h11", nthreads=1, quad_decimate=1.0)
    assert [x.tag_id for x in det.detect(small) if x.decision_margin > 30] == [42]
    paths = T.write_sheets([42, 43], tmp_path)
    assert {p.suffix for p in paths} >= {".png"}
    with pytest.raises(ValueError):
        T.tag_image(9999)


def render_equirect(tags_cam, tag_m, w=4096):
    """Ray-trace tags (centre in camera frame, facing the camera) into a w x w/2 equirect."""
    h = w // 2
    lon = (np.arange(w) + .5) / w * 2 * np.pi - np.pi
    lat = (np.arange(h) + .5) / h * np.pi - np.pi / 2
    d = np.stack([np.cos(lat)[:, None] * np.sin(lon)[None], np.broadcast_to(np.sin(lat)[:, None], (h, w)),
                  np.cos(lat)[:, None] * np.cos(lon)[None]], -1)
    out = np.full((h, w), 128, np.uint8)
    marker = cv2.aruco.generateImageMarker(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11), 0, 8 * 32, borderBits=1)
    for tid, c in tags_cam:
        marker = cv2.aruco.generateImageMarker(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11), tid, 8 * 32, borderBits=1)
        ez = c / np.linalg.norm(c)
        ex = np.cross([0, 1, 0], ez); ex /= np.linalg.norm(ex)
        ey = np.cross(ez, ex)
        lam = (c @ ez) / (d @ ez)
        pts = d * lam[..., None] - c
        a, b = pts @ ex, pts @ ey
        sel = (np.abs(a) < tag_m * .75) & (np.abs(b) < tag_m * .75) & (d @ ez > 0)
        u = ((a / tag_m + .5) * 256 - .5).astype(np.float32)
        v = ((b / tag_m + .5) * 256 - .5).astype(np.float32)
        tex = cv2.remap(marker, u, v, cv2.INTER_LINEAR, borderValue=255)
        out[sel] = tex[sel]
    return out


def test_end_to_end_with_rendered_tags():
    tag_m = 0.2
    # camera at SLAM origin, three keyframes (pure translations), tags 2-3.2 m away in different directions
    world = {10: [3.0, 1.0, 1.0], 11: [-2.0, 1.5, 0.5], 12: [0.5, -3.0, 1.2], 13: [-2.5, -1.5, -0.8]}
    # note: camera frame is y-down, so "world" here is directly SLAM = camera-aligned axes
    cams = [np.array([0, 0, 0.]), np.array([0.3, 0, 0.2]), np.array([-0.2, 0.1, -0.3])]
    true_R = Rotation.from_euler("z", 20, degrees=True).as_matrix()
    true_t = np.array([10.0, 20.0, 1.0])
    survey = T.TagSurvey("ifc_site", {k: true_R @ np.array(v) + true_t for k, v in world.items()})
    frames = []
    for c in cams:
        img = render_equirect([(k, np.array(v) - c) for k, v in world.items()], tag_m)
        frames.append((img, Pose3D(np.eye(3), c)))
    res = T.auto_align(frames, survey, tag_size_m=tag_m, estimate_scale=False)
    assert res.rmse_m < 0.05 and all(res.inliers.values()) and len(res.residuals_m) == 4
    assert np.allclose(res.R_bim_from_slam, true_R, atol=0.02) and np.allclose(res.t_bim_from_slam, true_t, atol=0.1)
