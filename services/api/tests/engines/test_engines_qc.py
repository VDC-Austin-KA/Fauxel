import io
import zipfile

from fauxel_api.qc.bcf import build_bcf_zip, parse_bcf_zip
from fauxel_api.qc.deviation import TOLERANCES_M, positional_deviation, precover_check

PNG = b"\x89PNG\r\n\x1a\nfake"


def test_bcf_roundtrip():
    issues = [{"guid": "11111111-1111-4111-8111-111111111111", "title": "Duct off by 5 cm", "description": "Check & fix <hvac>",
               "status": "Open", "priority": "High", "type": "Deviation", "labels": ["hvac", "L2"], "author": "ka@x.com",
               "created_at": "2025-01-01T10:00:00+00:00", "assigned_to": "bob",
               "element_guids": ["2O2Fr$t4X7Zf8NOew3FLOH", "0YXzFt7oD9Qv6wLq2GzJ1c"],
               "camera": {"position": [1, 2, 3], "direction": [0, 1, 0], "up": [0, 0, 1], "fov": 100, "aspect": 2},
               "snapshot_png": PNG, "comments": [{"author": "a", "date": "2025-01-02T00:00:00+00:00", "text": "see photo"}]},
              {"title": "No camera"}]
    data = build_bcf_zip("Proj", issues)
    z = zipfile.ZipFile(io.BytesIO(data))
    names = z.namelist()
    assert {"bcf.version", "project.bcfp", "extensions.xml"} <= set(names)
    assert b'VersionId="3.0"' in z.read("bcf.version")
    assert issues[0]["guid"] + "/viewpoint.bcfv" in names and issues[0]["guid"] + "/snapshot.png" in names
    back = {i["title"]: i for i in parse_bcf_zip(data)}
    a = back["Duct off by 5 cm"]
    assert a["guid"] == issues[0]["guid"] and a["description"] == "Check & fix <hvac>" and a["priority"] == "High"
    assert a["element_guids"] == issues[0]["element_guids"] and a["labels"] == ["hvac", "L2"]
    assert a["camera"]["position"] == [1, 2, 3] and a["camera"]["fov"] == 100
    assert a["comments"][0]["text"] == "see photo" and a["snapshot_png"] == PNG
    b = back["No camera"]
    assert b["camera"] is None and b["status"] == "Open" and b["guid"]


def test_positional_deviation_severity():
    r = positional_deviation((0, 0, 0), (0.0381, 0, 0), "hvac")
    assert r["severity"] == "low" and r["within_tolerance"] and r["tolerance_m"] == TOLERANCES_M["hvac"]
    assert positional_deviation((0, 0, 0), (0, 0.0381 * 1.5, 0), "hvac")["severity"] == "medium"
    assert positional_deviation((0, 0, 0), (0, 0, 0.0127 * 3), "structural")["severity"] == "high"
    r = positional_deviation((1, 1, 1), (1.2, 1, 1), "electrical")
    assert r["severity"] == "critical" and abs(r["dx"] - 0.2) < 1e-9 and abs(r["dist_m"] - 0.2) < 1e-9


def test_precover():
    assert precover_check("INSTALLED", ["NOT_STARTED"]) is True
    assert precover_check({"state": "INSTALLED", "percent": 80}, ["INSTALLED", "FRAMED_OR_ROUGHED"]) is True
    assert precover_check("INSULATED_OR_FINISHED", [{"state": "FRAMED_OR_ROUGHED", "verified": True}]) is False
    assert precover_check("FRAMED_OR_ROUGHED", ["NOT_STARTED"]) is False
    assert precover_check({"state": "FRAMED_OR_ROUGHED", "percent": 65}, ["NOT_STARTED"]) is False
    assert precover_check("INSTALLED", []) is False
