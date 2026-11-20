"""BCF 2.1 + 3.0 reader, BCF 2.1 writer (3.0 writer is reused from fauxel_api.qc.bcf).

Issue dict (same shape as fauxel_api.qc.bcf): guid, title, description, status, priority, type, labels[], author,
assigned_to, created_at, modified_at, due_date, stage, element_guids[] (IfcGuid, selection of every viewpoint, de-duplicated),
camera {type: perspective|orthogonal, position, direction, up, fov?, aspect?, scale?}|None, snapshot_png bytes|None,
comments[{guid,author,date,text}], viewpoints[{guid, file, snapshot}].

Schemas: buildingSMART BCF-XML release_2_1 and release_3_0 (Markup/VisualizationInfo xsd). Differences handled:
2.1 has Comment* and Viewpoints* as siblings of Topic, repeated <Labels>text</Labels>, no AspectRatio, FieldOfView 45..60,
Visibility required; 3.0 nests Comments/Viewpoints/Labels inside Topic and requires AspectRatio.
"""
from __future__ import annotations

import io
import re
import uuid
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

MAX_ENTRY = 64 * 1024 * 1024
MAX_TOTAL = 512 * 1024 * 1024
MAX_ENTRIES = 50_000
_NS = {"xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance", "xmlns:xsd": "http://www.w3.org/2001/XMLSchema"}


class BcfError(ValueError):
    pass


# ---------- reading ----------

def _safe_xml(data: bytes) -> ET.Element:
    head = data[:4096].upper()
    if b"<!DOCTYPE" in head or b"<!ENTITY" in data[:65536].upper():
        raise BcfError("DOCTYPE/ENTITY declarations are not allowed in BCF XML")
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise BcfError(f"invalid XML: {exc}") from exc


def _t(el, path):
    f = el.find(path)
    return f.text.strip() if f is not None and f.text and f.text.strip() else None


def _f(el, path):
    v = _t(el, path)
    try:
        return float(v) if v is not None else None
    except ValueError:
        return None


def _vec(el, tag):
    p = el.find(f"{{*}}{tag}")
    if p is None:
        return None
    vals = [_f(p, f"{{*}}{k}") for k in "XYZ"]
    return None if any(v is None for v in vals) else vals


def _camera(vi: ET.Element):
    for tag, kind in (("PerspectiveCamera", "perspective"), ("OrthogonalCamera", "orthogonal")):
        pc = vi.find(f"{{*}}{tag}")
        if pc is not None:
            pos, dr, up = _vec(pc, "CameraViewPoint"), _vec(pc, "CameraDirection"), _vec(pc, "CameraUpVector")
            if pos and dr:
                cam = {"type": kind, "position": pos, "direction": dr, "up": up or [0.0, 0.0, 1.0],
                       "aspect": _f(pc, "{*}AspectRatio")}
                if kind == "perspective":
                    cam["fov"] = _f(pc, "{*}FieldOfView")
                else:
                    cam["scale"] = _f(pc, "{*}ViewToWorldScale")
                return cam
    return None


def detect_version(z: zipfile.ZipFile) -> str | None:
    if "bcf.version" in z.namelist():
        v = _safe_xml(z.read("bcf.version"))
        return v.get("VersionId") or _t(v, "{*}DetailedVersion")
    return None


def parse_bcf(data: bytes) -> dict:
    """Parse a BCF 2.0/2.1/3.0 zip. Returns {"version", "files", "issues", "warnings"}. Raises BcfError on a bad archive."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise BcfError("not a zip archive") from exc
    infos = z.infolist()
    if len(infos) > MAX_ENTRIES or sum(i.file_size for i in infos) > MAX_TOTAL or any(i.file_size > MAX_ENTRY for i in infos):
        raise BcfError("BCF archive too large")
    names = {i.filename for i in infos}
    version = detect_version(z)
    out = {"version": version, "files": [], "issues": [], "warnings": []}
    for n in sorted(names):
        if not n.endswith("markup.bcf") or n.rsplit("/", 1)[-1] != "markup.bcf" or n.count("/") != 1:
            continue
        folder = n.rsplit("/", 1)[0]
        try:
            root = _safe_xml(z.read(n))
        except BcfError as exc:
            out["warnings"].append(f"{n}: {exc}")
            continue
        top = root.find("{*}Topic")
        if top is None or not top.get("Guid"):
            out["warnings"].append(f"{n}: no Topic/@Guid, skipped")
            continue
        labels = [l.text.strip() for l in top.findall("{*}Labels/{*}Label") if l.text] or \
                 [l.text.strip() for l in top.findall("{*}Labels") if l.text and l.text.strip()]
        comment_els = top.findall("{*}Comments/{*}Comment") or root.findall("{*}Comment")
        vp_els = top.findall("{*}Viewpoints/{*}ViewPoint") or root.findall("{*}Viewpoints")
        issue = {"guid": top.get("Guid"), "type": top.get("TopicType"), "status": top.get("TopicStatus"),
                 "title": _t(top, "{*}Title") or "", "priority": _t(top, "{*}Priority"), "labels": labels,
                 "created_at": _t(top, "{*}CreationDate"), "author": _t(top, "{*}CreationAuthor"),
                 "modified_at": _t(top, "{*}ModifiedDate"), "due_date": _t(top, "{*}DueDate"),
                 "assigned_to": _t(top, "{*}AssignedTo"), "stage": _t(top, "{*}Stage"), "description": _t(top, "{*}Description"),
                 "comments": [{"guid": c.get("Guid"), "date": _t(c, "{*}Date"), "author": _t(c, "{*}Author"), "text": _t(c, "{*}Comment")}
                              for c in comment_els],
                 "element_guids": [], "camera": None, "snapshot_png": None, "viewpoints": []}
        for hf in root.findall("{*}Header/{*}File") + root.findall("{*}Header/{*}Files/{*}File"):
            out["files"].append({"filename": _t(hf, "{*}Filename"), "ifc_project": hf.get("IfcProject"),
                                 "ifc_spatial": hf.get("IfcSpatialStructureElement")})
        for vp in vp_els:
            f, snap = _t(vp, "{*}Viewpoint"), _t(vp, "{*}Snapshot")
            issue["viewpoints"].append({"guid": vp.get("Guid"), "file": f, "snapshot": snap})
            if f and f"{folder}/{f}" in names:
                try:
                    vi = _safe_xml(z.read(f"{folder}/{f}"))
                except BcfError as exc:
                    out["warnings"].append(f"{folder}/{f}: {exc}")
                    continue
                for c in vi.findall("{*}Components/{*}Selection/{*}Component"):
                    g = c.get("IfcGuid")
                    if g and g not in issue["element_guids"]:
                        issue["element_guids"].append(g)
                issue["camera"] = issue["camera"] or _camera(vi)
            if snap and issue["snapshot_png"] is None and f"{folder}/{snap}" in names:
                issue["snapshot_png"] = z.read(f"{folder}/{snap}")
        out["issues"].append(issue)
    if not out["issues"] and not out["warnings"]:
        out["warnings"].append("archive contains no topics")
    return out


# ---------- BCF 2.1 writer ----------

def _x(el) -> bytes:
    ET.indent(el)
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(el, encoding="utf-8")


def _txt(parent, tag, text):
    if text not in (None, ""):
        ET.SubElement(parent, tag).text = str(text)


def _pt(parent, tag, v):
    e = ET.SubElement(parent, tag)
    for k, c in zip("XYZ", v):
        ET.SubElement(e, k).text = repr(float(c))


def _guid(g) -> str:
    try:
        return str(uuid.UUID(str(g)))
    except ValueError:
        return str(uuid.uuid4())


def _viewpoint21(guid: str, issue: dict) -> bytes:
    root = ET.Element("VisualizationInfo", {"Guid": guid, **_NS})
    comps = ET.SubElement(root, "Components")
    guids = [g for g in issue.get("element_guids") or [] if len(g) == 22]   # 2.1 schema: IfcGuid has length 22
    if guids:
        sel = ET.SubElement(comps, "Selection")
        for g in guids:
            ET.SubElement(sel, "Component", {"IfcGuid": g})
    ET.SubElement(comps, "Visibility", {"DefaultVisibility": "true"})      # required in 2.1
    cam = issue.get("camera")
    if cam and cam.get("type", "perspective") == "perspective":
        pc = ET.SubElement(root, "PerspectiveCamera")
        _pt(pc, "CameraViewPoint", cam["position"])
        _pt(pc, "CameraDirection", cam["direction"])
        _pt(pc, "CameraUpVector", cam.get("up") or (0, 0, 1))
        _txt(pc, "FieldOfView", min(60.0, max(45.0, float(cam.get("fov") or 60.0))))   # 2.1 limits FOV to 45..60
    elif cam:
        oc = ET.SubElement(root, "OrthogonalCamera")
        _pt(oc, "CameraViewPoint", cam["position"])
        _pt(oc, "CameraDirection", cam["direction"])
        _pt(oc, "CameraUpVector", cam.get("up") or (0, 0, 1))
        _txt(oc, "ViewToWorldScale", cam.get("scale") or 10.0)
    return _x(root)


def _markup21(topic_guid: str, vp_guid: str, issue: dict) -> bytes:
    now = datetime.now(timezone.utc).isoformat()
    root = ET.Element("Markup", _NS)
    top = ET.SubElement(root, "Topic", {"Guid": topic_guid, "TopicType": issue.get("type") or "Issue",
                                        "TopicStatus": issue.get("status") or "Open"})
    _txt(top, "Title", issue.get("title") or "Untitled")
    _txt(top, "Priority", issue.get("priority"))
    for l in issue.get("labels") or []:
        _txt(top, "Labels", l)
    _txt(top, "CreationDate", issue.get("created_at") or now)
    _txt(top, "CreationAuthor", issue.get("author") or "fauxel")
    _txt(top, "ModifiedDate", issue.get("modified_at"))
    _txt(top, "DueDate", issue.get("due_date"))
    _txt(top, "AssignedTo", issue.get("assigned_to"))
    _txt(top, "Stage", issue.get("stage"))
    _txt(top, "Description", issue.get("description"))
    for c in issue.get("comments") or []:                                # Comment* are children of Markup in 2.1
        ce = ET.SubElement(root, "Comment", {"Guid": _guid(c.get("guid"))})
        _txt(ce, "Date", c.get("date") or now)
        _txt(ce, "Author", c.get("author") or "fauxel")
        _txt(ce, "Comment", c.get("text") or "")
        ET.SubElement(ce, "Viewpoint", {"Guid": vp_guid})
    vp = ET.SubElement(root, "Viewpoints", {"Guid": vp_guid})            # Viewpoints* too
    _txt(vp, "Viewpoint", "viewpoint.bcfv")
    if issue.get("snapshot_png"):
        _txt(vp, "Snapshot", "snapshot.png")
    return _x(root)


def build_bcf21_zip(project_name: str, issues: list[dict]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        ver = ET.Element("Version", {"VersionId": "2.1", **_NS})
        ET.SubElement(ver, "DetailedVersion").text = "2.1"
        z.writestr("bcf.version", _x(ver))
        pe = ET.Element("ProjectExtension", _NS)
        pr = ET.SubElement(pe, "Project", {"ProjectId": str(uuid.uuid4())})
        _txt(pr, "Name", project_name)
        z.writestr("project.bcfp", _x(pe))
        for issue in issues:
            g, vp = _guid(issue.get("guid")), str(uuid.uuid4())
            z.writestr(f"{g}/markup.bcf", _markup21(g, vp, issue))
            z.writestr(f"{g}/viewpoint.bcfv", _viewpoint21(vp, issue))
            if issue.get("snapshot_png"):
                z.writestr(f"{g}/snapshot.png", issue["snapshot_png"])
    return buf.getvalue()


def build_bcf30_zip(project_name: str, issues: list[dict]) -> bytes:
    """Reuses the API's BCF 3.0 writer."""
    from fauxel_api.qc.bcf import build_bcf_zip
    return build_bcf_zip(project_name, issues)


def convert(data: bytes, to: str = "2.1", project_name: str = "Fauxel") -> bytes:
    issues = parse_bcf(data)["issues"]
    return build_bcf21_zip(project_name, issues) if to == "2.1" else build_bcf30_zip(project_name, issues)


# ---------- mapping to Fauxel deviations ----------

_SEV = {"critical": "critical", "blocker": "critical", "urgent": "critical", "high": "high", "major": "high", "important": "high",
        "normal": "medium", "medium": "medium", "moderate": "medium", "low": "low", "minor": "low", "trivial": "low"}
_CLOSED = {"closed", "resolved", "done", "fixed", "completed", "solved"}


def severity_of(priority: str | None) -> str:
    return _SEV.get((priority or "").strip().lower(), "medium")


def is_closed(status: str | None) -> bool:
    return (status or "").strip().lower() in _CLOSED


def kind_of(issue: dict) -> str:
    text = " ".join([issue.get("type") or "", issue.get("title") or "", *(issue.get("labels") or [])]).lower()
    if re.search(r"pre-?cover|before (insul|cover)|in-?wall", text):
        return "precover"
    if re.search(r"missing|not installed|omitted", text):
        return "missing"
    if re.search(r"\bextra\b|additional|not in model|unmodel", text):
        return "extra"
    return "positional"


def note_of(issue: dict, matched: list[str], unmatched: list[str]) -> str:
    parts = [issue.get("title") or ""]
    if issue.get("description"):
        parts.append(issue["description"])
    parts += [f"{c.get('author') or '?'}: {c.get('text')}" for c in issue.get("comments") or [] if c.get("text")]
    if unmatched:
        parts.append("IfcGuids not found in this project: " + ", ".join(unmatched[:10]))
    if len(matched) > 1:
        parts.append("Other matched elements: " + ", ".join(matched[1:10]))
    parts.append(f"BCF topic {issue['guid']}")
    return "\n".join(p for p in parts if p)[:4000]
