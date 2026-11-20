"""BCF 2.1 / 3.0 reader + 2.1 writer: round trips and hand-built vendor-style archives."""
from __future__ import annotations

import io
import sys
import urllib.request
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "services" / "api"))
from fauxel_api.qc import bcf as qc_bcf  # noqa: E402

from fauxel_openbim import bcf  # noqa: E402

G1, G2 = "2O2Fr$t4X7Zf8NOew3FLOH", "0YtXr3JBL9SPvXYWuCyc7P"
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 16


def issue(**over):
    d = {"guid": "11111111-1111-4111-8111-111111111111", "title": "Wall off by 40 mm", "description": "check", "status": "Open",
         "priority": "High", "type": "Issue", "labels": ["positional", "arch"], "author": "fauxel@example.com", "assigned_to": "ka@example.com",
         "created_at": "2026-11-01T10:00:00Z", "modified_at": "2026-11-02T10:00:00Z", "due_date": None, "stage": None,
         "element_guids": [G1, G2],
         "camera": {"type": "perspective", "position": [1.0, 2.0, 1.6], "direction": [0.0, 1.0, 0.0], "up": [0.0, 0.0, 1.0], "fov": 60.0},
         "snapshot_png": PNG, "comments": [{"guid": "22222222-2222-4222-8222-222222222222", "author": "ka@example.com",
                                            "date": "2026-11-02T11:00:00Z", "text": "agree"}]}
    d.update(over)
    return d


def names(data):
    return set(zipfile.ZipFile(io.BytesIO(data)).namelist())


def test_bcf21_roundtrip():
    data = bcf.build_bcf21_zip("Proj", [issue()])
    n = names(data)
    assert "bcf.version" in n and "11111111-1111-4111-8111-111111111111/markup.bcf" in n
    p = bcf.parse_bcf(data)
    assert p["version"].startswith("2.1") and not p["warnings"]
    i = p["issues"][0]
    assert i["title"] == "Wall off by 40 mm" and i["status"] == "Open" and i["priority"] == "High"
    assert set(i["labels"]) == {"positional", "arch"} and i["element_guids"] == [G1, G2]
    assert i["camera"]["type"] == "perspective" and i["camera"]["position"] == [1.0, 2.0, 1.6]
    assert i["snapshot_png"] == PNG and i["comments"][0]["text"] == "agree" and i["assigned_to"] == "ka@example.com"


def test_bcf30_writer_to_our_parser_and_convert():
    v30 = qc_bcf.build_bcf_zip("Proj", [issue(element_guids=[G1], element_ifc_guid=G1)])
    p = bcf.parse_bcf(v30)
    assert p["version"].startswith("3") and p["issues"][0]["title"] == "Wall off by 40 mm"
    v21 = bcf.convert(v30, "2.1")
    q = bcf.parse_bcf(v21)
    assert q["version"].startswith("2.1") and q["issues"][0]["guid"] == p["issues"][0]["guid"]
    assert set(q["issues"][0]["labels"]) == set(p["issues"][0]["labels"])
    assert bcf.parse_bcf(bcf.convert(v21, "3.0"))["version"].startswith("3")


def _zip(files: dict[str, str | bytes]) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for k, v in files.items():
            z.writestr(k, v)
    return b.getvalue()


BIMCOLLAB_21 = {
    "bcf.version": '<?xml version="1.0" encoding="utf-8"?><Version VersionId="2.1" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><DetailedVersion>2.1</DetailedVersion></Version>',
    "aaaaaaaa-0000-4000-8000-000000000001/markup.bcf": """<?xml version="1.0" encoding="utf-8"?>
<Markup xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
 <Header><File IfcProject="0J$yPqHBD12v72y4qF6XcD" isExternal="true"><Filename>Tower.ifc</Filename><Date>2026-10-01T00:00:00Z</Date></File></Header>
 <Topic Guid="aaaaaaaa-0000-4000-8000-000000000001" TopicType="Clash" TopicStatus="Resolved">
  <Title>Duct vs beam</Title><Priority>Critical</Priority><Labels>MEP</Labels><Labels>Level 2</Labels>
  <CreationDate>2026-10-02T08:00:00+00:00</CreationDate><CreationAuthor>a@b.c</CreationAuthor><AssignedTo>x@y.z</AssignedTo>
  <Description>Clash</Description></Topic>
 <Comment Guid="cccccccc-0000-4000-8000-000000000001"><Date>2026-10-03T08:00:00Z</Date><Author>a@b.c</Author><Comment>fixed</Comment><Viewpoint Guid="vvvvvvvv-0000-4000-8000-000000000001"/></Comment>
 <Viewpoints Guid="vvvvvvvv-0000-4000-8000-000000000001"><Viewpoint>viewpoint.bcfv</Viewpoint><Snapshot>snapshot.png</Snapshot></Viewpoints>
</Markup>""",
    "aaaaaaaa-0000-4000-8000-000000000001/viewpoint.bcfv": f"""<?xml version="1.0" encoding="utf-8"?>
<VisualizationInfo Guid="vvvvvvvv-0000-4000-8000-000000000001">
 <Components><Selection><Component IfcGuid="{G1}"/><Component IfcGuid="{G2}"/></Selection><Visibility DefaultVisibility="true"/></Components>
 <OrthogonalCamera><CameraViewPoint><X>1</X><Y>2</Y><Z>3</Z></CameraViewPoint><CameraDirection><X>0</X><Y>1</Y><Z>0</Z></CameraDirection>
 <CameraUpVector><X>0</X><Y>0</Y><Z>1</Z></CameraUpVector><ViewToWorldScale>5</ViewToWorldScale></OrthogonalCamera>
</VisualizationInfo>""",
    "aaaaaaaa-0000-4000-8000-000000000001/snapshot.png": PNG,
}

REVIT_30 = {
    "bcf.version": '<?xml version="1.0" encoding="UTF-8"?><Version VersionId="3.0"><DetailedVersion>3.0</DetailedVersion></Version>',
    "bbbbbbbb-0000-4000-8000-000000000002/markup.bcf": f"""<?xml version="1.0" encoding="UTF-8"?>
<Markup xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
 <Header><File IfcProject="0J$yPqHBD12v72y4qF6XcD"><Filename>Tower.ifc</Filename></File></Header>
 <Topic Guid="bbbbbbbb-0000-4000-8000-000000000002" TopicType="Request" TopicStatus="Closed" Priority="Low">
  <Title>Door swing</Title><CreationDate>2026-10-02T08:00:00Z</CreationDate><CreationAuthor>a@b.c</CreationAuthor>
  <Labels><Label>Arch</Label></Labels>
  <Comments><Comment Guid="dddddddd-0000-4000-8000-000000000002"><Date>2026-10-03T08:00:00Z</Date><Author>a@b.c</Author><Comment>ok</Comment></Comment></Comments>
  <Viewpoints><ViewPoint Guid="wwwwwwww-0000-4000-8000-000000000002"><Viewpoint>vp.bcfv</Viewpoint></ViewPoint></Viewpoints>
 </Topic></Markup>""",
    "bbbbbbbb-0000-4000-8000-000000000002/vp.bcfv": f"""<?xml version="1.0" encoding="UTF-8"?>
<VisualizationInfo Guid="wwwwwwww-0000-4000-8000-000000000002"><Components><Selection><Component IfcGuid="{G2}"/></Selection></Components>
<PerspectiveCamera><CameraViewPoint><X>0</X><Y>0</Y><Z>1</Z></CameraViewPoint><CameraDirection><X>1</X><Y>0</Y><Z>0</Z></CameraDirection>
<CameraUpVector><X>0</X><Y>0</Y><Z>1</Z></CameraUpVector><FieldOfView>60</FieldOfView><AspectRatio>1.78</AspectRatio></PerspectiveCamera></VisualizationInfo>""",
}


def test_vendor_style_21_sibling_comments_and_repeated_labels():
    p = bcf.parse_bcf(_zip(BIMCOLLAB_21))
    i = p["issues"][0]
    assert p["version"].startswith("2.1") and i["status"] == "Resolved" and i["priority"] == "Critical"
    assert i["labels"] == ["MEP", "Level 2"] and i["comments"][0]["text"] == "fixed"
    assert i["element_guids"] == [G1, G2] and i["camera"]["type"] == "orthogonal" and i["snapshot_png"] == PNG
    assert p["files"][0]["filename"] == "Tower.ifc"
    assert bcf.is_closed("Resolved") and bcf.severity_of("Critical") in ("high", "critical")


def test_vendor_style_30_nested():
    p = bcf.parse_bcf(_zip(REVIT_30))
    i = p["issues"][0]
    assert p["version"].startswith("3") and i["labels"] == ["Arch"] and i["comments"][0]["text"] == "ok"
    assert i["element_guids"] == [G2] and i["camera"]["type"] == "perspective" and bcf.is_closed(i["status"])


@pytest.mark.parametrize("bad", [b"not a zip", _zip({"x.txt": "y"})])
def test_garbage_is_rejected_or_warned(bad):
    try:
        p = bcf.parse_bcf(bad)
    except bcf.BcfError:
        return
    assert p["issues"] == [] and p["warnings"]


def test_xml_entities_are_rejected():
    evil = dict(BIMCOLLAB_21)
    k = "aaaaaaaa-0000-4000-8000-000000000001/markup.bcf"
    evil[k] = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "boom">]>' + evil[k].split("?>", 1)[1]
    p = bcf.parse_bcf(_zip(evil))
    assert p["issues"] == [] and any("DOCTYPE" in w for w in p["warnings"])


def test_oversized_archive_rejected(monkeypatch):
    monkeypatch.setattr(bcf, "MAX_ENTRIES", 2)
    with pytest.raises(bcf.BcfError):
        bcf.parse_bcf(_zip({"a": "1", "b": "2", "c": "3"}))


# ---------- XSD validation (network: buildingSMART schemas) ----------

RAW = "https://raw.githubusercontent.com/buildingSMART/BCF-XML/{ref}/Schemas/{f}"


def _schema(tmp_path_factory, ref, files):
    lxml = pytest.importorskip("lxml.etree")
    d = tmp_path_factory.mktemp(ref)
    try:
        for f in files:
            (d / f).write_bytes(urllib.request.urlopen(RAW.format(ref=ref, f=f), timeout=20).read())
    except OSError:
        pytest.skip("buildingSMART schemas not reachable")
    return {f: lxml.XMLSchema(lxml.parse(str(d / f))) for f in files if f != "shared-types.xsd"}, lxml


@pytest.mark.network
def test_written_21_validates_against_buildingsmart_xsd(tmp_path_factory):
    sch, lxml = _schema(tmp_path_factory, "release_2_1", ["markup.xsd", "visinfo.xsd"])
    z = zipfile.ZipFile(io.BytesIO(bcf.build_bcf21_zip("Proj", [issue(), issue(guid="33333333-3333-4333-8333-333333333333", camera=None, snapshot_png=None, element_guids=[])])))
    checked = 0
    for n in z.namelist():
        if n.endswith("markup.bcf"):
            sch["markup.xsd"].assertValid(lxml.fromstring(z.read(n)))
            checked += 1
        elif n.endswith(".bcfv"):
            sch["visinfo.xsd"].assertValid(lxml.fromstring(z.read(n)))
            checked += 1
    assert checked >= 3


@pytest.mark.network
def test_existing_30_writer_validates_against_buildingsmart_xsd(tmp_path_factory):
    sch, lxml = _schema(tmp_path_factory, "release_3_0", ["shared-types.xsd", "markup.xsd", "visinfo.xsd"])
    z = zipfile.ZipFile(io.BytesIO(bcf.build_bcf30_zip("Proj", [issue()])))
    for n in z.namelist():
        if n.endswith("markup.bcf"):
            sch["markup.xsd"].assertValid(lxml.fromstring(z.read(n)))
        elif n.endswith(".bcfv"):
            sch["visinfo.xsd"].assertValid(lxml.fromstring(z.read(n)))
