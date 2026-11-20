"""JSON shaped like the documented APS responses (field names from the reference pages / OpenAPI specs)."""

TOKEN_2LO = {"access_token": "2lo-token", "token_type": "Bearer", "expires_in": 3599}
TOKEN_3LO = {"access_token": "3lo-access", "token_type": "Bearer", "expires_in": 3599, "refresh_token": "3lo-refresh"}
TOKEN_3LO_REFRESHED = {"access_token": "3lo-access-2", "token_type": "Bearer", "expires_in": 3599, "refresh_token": "3lo-refresh-2"}

HUBS = {"jsonapi": {"version": "1.0"}, "links": {"self": {"href": "https://developer.api.autodesk.com/project/v1/hubs"}},
        "data": [{"type": "hubs", "id": "b.hub-1", "attributes": {"name": "Acme", "extension": {"type": "hubs:autodesk.bim360:Account"}}}]}

TOP_FOLDERS = {"data": [{"type": "folders", "id": "urn:adsk.wipprod:fs.folder:co.top", "attributes": {"name": "Project Files"}}]}


def folder_contents(page: int = 1, nxt: str | None = None):
    items = {1: [{"type": "folders", "id": "urn:adsk.wipprod:fs.folder:co.sub", "attributes": {"displayName": "Models"}},
                 {"type": "items", "id": "urn:adsk.wipprod:dm.lineage:abc", "attributes": {"displayName": "tower.rvt"}}],
             2: [{"type": "items", "id": "urn:adsk.wipprod:dm.lineage:def", "attributes": {"displayName": "spec.pdf"}}]}[page]
    body = {"jsonapi": {"version": "1.0"}, "data": items, "links": {}}
    if nxt:
        body["links"]["next"] = {"href": nxt}
    return body


VERSION_ID = "urn:adsk.wipprod:fs.file:vf.AbCdEf?version=3"

MD_VIEWS = {"data": {"type": "metadata", "metadata": [
    {"name": "{3D}", "role": "3d", "guid": "view-3d-guid", "isMasterView": True},
    {"name": "Sheet A101", "role": "2d", "guid": "view-2d-guid"}]}}

MD_TREE = {"data": {"type": "objects", "objects": [{"objectid": 1, "name": "tower.rvt", "objects": [
    {"objectid": 2, "name": "Basic Wall [100]"}, {"objectid": 3, "name": "Rectangular Duct [200]"}]}]}}

MD_PROPS = {"data": {"type": "properties", "collection": [
    {"objectid": 10, "name": "Level 1 [11]", "externalId": "lvl-1-uid", "properties": {
        "__category__": {"Category": "Revit Levels"}, "Constraints": {"Elevation": "0.0 m"}}},
    {"objectid": 11, "name": "Level 2 [12]", "externalId": "lvl-2-uid", "properties": {
        "__category__": {"Category": "Revit Levels"}, "Constraints": {"Elevation": "4.0 m"}}},
    {"objectid": 20, "name": "Basic Wall [100]", "externalId": "wall-1-uid-0001", "properties": {
        "__category__": {"Category": "Revit Walls"}, "Constraints": {"Base Constraint": "Level 1 [11]"},
        "Dimensions": {"Area": "24.5 m^2"}, "Identity Data": {"Type Name": "Generic - 200mm", "IfcGUID": "2O2Fr$t4X7Zf8NOew3FLOH"}}},
    {"objectid": 21, "name": "Rectangular Duct [200]", "externalId": "duct-1-uid-0002", "properties": {
        "__category__": {"Category": "Revit Ducts"}, "Constraints": {"Reference Level": "Level 2 [12]"},
        "Dimensions": {"Length": "3000 mm"}, "Identity Data": {"Type Name": "Mitered Elbows"}}},
    {"objectid": 22, "name": "Wall without geometry [300]", "externalId": "wall-2-uid-0003", "properties": {
        "__category__": {"Category": "Revit Walls"}, "Constraints": {"Base Constraint": "Level 1 [11]"}}},
    {"objectid": 23, "name": "Sheet", "externalId": "sheet-uid", "properties": {"__category__": {"Category": "Revit Sheets"}}}]}}

BBOXES = {"wall-1-uid-0001": ([0, 0, 0], [5, 0.2, 3]), "duct-1-uid-0002": ([1, 1, 3.2], [4, 1.4, 3.6])}

ISSUE_TYPES = {"pagination": {"limit": 100, "offset": 0, "totalResults": 2}, "results": [
    {"id": "type-1", "title": "Quality", "isActive": True, "subtypes": [
        {"id": "sub-fauxel", "issueTypeId": "type-1", "title": "Fauxel Deviation", "isActive": True, "code": "FXL"},
        {"id": "sub-other", "issueTypeId": "type-1", "title": "Other", "isActive": True, "code": "OTH"}]},
    {"id": "type-2", "title": "Safety", "isActive": True, "subtypes": [
        {"id": "sub-safety", "issueTypeId": "type-2", "title": "Hazard", "isActive": True, "code": "HAZ"}]}]}


def issue(issue_id="11111111-1111-4111-8111-111111111111", display=7, status="open", **kw):
    return {"id": issue_id, "displayId": display, "title": "t", "status": status, "issueSubtypeId": "sub-fauxel",
            "linkedDocuments": [], **kw}


STORAGE = {"jsonapi": {"version": "1.0"}, "links": {"self": {"href": "/oss/v2/buckets/wip.dm.prod/objects/830b7ac3-dc75-4e36-aa32-7a1cff7599a1.jpg"}},
           "data": {"type": "objects", "id": "urn:adsk.objects:os.object:wip.dm.prod/830b7ac3-dc75-4e36-aa32-7a1cff7599a1.jpg"}}
SIGNED_UPLOAD = {"uploadKey": "upload-key-1", "uploadExpiration": "2026-11-21T00:00:00Z", "urlExpiration": "2026-11-20T03:00:00Z",
                 "urls": ["https://s3.example.com/signed-url-uploads/abc?X-Amz-Signature=1"]}

LBS = {"pagination": {"limit": 100, "offset": 0, "totalResults": 6}, "results": [
    {"id": "root", "parentId": None, "type": "Root", "name": "Project", "description": None, "barcode": None, "order": 0, "areaDefined": False},
    {"id": "b1", "parentId": "root", "type": "Area", "name": "Tower A", "order": 0},
    {"id": "l1", "parentId": "b1", "type": "Area", "name": "Level 1", "order": 0},
    {"id": "l2", "parentId": "b1", "type": "Area", "name": "Level 2", "order": 1},
    {"id": "z1", "parentId": "l1", "type": "Area", "name": "Zone East", "order": 0},
    {"id": "z2", "parentId": "l1", "type": "Area", "name": "Zone West", "order": 1}]}

STATUS_SETS = {"pagination": {"limit": 200, "offset": 0, "totalResults": 1}, "results": [
    {"id": "set-1", "name": "Default", "statuses": [
        {"id": "st-ns", "label": "Not Started", "isActive": True},
        {"id": "st-ip", "label": "In Progress", "isActive": True},
        {"id": "st-in", "label": "Installed", "isActive": True},
        {"id": "st-done", "label": "Complete", "isActive": True},
        {"id": "st-dev", "label": "Deviation", "isActive": True}]}]}


def assets_page(cursor: str | None, items: list[dict]):
    body = {"pagination": {"limit": 200}, "results": items}
    if cursor:
        body["pagination"].update(cursorState=cursor, nextUrl="https://developer.api.autodesk.com/construction/assets/v2/projects/p/assets?cursorState=" + cursor)
    return body


def webhook_event(urn: str, status: str = "success", event: str = "extraction.finished"):
    return {"version": "1.0", "resourceUrn": "urn:adsk.wipprod:fs.file:vf.AbCdEf?version=3",
            "hook": {"system": "derivative", "event": event, "hookId": "hook-1", "tenant": "t", "callbackUrl": "https://x/cb",
                     "createdBy": "u", "createdDate": "2026-11-20T00:00:00Z", "scope": {"workflow": "wf-1"}},
            "payload": {"Payload": {"URN": urn, "status": status}}}
