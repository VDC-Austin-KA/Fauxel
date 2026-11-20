"""BCF 3.0 zip writer/reader (stdlib zipfile + ElementTree).

Issue dict: guid?, title, description, status, priority, type, labels[], author, assigned_to, created_at (ISO),
element_guids[] (IfcGuid), camera {position, direction, up, fov?, aspect?}, snapshot_png (bytes)?,
comments[{author, date, text}].
"""
from __future__ import annotations

import io
import uuid
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

_NS = {"xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance"}


def _x(el) -> bytes:
    ET.indent(el)
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(el, encoding="utf-8")


def _txt(parent, tag, text):
    if text not in (None, ""):
        ET.SubElement(parent, tag).text = str(text)


def _vec(parent, tag, v):
    e = ET.SubElement(parent, tag)
    for k, c in zip("XYZ", v):
        ET.SubElement(e, k).text = repr(float(c))


def _viewpoint(guid, issue):
    root = ET.Element("VisualizationInfo", {"Guid": guid, **_NS})
    comps = ET.SubElement(root, "Components")
    sel = ET.SubElement(comps, "Selection")
    for g in issue.get("element_guids") or []:
        ET.SubElement(sel, "Component", {"IfcGuid": g})
    ET.SubElement(comps, "Visibility", {"DefaultVisibility": "true"})
    cam = issue.get("camera")
    if cam:
        pc = ET.SubElement(root, "PerspectiveCamera")
        _vec(pc, "CameraViewPoint", cam["position"])
        _vec(pc, "CameraDirection", cam["direction"])
        _vec(pc, "CameraUpVector", cam.get("up", (0, 0, 1)))
        _txt(pc, "FieldOfView", cam.get("fov", 90.0))
        _txt(pc, "AspectRatio", cam.get("aspect", 2.0))
    return _x(root)


def _markup(guid, vp_guid, issue):
    root = ET.Element("Markup", _NS)
    now = datetime.now(timezone.utc).isoformat()
    top = ET.SubElement(root, "Topic", {"Guid": guid, "TopicType": issue.get("type") or "Issue",
                                        "TopicStatus": issue.get("status") or "Open"})
    _txt(top, "Title", issue.get("title") or "Untitled")
    _txt(top, "Priority", issue.get("priority"))
    if issue.get("labels"):
        lab = ET.SubElement(top, "Labels")
        for l in issue["labels"]:
            _txt(lab, "Label", l)
    _txt(top, "CreationDate", issue.get("created_at") or now)
    _txt(top, "CreationAuthor", issue.get("author") or "fauxel")
    _txt(top, "AssignedTo", issue.get("assigned_to"))
    _txt(top, "Description", issue.get("description"))
    com = ET.SubElement(top, "Comments")
    for c in issue.get("comments") or []:
        ce = ET.SubElement(com, "Comment", {"Guid": c.get("guid") or str(uuid.uuid4())})
        _txt(ce, "Date", c.get("date") or now)
        _txt(ce, "Author", c.get("author") or "fauxel")
        _txt(ce, "Comment", c.get("text"))
        ET.SubElement(ce, "Viewpoint", {"Guid": vp_guid})
    vps = ET.SubElement(top, "Viewpoints")
    vp = ET.SubElement(vps, "ViewPoint", {"Guid": vp_guid})
    _txt(vp, "Viewpoint", "viewpoint.bcfv")
    if issue.get("snapshot_png"):
        _txt(vp, "Snapshot", "snapshot.png")
    return _x(root)


def build_bcf_zip(project_name: str, issues: list) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("bcf.version", _x(ET.Element("Version", {"VersionId": "3.0", **_NS})))
        pi = ET.Element("ProjectInfo", _NS)
        pr = ET.SubElement(pi, "Project", {"ProjectId": str(uuid.uuid4())})
        _txt(pr, "Name", project_name)
        z.writestr("project.bcfp", _x(pi))
        ext = ET.Element("Extensions", _NS)
        for tag, items in (("TopicTypes", sorted({i.get("type") or "Issue" for i in issues} | {"Issue"})),
                           ("TopicStatuses", sorted({i.get("status") or "Open" for i in issues} | {"Open", "Closed"})),
                           ("Priorities", sorted({i["priority"] for i in issues if i.get("priority")}))):
            e = ET.SubElement(ext, tag)
            for it in items:
                _txt(e, tag[:-1] if tag != "Priorities" else "Priority", it)
        z.writestr("extensions.xml", _x(ext))
        for issue in issues:
            g = issue.get("guid") or str(uuid.uuid4())
            vp = str(uuid.uuid4())
            z.writestr(f"{g}/markup.bcf", _markup(g, vp, issue))
            z.writestr(f"{g}/viewpoint.bcfv", _viewpoint(vp, issue))
            if issue.get("snapshot_png"):
                z.writestr(f"{g}/snapshot.png", issue["snapshot_png"])
    return buf.getvalue()


def _t(el, path):
    f = el.find(path)
    return f.text if f is not None else None


def parse_bcf_zip(data: bytes) -> list:
    out = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = set(z.namelist())
        for n in sorted(names):
            if not n.endswith("/markup.bcf"):
                continue
            folder = n.rsplit("/", 1)[0]
            top = ET.fromstring(z.read(n)).find("Topic")
            issue = {"guid": top.get("Guid"), "type": top.get("TopicType"), "status": top.get("TopicStatus"),
                     "title": _t(top, "Title"), "priority": _t(top, "Priority"), "created_at": _t(top, "CreationDate"),
                     "author": _t(top, "CreationAuthor"), "assigned_to": _t(top, "AssignedTo"),
                     "description": _t(top, "Description"),
                     "labels": [l.text for l in top.findall("Labels/Label")],
                     "comments": [{"guid": c.get("Guid"), "date": _t(c, "Date"), "author": _t(c, "Author"), "text": _t(c, "Comment")}
                                  for c in top.findall("Comments/Comment")],
                     "element_guids": [], "camera": None, "snapshot_png": None}
            vp_name = _t(top, "Viewpoints/ViewPoint/Viewpoint")
            if vp_name and f"{folder}/{vp_name}" in names:
                vi = ET.fromstring(z.read(f"{folder}/{vp_name}"))
                issue["element_guids"] = [c.get("IfcGuid") for c in vi.findall("Components/Selection/Component")]
                pc = vi.find("PerspectiveCamera")
                if pc is not None:
                    v = lambda tag: [float(pc.find(f"{tag}/{k}").text) for k in "XYZ"]
                    issue["camera"] = {"position": v("CameraViewPoint"), "direction": v("CameraDirection"),
                                       "up": v("CameraUpVector"), "fov": float(_t(pc, "FieldOfView")),
                                       "aspect": float(_t(pc, "AspectRatio"))}
            snap = _t(top, "Viewpoints/ViewPoint/Snapshot")
            if snap and f"{folder}/{snap}" in names:
                issue["snapshot_png"] = z.read(f"{folder}/{snap}")
            out.append(issue)
    return out
