import base64
import hashlib
import json
import re
from urllib.parse import parse_qs, urlparse

import httpx
import jsonschema
import pytest
import respx

from fauxel_aps import (ApsAuthError, ApsConfig, ApsError, ApsRateLimited, ApsSession, AecDataModel, Issues, Locations, ModelDerivative,
                        TokenManager, Vault, auth, build_bim_export, classify, deviation_to_issue, generate_key, parse_extraction,
                        sign, upload_issue_attachment, urn_b64, urn_from_b64, verify_signature, with_b, strip_b)
from fauxel_aps.assets import Assets, match_assets, status_id_for
from fauxel_aps.bimexport import parse_length_m, trade_for_category
from fauxel_aps.datamanagement import DataManagement
from fauxel_aps.issues import clip, resolve_subtype
from fauxel_aps.webhooks import event_key

import fixtures as fx

BASE = "https://developer.api.autodesk.com"
CFG = ApsConfig(client_id="cid", client_secret="sec", callback_url="http://localhost:8000/api/v1/integrations/aps/callback")


def session(sleeper, token="tok", **kw):
    return ApsSession(lambda: token, sleep=sleeper, **kw)


# ---------------- OAuth ----------------

def test_pkce_pair_is_s256():
    v, c = auth.new_pkce()
    assert 43 <= len(v) <= 128
    assert c == base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()


def test_authorize_url_matches_documented_parameters():
    url = auth.authorize_url(CFG, "st4te", "chal")
    u = urlparse(url)
    assert f"{u.scheme}://{u.netloc}{u.path}" == f"{BASE}/authentication/v2/authorize"
    q = {k: v[0] for k, v in parse_qs(u.query).items()}
    assert q["response_type"] == "code" and q["client_id"] == "cid" and q["state"] == "st4te"
    assert q["redirect_uri"] == CFG.callback_url and q["code_challenge"] == "chal" and q["code_challenge_method"] == "S256"
    assert "data:read" in q["scope"]


def test_authorize_url_needs_callback():
    with pytest.raises(ApsAuthError):
        auth.authorize_url(ApsConfig(client_id="x"), "s")


@respx.mock
def test_client_credentials_uses_basic_header_only():
    route = respx.post(f"{BASE}/authentication/v2/token").mock(return_value=httpx.Response(200, json=fx.TOKEN_2LO))
    assert auth.client_credentials(CFG)["access_token"] == "2lo-token"
    req = route.calls.last.request
    assert req.headers["Authorization"] == "Basic " + base64.b64encode(b"cid:sec").decode()
    body = parse_qs(req.content.decode())
    assert body["grant_type"] == ["client_credentials"] and "client_id" not in body and "client_secret" not in body  # both => 400 per docs
    assert req.headers["content-type"] == "application/x-www-form-urlencoded"


@respx.mock
def test_public_client_code_exchange_sends_verifier_and_client_id_no_header():
    route = respx.post(f"{BASE}/authentication/v2/token").mock(return_value=httpx.Response(200, json=fx.TOKEN_3LO))
    cfg = ApsConfig(client_id="cid", callback_url="http://cb")
    t = auth.exchange_code(cfg, "the-code", "the-verifier")
    req = route.calls.last.request
    assert "Authorization" not in req.headers
    b = parse_qs(req.content.decode())
    assert b["grant_type"] == ["authorization_code"] and b["code"] == ["the-code"] and b["code_verifier"] == ["the-verifier"]
    assert b["client_id"] == ["cid"] and b["redirect_uri"] == ["http://cb"]
    assert t["refresh_token"] == "3lo-refresh"


@respx.mock
def test_refresh_and_errors():
    respx.post(f"{BASE}/authentication/v2/token").mock(side_effect=[httpx.Response(200, json=fx.TOKEN_3LO_REFRESHED),
                                                                  httpx.Response(400, json={"error": "invalid_grant"})])
    assert auth.refresh(CFG, "old")["refresh_token"] == "3lo-refresh-2"
    with pytest.raises(ApsAuthError):
        auth.refresh(CFG, "bad")


def test_two_legged_needs_secret():
    with pytest.raises(ApsAuthError):
        auth.client_credentials(ApsConfig(client_id="x"))


# ---------------- tokens ----------------

def test_vault_roundtrip_and_wrong_key():
    v = Vault(generate_key())
    enc = v.enc("secret")
    assert enc != "secret" and v.dec(enc) == "secret"
    with pytest.raises(ApsAuthError):
        Vault(generate_key()).dec(enc)
    with pytest.raises(ApsAuthError):
        Vault("")
    with pytest.raises(ApsAuthError):
        Vault("not-a-fernet-key")


@respx.mock
def test_two_legged_token_cached_until_skew():
    route = respx.post(f"{BASE}/authentication/v2/token").mock(return_value=httpx.Response(200, json=fx.TOKEN_2LO))
    now = [1000.0]
    tm = TokenManager(CFG, clock=lambda: now[0])
    assert tm.two_legged() == tm.two_legged() == "2lo-token"
    assert route.call_count == 1
    now[0] += 3599 - 30            # inside the 90 s skew window
    tm.two_legged()
    assert route.call_count == 2


# ---------------- HTTP: rate limit, retry, pagination ----------------

@respx.mock
def test_429_retry_after_is_honored(sleeper):
    respx.get(f"{BASE}/x").mock(side_effect=[httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200, json={"ok": 1})])
    assert session(sleeper).json("GET", "/x") == {"ok": 1}
    assert sleeper.calls == [7.0]


@respx.mock
def test_5xx_retry_then_persistent_429_raises(sleeper):
    respx.get(f"{BASE}/a").mock(side_effect=[httpx.Response(503), httpx.Response(200, json={})])
    session(sleeper).json("GET", "/a")
    assert len(sleeper.calls) == 1
    respx.get(f"{BASE}/b").mock(return_value=httpx.Response(429, headers={"Retry-After": "1"}))
    with pytest.raises(ApsRateLimited):
        session(sleeper, max_retries=2).json("GET", "/b")


@respx.mock
def test_error_includes_status_and_body(sleeper):
    respx.get(f"{BASE}/e").mock(return_value=httpx.Response(403, json={"reason": "nope"}))
    with pytest.raises(ApsError) as e:
        session(sleeper).json("GET", "/e")
    assert e.value.status == 403 and e.value.body == {"reason": "nope"}


@respx.mock
def test_offset_cursor_and_jsonapi_pagination(sleeper):
    s = session(sleeper)
    respx.get(f"{BASE}/off").mock(side_effect=[httpx.Response(200, json={"pagination": {"totalResults": 3}, "results": [{"i": 1}, {"i": 2}]}),
                                               httpx.Response(200, json={"pagination": {"totalResults": 3}, "results": [{"i": 3}]})])
    assert [r["i"] for r in s.paginate_offset("/off", limit=2)] == [1, 2, 3]
    respx.get(f"{BASE}/cur").mock(side_effect=[
        httpx.Response(200, json=fx.assets_page("c2", [{"id": "a"}])), httpx.Response(200, json=fx.assets_page(None, [{"id": "b"}]))])
    assert [r["id"] for r in s.paginate_cursor("/cur")] == ["a", "b"]
    assert respx.calls.last.request.url.params["cursorState"] == "c2"
    nxt = f"{BASE}/data/v1/projects/b.p/folders/f/contents?page%5Bnumber%5D=1"
    respx.get(f"{BASE}/jsonapi").mock(return_value=httpx.Response(200, json=fx.folder_contents(1, nxt)))
    respx.get(nxt).mock(return_value=httpx.Response(200, json=fx.folder_contents(2)))
    assert len(list(s.paginate_jsonapi("/jsonapi"))) == 3


# ---------------- Data Management ----------------

def test_urn_helpers_roundtrip_and_prefix():
    b = urn_b64(fx.VERSION_ID)
    assert "=" not in b and "+" not in b and "/" not in b
    assert urn_from_b64(b) == fx.VERSION_ID
    assert strip_b("b.abc") == "abc" and with_b("abc") == "b.abc" == with_b("b.abc")


@respx.mock
def test_data_management_pick_models(sleeper):
    dm = DataManagement(session(sleeper))
    respx.get(f"{BASE}/project/v1/hubs").mock(return_value=httpx.Response(200, json=fx.HUBS))
    assert dm.hubs()[0]["id"] == "b.hub-1"
    respx.get(f"{BASE}/project/v1/hubs/b.hub-1/projects/b.p1/topFolders").mock(return_value=httpx.Response(200, json=fx.TOP_FOLDERS))
    assert dm.top_folders("b.hub-1", "p1")[0]["attributes"]["name"] == "Project Files"
    top = "urn:adsk.wipprod:fs.folder:co.top"
    respx.get(f"{BASE}/data/v1/projects/b.p1/folders/{top}/contents").mock(return_value=httpx.Response(200, json=fx.folder_contents(1)))
    respx.get(f"{BASE}/data/v1/projects/b.p1/folders/urn:adsk.wipprod:fs.folder:co.sub/contents").mock(
        return_value=httpx.Response(200, json=fx.folder_contents(2)))
    assert [m["name"] for m in dm.models("p1", top)] == ["tower.rvt"]


# ---------------- Model Derivative ----------------

@respx.mock
def test_model_derivative_polls_202_and_picks_master_view(sleeper):
    urn = urn_b64(fx.VERSION_ID)
    md = ModelDerivative(session(sleeper), sleep=sleeper, poll_seconds=1)
    respx.get(f"{BASE}/modelderivative/v2/designdata/{urn}/metadata").mock(
        side_effect=[httpx.Response(202, json={"result": "created"}), httpx.Response(200, json=fx.MD_VIEWS)])
    assert md.pick_3d_view(urn)["guid"] == "view-3d-guid"
    assert sleeper.calls == [1]
    respx.get(f"{BASE}/modelderivative/v2/designdata/{urn}/metadata/view-3d-guid").mock(return_value=httpx.Response(200, json=fx.MD_TREE))
    assert md.object_tree(urn, "view-3d-guid")[0]["objects"][0]["objectid"] == 2
    respx.get(f"{BASE}/modelderivative/v2/designdata/{urn}/metadata/view-3d-guid/properties").mock(return_value=httpx.Response(200, json=fx.MD_PROPS))
    assert len(md.properties(urn, "view-3d-guid")) == 6
    manifest = {"type": "manifest", "status": "success", "derivatives": [{"outputType": "svf2", "status": "success"}]}
    respx.get(f"{BASE}/modelderivative/v2/designdata/{urn}/manifest").mock(return_value=httpx.Response(200, json=manifest))
    assert md.is_translated(urn)


@respx.mock
def test_model_derivative_gives_up_after_max_polls(sleeper):
    respx.get(f"{BASE}/modelderivative/v2/designdata/u/metadata").mock(return_value=httpx.Response(202, json={}))
    with pytest.raises(Exception, match="202"):
        ModelDerivative(session(sleeper), sleep=sleeper, max_polls=3).model_views("u")


# ---------------- properties -> bim-export ----------------

def test_length_parsing():
    assert parse_length_m("3000 mm") == pytest.approx(3.0)
    assert parse_length_m("10 ft") == pytest.approx(3.048)
    assert parse_length_m(2) == 2.0 and parse_length_m("abc") is None
    assert trade_for_category("Ducts") == "hvac" and trade_for_category("Structural Columns") == "structural"
    assert trade_for_category("Cable Trays") == "electrical" and trade_for_category("Pipes") == "plumbing_fp"


def test_bim_export_validates_against_contract_schema(tmp_path):
    schema = json.loads((__import__("pathlib").Path(__file__).resolve().parents[3] / "contracts" / "bim-export.schema.json").read_text())
    payload, rep = build_bim_export("tower", fx.MD_PROPS["data"]["collection"], bboxes=fx.BBOXES)
    jsonschema.Draft202012Validator(schema).validate(payload)
    assert rep["elements"] == 2 and rep["skipped_no_geometry"] == 1 and rep["skipped_non_physical"] == 3
    assert [l["name"] for l in payload["levels"]] == ["Level 1", "Level 2"] and payload["levels"][1]["elevation_m"] == 4.0
    wall = next(e for e in payload["elements"] if e["category"] == "Walls")
    assert wall["revit_unique_id"] == "wall-1-uid-0001" and wall["ifc_global_id"] == "2O2Fr$t4X7Zf8NOew3FLOH"
    assert wall["level"] == "Level 1" and wall["quantity"] == 24.5 and wall["quantity_unit"] == "m2" and wall["trade"] == "arch_interior"
    duct = next(e for e in payload["elements"] if e["category"] == "Ducts")
    assert duct["quantity"] == 3.0 and duct["quantity_unit"] == "m" and duct["trade"] == "hvac" and duct["level"] == "Level 2"


def test_placeholder_box_flagged():
    payload, rep = build_bim_export("t", fx.MD_PROPS["data"]["collection"], placeholder_box=True)
    assert rep["placeholder_boxes"] == 3 and len(payload["elements"]) == 3


# ---------------- AEC Data Model ----------------

@respx.mock
def test_aec_dm_filter_cursor_pagination_and_error(sleeper):
    def page(cursor, ids):
        return {"data": {"elementsByProject": {"pagination": {"cursor": cursor, "pageSize": 2},
                                               "results": [{"id": i, "name": f"Wall {i}", "properties": {"results": [
                                                   {"name": "Category", "value": "Walls"}, {"name": "External ID", "value": f"uid-{i}"}]}} for i in ids]}}}
    route = respx.post(f"{BASE}/aec/graphql").mock(side_effect=[httpx.Response(200, json=page("next-cur", ["a", "b"])),
                                                              httpx.Response(200, json=page(None, ["c"]))])
    aec = AecDataModel(ApsSession(lambda: "t", sleep=sleeper, region="EMEA"))
    els = aec.elements("proj-1", "Walls")
    assert [e["id"] for e in els] == ["a", "b", "c"]
    first = json.loads(route.calls[0].request.content)
    assert first["variables"]["filter"]["query"] == "property.name.category==Walls and 'property.name.Element Context'==Instance"
    assert json.loads(route.calls[1].request.content)["variables"]["pagination"]["cursor"] == "next-cur"
    assert route.calls[0].request.headers["Region"] == "EMEA"
    coll = aec.to_collection(els)
    assert coll[0]["externalId"] == "uid-a"
    respx.post(f"{BASE}/aec/graphql").mock(return_value=httpx.Response(200, json={"errors": [{"message": "boom"}]}))
    with pytest.raises(ApsError, match="boom"):
        aec.hubs()


# ---------------- Issues ----------------

DEV = {"id": "d0d0d0d0-0000-4000-8000-000000000001", "kind": "positional", "severity": "high", "status": "open", "note": "Wall 40mm off",
       "dx": 0.04, "dy": 0.0, "dz": -0.01, "frame_id": None}
ELEM = {"element_uid": "e1", "name": "Basic Wall", "ifc_global_id": "2O2Fr$t4X7Zf8NOew3FLOH", "revit_unique_id": None, "cx": 1.0, "cy": 2.0, "cz": 3.0}


@respx.mock
def test_issue_types_subtypes_and_subtype_resolution(sleeper):
    route = respx.get(f"{BASE}/construction/issues/v1/projects/p-uuid/issue-types").mock(return_value=httpx.Response(200, json=fx.ISSUE_TYPES))
    iss = Issues(session(sleeper), "b.p-uuid")
    subs = iss.subtypes()
    assert route.calls[0].request.url.params["include"] == "subtypes"        # b. prefix stripped, subtypes requested
    assert len(subs) == 3
    assert resolve_subtype(DEV, {}, subs) == "sub-fauxel"                     # title match
    assert resolve_subtype(DEV, {"issue_subtype_map": {"high": "sub-safety"}}, subs) == "sub-safety"
    assert resolve_subtype(DEV, {"issue_subtype_map": {"positional": "sub-other", "high": "sub-safety"}}, subs) == "sub-other"
    assert resolve_subtype(DEV, {"default_subtype_id": "dflt"}, subs) == "dflt"
    with pytest.raises(ApsError):
        resolve_subtype(DEV, {}, [])


def test_deviation_to_issue_body_respects_documented_limits():
    long = {**DEV, "note": "x" * 3000}
    body = deviation_to_issue(long, {**ELEM, "name": "N" * 300}, {}, [{"id": "s", "title": "Fauxel", "isActive": True}], evidence_url="https://f/x", location_id="loc-1")
    assert len(body["title"]) <= 100 and len(body["description"]) <= 1000 and len(body["locationDetails"]) <= 250
    assert body["issueSubtypeId"] == "s" and body["status"] == "open" and body["locationId"] == "loc-1"
    assert DEV["id"] in deviation_to_issue(DEV, ELEM, {}, [{"id": "s", "title": "Fauxel"}])["description"]   # idempotency marker
    assert clip("abcdef", 4) == "abc…"


@respx.mock
def test_create_issue_posts_to_documented_path(sleeper):
    route = respx.post(f"{BASE}/construction/issues/v1/projects/p-uuid/issues").mock(return_value=httpx.Response(201, json=fx.issue()))
    out = Issues(session(sleeper, "3lo"), "p-uuid", region="EMEA").create({"title": "t", "issueSubtypeId": "s", "status": "open"})
    req = route.calls[0].request
    assert out["displayId"] == 7 and req.headers["Authorization"] == "Bearer 3lo" and req.headers["x-ads-region"] == "EMEA"


@respx.mock
def test_attachment_flow_order_and_payload(sleeper):
    order = []
    respx.post(f"{BASE}/data/v1/projects/b.p-uuid/storage").mock(side_effect=lambda r: (order.append("storage"), httpx.Response(201, json=fx.STORAGE))[1])
    obj = "830b7ac3-dc75-4e36-aa32-7a1cff7599a1.jpg"
    respx.get(f"{BASE}/oss/v2/buckets/wip.dm.prod/objects/{obj}/signeds3upload").mock(side_effect=lambda r: (order.append("signed"), httpx.Response(200, json=fx.SIGNED_UPLOAD))[1])
    put = respx.put("https://s3.example.com/signed-url-uploads/abc").mock(side_effect=lambda r: (order.append("put"), httpx.Response(200, headers={"ETag": '"abc123"'}))[1])
    respx.post(f"{BASE}/oss/v2/buckets/wip.dm.prod/objects/{obj}/signeds3upload").mock(side_effect=lambda r: (order.append("complete"), httpx.Response(200, json={"bucketKey": "wip.dm.prod"}))[1])
    link = respx.post(f"{BASE}/construction/issues/v1/projects/p-uuid/attachments").mock(side_effect=lambda r: (order.append("link"), httpx.Response(200, json={"attachments": []}))[1])
    s = session(sleeper)
    att = upload_issue_attachment(s, Issues(s, "p-uuid"), "p-uuid", "urn:adsk.wipprod:fs.folder:co.top", "iss-1", "frame.jpg", b"JPEGDATA")
    assert order == ["storage", "signed", "put", "complete", "link"]
    assert "Authorization" not in put.calls[0].request.headers            # signed S3 URL must not carry the bearer token
    sent = json.loads(link.calls[0].request.content)
    assert sent["domainEntityId"] == "iss-1"
    a = sent["attachments"][0]
    assert a == att and a["attachmentType"] == "issue-attachment" and a["attachmentId"] == "830b7ac3-dc75-4e36-aa32-7a1cff7599a1"
    assert a["fileName"] == obj and a["storageUrn"] == fx.STORAGE["data"]["id"] and a["displayName"] == "frame.jpg"
    done = json.loads(respx.calls[3].request.content)
    assert done == {"uploadKey": "upload-key-1", "size": 8, "eTags": ["abc123"]}


# ---------------- Locations ----------------

@respx.mock
def test_locations_pagination_and_lbs_mapping(sleeper):
    path = f"{BASE}/construction/locations/v2/projects/p-uuid/trees/default/nodes"
    first, second = dict(fx.LBS), dict(fx.LBS)
    first = {**fx.LBS, "pagination": {"totalResults": 6}, "results": fx.LBS["results"][:4]}
    second = {**fx.LBS, "pagination": {"totalResults": 6}, "results": fx.LBS["results"][4:]}
    respx.get(path).mock(side_effect=[httpx.Response(200, json=first), httpx.Response(200, json=second)])
    nodes = Locations(session(sleeper), "b.p-uuid").nodes()
    assert len(nodes) == 6 and respx.calls[1].request.url.params["offset"] == "4"
    tree = classify(nodes)
    assert [b["name"] for b in tree["buildings"]] == ["Tower A"]
    assert [(l["name"], l["building_id"]) for l in tree["levels"]] == [("Level 1", "b1"), ("Level 2", "b1")]
    assert [(z["name"], z["level_id"]) for z in tree["zones"]] == [("Zone East", "l1"), ("Zone West", "l1")]


def test_classify_honours_explicit_types():
    nodes = [{"id": "r", "parentId": None, "type": "Root", "name": "P"}, {"id": "f1", "parentId": "r", "type": "Level", "name": "Floor 1"},
             {"id": "rm", "parentId": "f1", "type": "Room", "name": "Lobby"}]
    t = classify(nodes)
    assert t["buildings"] == [] and t["levels"][0]["name"] == "Floor 1" and t["zones"][0]["level_id"] == "f1"


# ---------------- Assets ----------------

@respx.mock
def test_assets_listing_status_mapping_and_batch_patch(sleeper):
    base = f"{BASE}/construction/assets/v2/projects/p-uuid/assets"
    respx.get(base).mock(side_effect=[httpx.Response(200, json=fx.assets_page("c2", [{"id": "a1", "clientAssetId": "2O2Fr$t4X7Zf8NOew3FLOH", "statusId": "st-ns"}])),
                                     httpx.Response(200, json=fx.assets_page(None, [{"id": "a2", "barcode": "duct-1-uid-0002"}]))])
    respx.get(f"{BASE}/construction/assets/v1/projects/p-uuid/status-step-sets").mock(return_value=httpx.Response(200, json=fx.STATUS_SETS))
    patch = respx.patch(f"{BASE}/construction/assets/v2/projects/p-uuid/assets:batch-patch").mock(return_value=httpx.Response(200, json={}))
    api = Assets(session(sleeper), "b.p-uuid")
    assets = list(api.list_assets())
    assert [a["id"] for a in assets] == ["a1", "a2"]
    links = match_assets(assets, [{"element_uid": "E1", "ifc_global_id": "2O2Fr$t4X7Zf8NOew3FLOH"}, {"element_uid": "E2", "revit_unique_id": "duct-1-uid-0002"}])
    assert links == {"a1": "E1", "a2": "E2"}
    sts = api.statuses()
    assert status_id_for("INSTALLED", sts) == "st-in" and status_id_for("DEVIATION_FLAGGED", sts) == "st-dev"
    assert status_id_for("NOT_STARTED", sts, {"NOT_STARTED": "custom"}) == "custom"
    api.batch_patch({"a1": {"statusId": "st-in"}})
    assert json.loads(patch.calls[0].request.content) == {"a1": {"statusId": "st-in"}}


@respx.mock
def test_assets_v2_falls_back_to_bim360_path(sleeper):
    respx.get(f"{BASE}/construction/assets/v2/projects/p/assets").mock(return_value=httpx.Response(404, json={}))
    respx.get(f"{BASE}/bim360/assets/v2/projects/p/assets").mock(return_value=httpx.Response(200, json=fx.assets_page(None, [{"id": "z"}])))
    assert [a["id"] for a in Assets(session(sleeper), "p").list_assets()] == ["z"]


# ---------------- Webhooks ----------------

def test_signature_matches_documented_format():
    body = b'{"a":1}'
    sig = sign("s3cret", body)
    assert sig.startswith("sha1hash=") and len(sig) == len("sha1hash=") + 40
    import hmac, hashlib as h
    assert sig == "sha1hash=" + hmac.new(b"s3cret", body, h.sha1).hexdigest()
    assert verify_signature("s3cret", body, sig)
    assert not verify_signature("s3cret", body + b" ", sig) and not verify_signature("other", body, sig)
    assert not verify_signature("s3cret", body, None) and not verify_signature("", body, sig)


def test_parse_extraction_and_dedupe_key():
    ev = fx.webhook_event("dXJuOnRlc3Q")
    info = parse_extraction(ev)
    assert info == {"event": "extraction.finished", "status": "success", "urn": "dXJuOnRlc3Q", "resource_urn": ev["resourceUrn"],
                    "workflow": "wf-1", "hook_id": "hook-1"}
    raw = json.dumps(ev).encode()
    assert event_key(ev, raw) == event_key(ev, raw) != event_key(ev, raw + b" ")


def test_config_treats_blank_env_as_unset(monkeypatch):
    # docker compose passes `${FAUXEL_APS_BASE_URL:-}` as an empty string; that must not wipe the defaults
    from fauxel_aps import config as c
    for k in ("FAUXEL_APS_BASE_URL", "FAUXEL_APS_SCOPES_3LO", "FAUXEL_APS_SCOPES_2LO", "FAUXEL_APS_REGION"):
        monkeypatch.setenv(k, "")
    cfg = c.ApsConfig.from_env()
    assert cfg.base_url == c.BASE_URL and cfg.base_url.startswith("https://")
    assert cfg.scopes_3lo == c.DEFAULT_SCOPES_3LO and cfg.scopes_2lo == c.DEFAULT_SCOPES_2LO and cfg.region is None
