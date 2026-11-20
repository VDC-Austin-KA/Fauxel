# fauxel_aps (M1.5, M1.6 APS side)

Autodesk Platform Services adapter for Fauxel (Forma / ACC endpoints unchanged by the rename). Router: `services/api/fauxel_api/routers/aps.py`
(`router` needs auth; `public_router` = OAuth callback + webhook, register WITHOUT `require_auth`, both under `/api/v1`).

```
pip install -e connectors/aps-forma
FAUXEL_TEST_ADMIN_URL=postgresql://fauxel@localhost:55432/postgres pytest connectors/aps-forma/tests   # 46 tests, mocked HTTP
```

## Env
| Var | Purpose |
|---|---|
| `FAUXEL_APS_CLIENT_ID` / `FAUXEL_APS_CLIENT_SECRET` | APS app (private "Traditional Web App" for 3LO; secret via HTTP Basic only) |
| `FAUXEL_APS_CALLBACK_URL` | must equal the app's registered callback, e.g. `https://host/api/v1/integrations/aps/callback` |
| `FAUXEL_TOKEN_KEY` | Fernet key (`python -c "from fauxel_aps import generate_key; print(generate_key())"`); tokens are stored encrypted in `integration_tokens` |
| `FAUXEL_APS_WEBHOOK_SECRET` | HMAC secret, registered once via `Webhooks.set_secret` (`POST /webhooks/v1/tokens`) |
| `FAUXEL_APS_SCOPES_3LO` / `_2LO` / `_BASE_URL` / `_REGION` | optional overrides |

## Verified against official docs (https://aps.autodesk.com/en/docs/...; OpenAPI specs: github.com/autodesk-platform-services/aps-sdk-openapi)
| Area | Endpoint | Doc / source | Notes |
|---|---|---|---|
| OAuth authorize | `GET /authentication/v2/authorize` | oauth/v2/reference/http/authorize-GET | PKCE S256 only, code valid 5 min |
| OAuth token | `POST /authentication/v2/token` | oauth/v2/reference/http/token-POST | private client: Basic header only (header + body creds = 400); public: `client_id` in body; refresh token rotates |
| Data Management | `/project/v1/hubs`, `/hubs/{h}/projects`, `/projects/{p}/topFolders`, `/folders/{f}/contents`, `/items/{i}/tip`, `/versions/{v}`; `POST /data/v1/projects/{p}/storage` | data/v2 reference (OpenAPI yaml) | JSON:API, `links.next.href` pagination |
| Model Derivative | `GET /modelderivative/v2/designdata/{urn}/metadata`, `.../metadata/{guid}`, `.../metadata/{guid}/properties` | model-derivative/v2 reference (OpenAPI yaml) | urn = url-safe base64 no padding; 202 = not ready, poll |
| Issues | `/construction/issues/v1/projects/{pid}/issues` (GET/POST/PATCH), `/issue-types?include=subtypes`, `/attachments` | acc/v1/reference/http/issues-* (OpenAPI yaml) | pid without `b.`; 3LO `data:write`; title <=100, description <=1000, locationDetails <=250 |
| Evidence upload | `POST /data/v1/projects/{p}/storage` -> `GET /oss/v2/buckets/wip.dm.prod/objects/{o}/signeds3upload` -> `PUT` signed URL (no Authorization) -> `POST .../signeds3upload {uploadKey}` -> `POST /issues/.../attachments` | oss + issues docs | |
| Webhooks | `x-adsk-signature: sha1hash=<hex>` HMAC-SHA1 over raw body; `POST /webhooks/v1/tokens` | webhooks/v1 reference | at-least-once, 7 s response limit => work runs in background, deduped by `integration_webhook_events.event_key` |

## Doc page + web search only (no official OpenAPI yaml; payload shapes taken from doc text)
Locations `/construction/locations/v2/projects/{pid}/trees/default/nodes` (limit/offset), Assets `/construction/assets/v2/projects/{pid}/assets` (`cursorState`), `assets:batch-patch` (body keyed by asset id; bim360 path fallback implemented), AEC Data Model `POST https://developer.api.autodesk.com/aec/graphql` (3LO only, RSQL filters).

## Not verified / known limits (flag before relying on them)
- `extraction.finished` payload URN casing, AEC DM `properties{results{name value}}` selection and pagination input names, Locations node-type names, Model Derivative unit strings: written to the documented shapes, tested only against fixtures. Needs one live run.
- Issue pushpins (`linkedDocuments`) are undocumented: only `locationId` + `locationDetails` are sent (`pushpin_experimental` off).
- Photos API is read-only: evidence is attached through the Issues attachment flow.
- Model Derivative / AEC DM return no geometry: `import-model` needs caller-supplied `bboxes` or `placeholder_box`. Follow-up: IFC derivative route.
- bim-export `source` enum is `revit|navisworks`; the stored model is relabelled `aps`. `progress.synced` enum has the same limit, so it is not emitted for APS.
