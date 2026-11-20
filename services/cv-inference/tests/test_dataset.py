import cv2
import numpy as np

from fauxel_cv.dataset import assign_splits, build, snap_rung


class FakeApi:
    def __init__(self, rows):
        self.rows = rows

    def samples(self, pid, since, source):
        return self.rows

    def crop(self, frame_id, element_uid, size):
        if frame_id == "bad":
            return None
        return cv2.imencode(".jpg", np.full((size, size, 3), 120, np.uint8))[1].tobytes()


def test_split_by_capture_no_leakage(tmp_path):
    rows = [{"observation_id": f"o{i}", "frame_id": "bad" if i == 0 else f"f{i}", "capture_id": f"c{i % 5}", "element_uid": f"e{i}",
             "trade": "arch_interior", "percent": [0, 30, 52, 100][i % 4]} for i in range(40)]
    stats = build(FakeApi(rows), "p", tmp_path, size=64)["arch_interior"]
    assert stats["skipped"] == 1 and stats["captures"] == 5 and stats["val"] > 0
    tr = {r["capture_id"] for l in (tmp_path / "arch_interior" / "samples.jsonl").read_text().splitlines()
          if (r := __import__("json").loads(l)) and r["path"].startswith("train")}
    va = {r["capture_id"] for l in (tmp_path / "arch_interior" / "samples.jsonl").read_text().splitlines()
          if (r := __import__("json").loads(l)) and r["path"].startswith("val")}
    assert tr and va and not tr & va
    assert snap_rung("arch_interior", 52)["percent"] == 50  # free-form percent snaps to the nearest rung
    assert set(assign_splits(["a"]).values()) == {"train"}
