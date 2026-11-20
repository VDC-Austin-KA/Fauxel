# Fauxel Platform Contract (v1)

Single source of truth for every component built in the first full-platform pass. Components code against this file, not against each other's internals.

## Deployment shape

| Piece | Runs on | Notes |
|---|---|---|
| Web portal (`apps/web-portal`) | Cloudflare Pages at `fauxel.<domain>` | Static React build. Calls `/api/v1/*` on the same origin |
| API gateway (`infra/cloudflare/worker`) | Cloudflare Worker on `fauxel.<domain>/api/*` | Proxies to the local API through Cloudflare Tunnel. Cloudflare Access protects the whole hostname |
| Object storage | Cloudflare R2 bucket `fauxel-media` (prod) / MinIO (local dev) | S3 API either way. Panoramas, IFC files, evidence crops |
| API + workers (`services/api`) | KA's Windows PC, Docker Desktop | FastAPI. GPU optional (NVIDIA via CUDA). Exposed only through `cloudflared` |
| Postgres 16 + PostGIS + pgvector | KA's PC, Docker | Never public. Volume-backed, nightly `pg_dump` to R2 |
| Redpanda | KA's PC, Docker | Event bus (Kafka API). The API also writes an `outbox_events` table, and a relay publishes it |
| Revit / Navisworks plugins | KA's PC, inside Revit 2025–2027 / Navisworks 2025–2027 | Talk to the API with an API key |

## Conventions

- Units: meters, project frame +X East, +Y North, +Z Up. Revit feet only inside the Revit plugin.
- IDs: UUID v4 strings. `element_uid` is Fauxel's canonical element key (UUID). Revit `UniqueId`, IFC `GlobalId` and Navisworks `ItemGuid` are mapped columns.
- Timestamps: ISO 8601 UTC.
- Installation state enum `install_state`: `NOT_STARTED`, `FRAMED_OR_ROUGHED`, `INSTALLED`, `INSULATED_OR_FINISHED`, `DEVIATION_FLAGGED`.
- Trades: `structural`, `arch_interior`, `hvac`, `electrical`, `plumbing_fp`, `other`.
- Status colors (portal and plugins): installed or finished `#22c55e`, in progress (`FRAMED_OR_ROUGHED`, or percent between 1 and 99) `#eab308`, deviation `#ef4444`, not started `#9ca3af`.
- Auth: plugins and scripts send `X-Fauxel-Key: <key>`. Behind Cloudflare Access, the Worker forwards `Cf-Access-Authenticated-User-Email`, which the API trusts only when `FAUXEL_TRUST_ACCESS_HEADER=1`. Local dev: `FAUXEL_AUTH=off`.

## Database (Postgres 16 + PostGIS + pgvector), `db/migrations/*.sql`

`projects, buildings, levels(elevation_m), zones(geom polygon Z, level_id)`, `bim_models(source: ifc|revit|navisworks|speckle|aps, file_key, version)`, `bim_elements(element_uid PK, model_id, revit_unique_id, ifc_global_id, navis_item_guid, aps_id, name, category, ifc_class, family_type, trade, level_id, zone_id, aabb_min/aabb_max float8[3], centroid geometry(PointZ), mesh_key, material, expected_qty, qty_unit)`, `element_status(element_uid PK, state, percent_complete, confidence, last_verified, evidence_frame_id, rules_version)`, `element_observations(id, element_uid, capture_id, frame_id, source cv|human|import, state, percent, confidence, occlusion_ratio, observed_at)`, `progress_rules(id, version, trade, ifc_class pattern, ladder jsonb [{percent, state, label}])`, `capture_sessions(id, project_id, level_id, captured_at, source insta360|openspace|upload, camera_model, status uploaded|aligned|processed|failed, transform_bim_from_slam float8[16], scale, rmse_m)`, `trajectory_nodes(id, capture_id, seq, x,y,z, qw,qx,qy,qz, t_sec)`, `panorama_frames(id, capture_id, node_id, image_key, width, height, blur_score, taken_at)`, `detections(id, frame_id, element_uid, state, confidence, occlusion_ratio, view_quality, pixel_bbox int[4], detected_qty)`, `schedule_activities(id, project_id, external_id, name, wbs, trade, zone_id, planned_start, planned_finish, actual_start, actual_finish, budget_cost, planned_qty, qty_unit, percent_complete)`, `activity_elements(activity_id, element_uid)`, `evm_snapshots(id, project_id, as_of, scope (project|trade|zone), scope_key, pv, ev, ac, spi, cpi, velocity_per_week)`, `deviations(id, element_uid, frame_id, kind positional|precover|missing|extra, dx,dy,dz, severity low|medium|high|critical, status open|closed, note, created_at)`, `api_keys(id, name, key_hash, created_at)`, `outbox_events(id, topic, payload jsonb, created_at, published_at)`.

## REST API (`/api/v1`, JSON)

| Method + path | Purpose |
|---|---|
| `GET /health` | `{status, db, storage, gpu}` |
| `GET/POST /projects`, `GET /projects/{pid}` | Projects |
| `GET /projects/{pid}/lbs` | `{buildings:[{id,name,levels:[{id,name,elevation_m,bbox,zones:[{id,name,polygon:[[x,y]..]|null,bbox}]}]}]}` (meters, project frame; polygon = IfcSpace footprint) |
| `GET /projects/{pid}/levels/{lid}/floorplan.svg?width=&walk=` | Plan SVG: zones coloured by percent complete, element footprints by status (red = open deviation), walk-path dots of the latest capture |
| `POST /uploads` `{project_id, filename, size, content_type, kind: capture\|model\|schedule\|evidence}` | Start a direct-to-storage multipart upload → `{upload_id, key, part_size 16–64 MB, parts:[{part_number, url}]}`. S3/R2: presigned PUT URLs (1 h) on `FAUXEL_S3_PUBLIC_ENDPOINT`; fs backend: API-side part URLs |
| `GET /uploads/{id}` | Resume: stored parts (S3 ListParts) + fresh URLs for pending parts |
| `PUT /uploads/{id}/parts/{n}` | API-side part upload (fs backend / local use; ≤ part_size) |
| `POST /uploads/{id}/complete` `{parts:[{part_number, etag}]}` / `DELETE /uploads/{id}` | Finish (size verified, returns `key`) / abort |
| `GET /jobs/{id}` | Worker job `{status: queued\|running\|done\|failed, result, error}` |
| `POST /projects/{pid}/models` (multipart `file`, IFC; or JSON `{upload_key}` → 202 `{job_id}`) | Upload and ingest an IFC → elements, LBS, meshes (zones get IfcSpace footprint polygons). Returns the model |
| `POST /projects/{pid}/models/import` (JSON, `contracts/bim-export.schema.json`) | Element export from the Revit or Navisworks plugin |
| `GET /projects/{pid}/models`, `GET /models/{mid}/file` | List models / download the IFC for the web viewer |
| `GET /projects/{pid}/elements?level_id&zone_id&trade&state&q&limit&offset` | Elements joined with `element_status` |
| `GET /elements/{uid}` | Element + status + observation history + evidence frames |
| `POST /elements/{uid}/observations` | `{state, percent?, confidence?, frame_id?, source:"human"}`. Human verification. The monotonic rule applies unless `rework:true` |
| `POST /projects/{pid}/captures` (multipart `files[]` equirect JPG/PNG or MP4, form `level_id`, `captured_at`, `camera_model`) | Create a capture, extract keyframes, store panoramas (201) |
| `POST /projects/{pid}/captures` (JSON `{upload_keys, level_id, captured_at, camera_model}`, or multipart with raw `.insv/.insp` / `async=true`) | Queued: 202 `{id, capture_id, job_id}`. Worker stitches ONE X2 raw files and keyframes video; capture status `uploaded → processing → ready \| failed` (+`error`, `telemetry`) |
| `GET /projects/{pid}/captures`, `GET /captures/{cid}`, `GET /captures/{cid}/frames` | Captures and frames (with node pose) |
| `GET /frames/{fid}/image?w=` | Panorama JPEG (resized) |
| `POST /captures/{cid}/align` | `{points:[{slam:[x,y,z], bim:[x,y,z]}], estimate_scale}` or `{tags:[...]}` → transform, RMSE |
| `PUT /captures/{cid}/trajectory` | `{nodes:[{frame_id, x,y,z, qw,qx,qy,qz}]}` pose import (SLAM output or manual pins) |
| `POST /captures/{cid}/process[?background=true]` | Projection + inference + fusion → detections, observations, status. Emits `inference.completed`. `background=true` queues a worker job (202) |
| `GET /frames/{fid}/overlay` | `[{element_uid, state, percent, polygon:[[u,v]...] (normalized 0..1), occlusion_ratio}]` for the 360° overlay |
| `GET /projects/{pid}/progress/summary` | `{percent_complete, spi, cpi, ev, pv, open_deviations, projected_finish, elements_total, elements_verified}` |
| `GET /projects/{pid}/progress/by-trade`, `/by-zone`, `/timeseries` | Breakdowns and an S-curve series `[{date, planned_pct, actual_pct}]` |
| `POST /projects/{pid}/schedule` (multipart P6 `.xer`/`.xml` or MS Project `.xml`), `POST /projects/{pid}/schedule/from-upload` `{upload_key}` | Ingest activities and auto-map to elements (zone + trade + WBS rules) |
| `GET /projects/{pid}/activities`, `GET /projects/{pid}/evm?scope=trade|zone`, `GET /projects/{pid}/forecast` | Schedule, EVM, slip forecast and trade-stacking alerts |
| `GET /projects/{pid}/deviations`, `POST /projects/{pid}/deviations`, `GET/PATCH /deviations/{id}` | QC issues; JSON includes `attachments:[{id,url,content_type}]`; `kind` also `note` |
| `POST /deviations/{id}/attachments` (multipart image, or JSON `{upload_key}` of an `evidence` upload), `GET /deviations/{id}/attachments`, `GET /attachments/{aid}/file` | Evidence photos |
| `POST /projects/{pid}/notes` `{level_id, x, y, text, photo?: upload_key, severity?}` | Field QC note → low-severity `note` deviation pinned at (x, y) m on a level |
| `GET /projects/{pid}/issues.bcf` | BCF 3.0 zip of open deviations |
| `GET /projects/{pid}/writeback?since=` | `contracts/writeback.schema.json` payload for the plugins |
| `GET /projects/{pid}/timeliner` | `[{external_id, name, actual_start, actual_finish, percent_complete}]` for the Navisworks TimeLiner sync |
| `POST /projects/{pid}/writeback/ack` | `{host:"revit"|"navisworks", applied:int, model:"..."}` |
| `GET /projects/{pid}/export.csv` | Element progress CSV (also for Power BI) |
| `POST /api-keys` | Create an API key (admin, local only) |

## Shared JSON contracts (`contracts/`)

`writeback.schema.json`:
```json
{"project_id":"uuid","generated_at":"iso","rules_version":1,
 "elements":[{"element_uid":"uuid","revit_unique_id":"str|null","ifc_global_id":"str|null","navis_item_guid":"str|null",
   "percent_complete":0,"status":"INSTALLED","last_verified":"iso|null","confidence":0.0,"evidence_url":"str|null","color":"#22c55e"}]}
```
`bim-export.schema.json` (from the Revit or Navisworks plugin):
```json
{"source":"revit|navisworks","model_name":"str","host_version":"2027","units":"m",
 "shared_transform":[16 floats, internal->shared, meters],
 "levels":[{"name":"L1","elevation_m":0.0}],
 "elements":[{"revit_unique_id":"str|null","ifc_global_id":"str|null","navis_item_guid":"str|null","name":"str","category":"str","family_type":"str","level":"str","room_or_zone":"str|null","trade":"str","aabb_min":[0,0,0],"aabb_max":[0,0,0],"quantity":0.0,"quantity_unit":"m|m2|m3|ea"}]}
```
Event topics (`contracts/events/*.schema.json`): `capture.uploaded`, `trajectory.aligned`, `inference.completed`, `deviation.detected`, `progress.synced`. Each payload has `{event_id, occurred_at, project_id, ...}`.

## Default progress rules (v1)

| Trade | Ladder |
|---|---|
| arch_interior (walls) | 0 NOT_STARTED · 25 FRAMED_OR_ROUGHED (track) · 50 FRAMED_OR_ROUGHED (studs) · 65 FRAMED_OR_ROUGHED (MEP rough verified + insulation) · 80 INSTALLED (board hung) · 95 INSTALLED (taped) · 100 INSULATED_OR_FINISHED (painted) |
| hvac, plumbing_fp, electrical | 0 NOT_STARTED · 30 FRAMED_OR_ROUGHED (hangers/supports) · 80 INSTALLED · 100 INSULATED_OR_FINISHED (insulated/trimmed) |
| structural | 0 NOT_STARTED · 40 FRAMED_OR_ROUGHED (formwork/rebar) · 100 INSTALLED (poured/erected) |
| other | 0 NOT_STARTED · 100 INSTALLED |

## CV in v1 (honest scope)

No trained trade models exist yet. v1 inference is BIM-prior change detection: for each visible element (from the equirect projection), compare edge density and color statistics inside its projected mask against the same element in the previous capture and an empty-scene baseline. Output a state proposal with confidence. Proposals below 0.8 confidence go to a **verification queue** in the portal, where a person confirms them with one click (Doxel-style QA). Every human verification is stored as a labeled sample for training the M4 trade heads later. A model slot (`FAUXEL_MODEL_DIR` with `<trade>/manifest.json` + `model.onnx`) replaces the heuristic per trade when a trained model exists.
