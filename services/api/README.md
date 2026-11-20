# Fauxel API

FastAPI backend: IFC / Revit / Navisworks ingest, 360 capture, alignment, projection + CV fusion, progress analytics, write-back.
Contract: `docs/specs/platform-contract.md`. Apache-2.0.

## Run

```bash
pip install -e ../capture-ingestion -e ".[kafka,test]"      # from services/api
export FAUXEL_DATABASE_URL=postgresql://fauxel:fauxel@localhost:5432/fauxel
export FAUXEL_S3_ENDPOINT=http://localhost:9000 FAUXEL_S3_BUCKET=fauxel-media FAUXEL_S3_ACCESS_KEY=... FAUXEL_S3_SECRET_KEY=...
export FAUXEL_AUTH=off                                        # dev only
python -m fauxel_api.migrate                                  # also runs on startup unless FAUXEL_AUTO_MIGRATE=0
uvicorn fauxel_api.app:app_factory --factory --port 8000
python -m fauxel_api.relay                                    # outbox -> Redpanda (needs FAUXEL_KAFKA_BOOTSTRAP)
python -m fauxel_api.worker                                   # job queue: capture ingest (video, raw .insv/.insp), IFC ingest, background processing
pip install shapely                                           # optional: concave IfcSpace footprints (convex hull without it)
```

Docker (repo root): `docker build -f services/api/Dockerfile -t fauxel-api .`

## Environment

| Var | Default | Meaning |
|---|---|---|
| `FAUXEL_DATABASE_URL` | `postgresql://fauxel:fauxel@localhost:5432/fauxel` | Postgres 16 + PostGIS + pgvector (migration needs rights to `CREATE EXTENSION`) |
| `FAUXEL_STORAGE` | `s3` | `s3` (MinIO/R2) or `fs` |
| `FAUXEL_FS_ROOT` | `./.fauxel-data` | directory for `fs` storage |
| `FAUXEL_S3_ENDPOINT` / `_BUCKET` / `_ACCESS_KEY` / `_SECRET_KEY` / `_REGION` | - / `fauxel-media` / - / - / `auto` | S3 API settings |
| `FAUXEL_S3_PUBLIC_ENDPOINT` | = `FAUXEL_S3_ENDPOINT` | endpoint used in presigned upload URLs, i.e. what browsers / phones can reach (R2 `https://<account>.r2.cloudflarestorage.com`, `http://localhost:9000` for MinIO) |
| `FAUXEL_S3_ADDRESSING` | `path` | `path` or `virtual` bucket addressing for presigned URLs (R2 supports both) |
| `FAUXEL_MAX_DIRECT_UPLOAD_MB` | `20480` | cap for `capture` direct uploads (model 4096, schedule 512, evidence 50 are lower fixed caps) |
| `FAUXEL_AUTH` | `on` | `off` disables auth (dev) |
| `FAUXEL_KEY_SALT` | `change-me` | pepper mixed into API key hashes (each key also has its own salt) |
| `FAUXEL_TRUST_ACCESS_HEADER` | `0` | `1` trusts `Cf-Access-Authenticated-User-Email` (only behind the Worker/Tunnel) |
| `FAUXEL_ADMIN_TOKEN` | - | optional `X-Fauxel-Admin` token for `POST /api-keys`; otherwise only direct loopback callers (no proxy headers) may create keys |
| `FAUXEL_KAFKA_BOOTSTRAP` | - | enables the relay; without it events stay in `outbox_events` |
| `FAUXEL_PUBLIC_URL` | empty | prefix for `evidence_url` in write-back (empty = relative `/api/v1/...`) |
| `FAUXEL_BLUR_MIN` | `20` | Laplacian-variance floor for video keyframes |
| `FAUXEL_MAX_FRAMES` / `FAUXEL_MAX_UPLOAD_MB` | `1500` / `2048` | upload limits |
| `FAUXEL_MIGRATIONS_DIR` / `FAUXEL_CONTRACTS_DIR` | repo `db/migrations`, `contracts` | override when installed outside the repo |

## Conventions worth knowing

- Node poses (`PUT /captures/{id}/trajectory`): `T_slam_from_cam`, OpenCV camera axes, Hamilton quaternion `(qw,qx,qy,qz)`. With no alignment, poses are taken as already in the project frame.
- `capture_sessions.transform_bim_from_slam` is the row-major rigid `[R|t]`; `p_bim = scale * R * p_slam + t`.
- Revit/Navisworks import: `aabb_*` are internal-frame meters; the API applies `shared_transform` (row-major 4x4).
- `GET .../elements` returns a JSON list; the unpaged total is in `X-Total-Count`.
- Processing writes `element_status` from the fusion result even when confidence is below 0.8; the best view of each such element is queued in `GET /projects/{pid}/verification-queue` until a human `POST /elements/{uid}/observations` (which also cleans the queue).
- Open deviations override write-back status/colour (red) without changing stored progress. Processing also opens a `precover` deviation when a wall is board-hung while MEP in its zone is not verified.
- Extra `progress_rules` v1 row: `arch_interior` default ladder (0/30/80/100) for non-wall classes; `IfcWall%` uses the contract wall ladder.

## Direct uploads and the worker

Cloudflare rejects bodies over 100 MB, so large files go straight to object storage and the API only sees keys:

1. `POST /uploads {project_id, filename, size, content_type, kind: capture|model|schedule|evidence}` returns `{upload_id, key, part_size (16/32/64 MB), parts:[{part_number, url}]}`. With S3/R2 the URLs are presigned `PUT`s (1 h) on `FAUXEL_S3_PUBLIC_ENDPOINT`; with `FAUXEL_STORAGE=fs` they point at `PUT /api/v1/uploads/{id}/parts/{n}` on the API.
2. `PUT` each part (the `ETag` response header is the part's etag; R2/MinIO CORS must expose `ETag`). Resume: `GET /uploads/{id}` lists stored parts and fresh URLs for the missing ones.
3. `POST /uploads/{id}/complete {parts:[{part_number, etag}]}` finishes the upload (declared size is verified) and returns `key`. `DELETE /uploads/{id}` aborts.
4. Hand the key to `POST /projects/{pid}/captures` (`{upload_keys, level_id, captured_at, camera_model}`), `POST /projects/{pid}/models` (`{upload_key}`), `POST /projects/{pid}/schedule/from-upload` (`{upload_key}`), `POST /deviations/{id}/attachments` (`{upload_key}`) or `POST /projects/{pid}/notes` (`photo`). A key must belong to a completed upload of the same project and kind.

Captures from keys, raw `.insv/.insp` files and `?async=true` multipart return **202** with the capture id and a `job_id`; `python -m fauxel_api.worker` runs the job (video keyframes, `.insv/.insp` stitching via `fauxel_capture.insta360.stitch_file`) and moves the capture `uploaded -> processing -> ready | failed` (`error` holds the reason; `telemetry` holds the compacted ONE X2 trailer). `POST /captures/{id}/process?background=true` and IFC-by-key uploads are queued the same way; `GET /jobs/{id}` shows `status/result/error`. The queue is the `jobs` table (`FOR UPDATE SKIP LOCKED`, dead workers' jobs are requeued after 2 h, 3 attempts). Multipart uploads of plain stills stay synchronous (201).

## Floor plans, zones, field notes

`GET /projects/{pid}/lbs` returns per zone `polygon` ([[x,y],...] meters, from the IfcSpace footprint) and `bbox`, and per level `bbox`. `GET /projects/{pid}/levels/{lid}/floorplan.svg[?width=&walk=]` renders zones by percent complete, element footprints by status (red = open deviation) and the latest capture's walk path. `POST /projects/{pid}/notes {level_id, x, y, text, photo?}` creates a low-severity `note` deviation pinned on the plan; deviations carry `attachments:[{id, url, ...}]`.

## Tests

`FAUXEL_TEST_ADMIN_URL=postgresql://fauxel@localhost:55432/postgres pytest tests` (needs a Postgres with postgis + vector, superuser; creates DB `fauxel_pytest`). The buildingSMART sample test is skipped without internet.
