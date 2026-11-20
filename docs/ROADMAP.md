# Fauxel Roadmap & Status

Last updated: 2026-11-20 (full platform build) · Repo: [VDC-Austin-KA/Fauxel](https://github.com/VDC-Austin-KA/Fauxel) · Mode: **build** (Claude builds and tests locally, pushes finished files to `main`; no GitHub Actions)

Status keys: `todo` · `in-progress` · `done` (tested and pushed to main) · `blocked`

The order is a vertical slice first: the open-source path, from capture to Revit write-back, on one trade, before going wide.

## Milestones

| ID | Item | Phase | Depends on | Definition of done | Status |
|---|---|---|---|---|---|
| M0.1 | Monorepo scaffold, CONTRIBUTING, docker-compose dev stack (LICENSE, docs, alignment code and `scripts/check.sh` already on main) | 0 | — | `docker compose up` brings up PG+PostGIS+pgvector, MinIO, Redpanda. `scripts/check.sh` passes | done |
| M0.2 | Canonical schema DDL + migrations (projects→zones LBS, bim_models/elements, capture_sessions, trajectory_nodes, panorama_frames, detections, progress_rules, schedule_activities, evm_snapshots) | 0 | M0.1 | Migrations apply clean. Seed fixture loads. ERD in docs | done |
| M0.3 | Event contracts (JSON Schema per topic) + transform contract doc | 0 | M0.2 | Schemas validate sample payloads in `scripts/check.sh` | done |
| M1.1 | IFC connector: IfcOpenShell parse → bim_elements, storey/zone tree, meshes, BVH | 1 | M0.2 | Public buildingSMART IFC4 sample ingests (fixtures fetched by script, not committed). Element count matches IfcOpenShell. Unit tests | done |
| M1.2 | BCF 2.1/3.0 import/export | 1 | M1.1 | Round-trip test with a BCF file from Revit/Navis | done |
| M2.1 | Ingest pre-stitched equirect video/photos → keyframes (1–1.5 m / 15°, Laplacian blur filter) → MinIO | 2 | M0.1 | Keyframes plus panorama_frames rows from a sample walk | done |
| M2.2 | stella_vslam equirect worker → trajectory_nodes | 2 | M2.1 | Trajectory output for a sample walk; out-of-process container | done |
| M2.3 | AprilTag tangent-plane detection + Umeyama alignment (reuse project code) → `trajectory.aligned` | 2 | M2.2, M1.1 | RMSE < 5 cm on a synthetic test. Field tag-placement guide | done |
| M3.1 | Equirect visibility/occlusion projection (instance-ID + depth buffer, occlusion ratio, view-quality score) | 3 | M1.1, M2.3 | Golden-image tests on a synthetic pose | done |
| M4.1 | Shared spherical backbone + BIM-prior mask input, inference API (FastAPI, ONNX) | 4 | M3.1 | Runs on a sample frame; `inference.completed` emitted | done |
| M4.2 | First trade head: arch_interior (wall ladder) + multi-view Bayesian fusion + monotonic state machine | 4 | M4.1 | Per-wall state on a labeled test walk; mapped through progress_rules | done |
| M1.3 | **Revit plugin: element export + write-back** (shared params, view filters/overrides, Fauxel ribbon) | 1 | M0.2 | Builds for Revit 2025, 2026 (net8.0-windows, net10 once Autodesk's update ships) and 2027 (net10.0-windows). Updated params and colored 3D view on a sample model | done |
| M1.4 | Navisworks plugin: selection tree export, search sets, TimeLiner actuals sync, status appearance profiles | 1 | M0.2 | Builds for Navisworks 2025, 2026, 2027 (all net48), deployed as one ApplicationPlugins bundle with Contents\v22/v23/v24 (2027 ignores the legacy Plugins folder). Actual start/finish written to TimeLiner from Fauxel data | done |
| M6.1 | Schedule ingest (P6 XER/XML, MSP XML, TimeLiner) + activity↔element mapping rules | 6 | M0.2, M1.1 | Sample XER maps to elements by LBS + CSI | done |
| M6.2 | EVM + production velocity + slip forecast | 6 | M6.1, M4.2 | PV/EV/SPI per zone/trade from fixture data | done |
| M7.1 | Web portal: split 360°/BIM viewer (That Open Engine first), synced camera, status overlay | 7 | M3.1 | Viewer shows a sample walk with overlay | done |
| M7.2 | Executive dashboard (KPIs, heatmaps, S-curves, pay-app table) | 7 | M6.2 | Renders from the analytics API | done |
| M5.1 | Deviation QC: back-projection, tolerances, in-wall pre-cover alert, BCF/Issues push | 5 | M4.2, M1.2 | Critical alert fires on a synthetic drywall-over-unverified-MEP case | done |
| M2.4 | Raw .insv/.insp ingest: OpenCV dual-fisheye stitcher + IMU extraction (Media SDK optional) | 2 | M2.1 | Stitches a ONE X2 .insv/.insp without the SDK; X2 lens calibration stored | done |
| M2.5 | OpenSpace capture-source adapter (API → captures, 360 frames, sheet positions → panorama_frames + poses) | 2 | M0.2 | Pulls one capture from KA's OpenSpace account via API; frames registered with poses. Endpoint docs come from KA's OpenSpace developer access | blocked |
| M3.2 | Synthetic 360° data generator (Blender worker) + fine-tune script | 3 | M3.1 | 1k labeled frames from a sample model | done |
| M4.3 | Remaining trade heads: structural, HVAC, electrical, plumbing/FP | 4 | M4.2, M3.2 | Per-trade eval report | in-progress |
| M1.5 | APS / Forma adapter: OAuth 2/3-legged, AEC Data Model, Model Derivative, Issues, Photos, Locations, Assets, webhooks | 1 | M1.3 | Issue with a 3D pin created from a deviation in a test hub | done |
| M1.6 | Speckle connector | 1 | M1.1 | Commit streamed and diffed | done |
| M8.1 | Field app: OSC control, floorplan-guided walk, resumable tus upload | 8 | M2.1 | Controls a ONE X2 over OSC (192.168.42.1), captures and uploads to dev | in-progress |

## Decision log
| Date | Decision | Source |
|---|---|---|
| 2026-11-20 | Open-source/OpenBIM path is the core and gets built first. Autodesk is an adapter. | KA's goal statement |
| 2026-11-20 | C# connectors: shared netstandard2.0 core, Revit host on .NET 10, Navisworks host on net48 (overrides Phase 1 ".NET 8") | Verified platform requirements |
| 2026-11-20 | SLAM default is stella_vslam (BSD-2, native equirect). ORB-SLAM3 is an optional GPL worker | License hygiene |
| 2026-11-20 | Poses stored as quaternions, not roll/pitch/yaw (aligns Phase 0 with Phase 2) | Schema consistency |
| 2026-11-20 | Wall % ladder maps to the 5-state enum through a versioned progress_rules table | Phase 0/4 consistency |
| 2026-11-20 | Revit internal→shared uses `ProjectLocation.GetTotalTransform()`. The Python transformer is the test oracle | Avoid true-north sign errors |
| 2026-11-20 | Treat "ACC" and "Forma" as one adapter (rebrand, same endpoints) | Mar 2026 rebrand |
| 2026-11-20 | License: Apache-2.0 | KA deferred to Claude |
| 2026-11-20 | OpenSpace integration via API (KA has API access). M2.5 unblocked | KA |
| 2026-11-20 | Reference camera: Insta360 ONE X2 (OSC supported). X3/X4/X5 support stays in scope, but tests and calibration target the X2 first | KA |
| 2026-11-20 | Supported hosts: Revit 2025–2027, Navisworks 2025–2027 | KA |
| 2026-11-20 | Navisworks 2027 stays on .NET Framework 4.8 (no .NET 8 move). All Navisworks hosts net48, delivered as an ApplicationPlugins bundle | KA confirmed; third-party 2027 SDK builds |
| 2026-11-20 | Test fixtures: public IFC samples only (buildingSMART Sample-Test-Files), downloaded by script, not committed | KA |
| 2026-11-20 | Repo: VDC-Austin-KA/Fauxel. This Project doc is KA's live status, mirrored to `docs/ROADMAP.md` on every push | KA |
| 2026-11-20 | No GitHub Actions, Copilot or GitHub-hosted agents. Claude builds and tests locally (`scripts/check.sh`) and pushes finished files straight to `main` (no PR flow). CI workflow removed | KA |

## Spec gaps found in the phase docs
1. OpenSpace is in the project goal but no phase covers it, so M2.5 was added.
2. Phase 0 `trajectory_nodes` uses quaternions while Phase 2 outputs roll/pitch/yaw. Resolved: quaternions.
3. Phase 4's 7-step wall ladder isn't reconciled with Phase 0's 5-state enum. Resolved: progress_rules.
4. In `Mathematical approach.txt`, `to_revit_internal_feet` assumes the tag survey frame equals Revit internal coordinates. Tag survey coordinates need a declared frame (`internal` | `shared` | `ifc_site`) per tag set.
5. Phase 8 targets the Insta360 Pro 2 over OSC, but Pro 2 uses the Pro Camera API. OSC exposure-lock support per model needs a hardware test.
6. No auth/tenancy model (RBAC, project membership) is specified for the web portal or APIs. Add it to M0.2.
7. No capture QA metric is defined (coverage %, alignment RMSE gate before inference runs). Add an RMSE gate to M2.3.

## Live verification still needed (built and tested in containers, not yet on real hardware)
- Revit 2025–2027: ribbon, shared parameter binding, "Fauxel Progress" view, internal→shared direction (`GetTotalTransform().Inverse`) on a model with a rotated survey point
- Navisworks 2025–2027: bundle loading, TimeLiner TaskEdit, color overrides on real NWC/NWD
- ONE X2: OSC option names, raw .insv stitch calibration (`python -m fauxel_capture.insta360 refine`), phone app on device
- APS: one live sign-in and import; Speckle against a live server
- M4.3: trade models need verified samples from real walks before training (pipeline ready)
- M2.5: blocked on KA's OpenSpace API documentation (no endpoints guessed)

## Review follow-ups (medium/low, open)
Job heartbeat for long stitches; row locking between CV processing and human checks; bbox-local masks + batched mesh loads for 10k+ element models; skip boxes that contain the camera; streamed multipart uploads; webhook body cap; defusedxml everywhere; Revit workshared checkout handling; Navisworks single-pass property reads; SharedParams restore order; TimeLiner date compare; phone app Access headers + direct-to-R2 uploads.

## Open questions for KA
- (none open) Answered Nov 20: license→Apache-2.0, OpenSpace→API, camera→ONE X2, hosts→2025–2027, fixtures→public IFC, repo→VDC-Austin-KA/Fauxel
- To verify in the sprints: the timing of Autodesk's Revit 2025/2026 .NET 10 update

## Scheduled tasks
| Task | Schedule (America/Chicago) | ID | State |
|---|---|---|---|
| Fauxel build sprint: builds and tests the next ready roadmap item itself, pushes finished files to `main`, updates this file | Tue + Fri 1:17 PM | trig_018XatVShzJ5uCt9YrEUwLfZ | active, auto approval |
| Fauxel ecosystem watch: weekly dependency/API/license check, updates ecosystem-watch.md | Mon 5:37 AM | — | not created (blocked by the approval check; KA to approve) |
