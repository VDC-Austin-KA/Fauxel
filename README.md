# Fauxel

Open-source construction progress tracking from 360° capture to BIM, and back into the model.

Fauxel aligns 360° site walks (Insta360 cameras, OpenSpace captures) to the BIM model, uses computer vision to verify the installation state of each element, and **writes the verified progress back into Revit and Navisworks** (shared parameters, color-coded views, TimeLiner actuals), so the model itself shows field progress against the schedule. It works on open standards (IFC, BCF, Speckle) with no Autodesk license required, and adds Autodesk adapters (Revit, Navisworks, Forma/ACC via APS) when you have them.

## Status

Full platform built and tested in containers (22 of 25 roadmap items done). Start here: [docs/setup/quickstart.md](docs/setup/quickstart.md). Roadmap and decision log: [docs/ROADMAP.md](docs/ROADMAP.md).

| Area | Where | State |
|---|---|---|
| API, database, job queue, direct-to-storage uploads | `services/api`, `db/migrations` | ✅ 86 tests |
| 360° projection, change detection, fusion, schedule/EVM, BCF, QC | `services/api/fauxel_api/{vision,analytics,qc}` | ✅ |
| ONE X2 raw stitching, IMU, AprilTag alignment | `services/capture-ingestion` | ✅ 26 tests |
| Trade-model training, ONNX slot, synthetic data (Blender, GPL, isolated) | `services/cv-inference`, `services/synthetic-worker` | ✅ pipeline; models need real verified samples |
| SLAM worker (stella_vslam) | `services/slam-worker` | ✅ image builds |
| Revit 2025–2027 + Navisworks 2025–2027 plugins | `connectors/{dotnet-core,revit,navisworks}` | ✅ 6 builds, 47 tests; live test pending |
| Autodesk Forma (ACC), Speckle, BCF 2.1/3.0 | `connectors/{aps-forma,ifc-speckle-bcf}` | ✅ 67 tests |
| Web portal (Doxel-style) | `apps/web-portal` | ✅ 38 tests, demo mode |
| Field app (iOS/Android, Expo) | `apps/field-app` | 🟡 44 tests; Access + direct uploads pending |
| Docker stack, Cloudflare (Worker, Pages, Tunnel, Access; R2 optional), Windows setup | `infra`, `scripts/windows` | ✅ full-stack smoke test 15/15 |
| OpenSpace adapter | — | ⛔ needs OpenSpace API docs |

Quality gate: `scripts/check.sh` runs every suite locally (no GitHub Actions in this repo).

## Architecture

```
capture (Insta360 ONE X2+ / OpenSpace API)
  → capture-ingestion: stitch, keyframes, stella_vslam, AprilTag + Umeyama alignment   [trajectory.aligned]
  → bim-projection-engine: equirect visibility, occlusion ratio, BIM prior masks
  → cv-inference: spherical backbone + trade heads, multi-view fusion, monotonic state   [inference.completed]
  → deviation-qc-engine: positional deviation, pre-cover audit, BCF / Issues           [deviation.detected]
  → analytics-engine: P6 / MSP / TimeLiner mapping, EVM, slip forecast
  → connectors: Revit + Navisworks write-back, Forma/ACC, Speckle, BCF                 [progress.synced]
  → web-portal (split 360°/BIM viewer, dashboards) · field-app (OSC capture, resumable upload)
```

Planned layout: `services/`, `connectors/{revit,navisworks,aps-forma,ifc-speckle-bcf}`, `apps/{web-portal,field-app}`, `contracts/`, `db/migrations/`, `infra/`, `docs/`.

## Developer quick start

```bash
docker compose -f infra/docker-compose.yml up -d --build     # db, storage, redpanda, api, worker, relay, portal
bash infra/smoke-test.sh                                      # end-to-end check (15 steps)
open http://localhost:8080                                    # portal (demo data when the API is unreachable)
scripts/check.sh                                              # every test suite
```

Windows production setup: [docs/setup/quickstart.md](docs/setup/quickstart.md).

## Supported targets

- Revit 2025, 2026, 2027 · Navisworks Manage 2025, 2026, 2027
- IFC2X3 / IFC4 / IFC4X3 · BCF 2.1 / 3.0 · Speckle
- Insta360 ONE X2 (reference), X3, X4, X5 over OSC · OpenSpace via API

## License

[Apache-2.0](LICENSE). GPL tools (ORB-SLAM3, Blender/BlenderProc) are only used as optional, separately run workers.
