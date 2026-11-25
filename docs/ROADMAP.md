# Fauxel Roadmap & Status

Last updated: 2026-11-25 (morning build: Forma Revit import fixed for current Model Derivative output, first live import of Snowdon Towers Structural (1,616 elements), worker container on the current image, collapsible element panel in the Reality viewer; pushed from KA's PC) · Repo: [VDC-Austin-KA/Fauxel](https://github.com/VDC-Austin-KA/Fauxel) · Mode: **build** (Claude builds and tests locally, pushes finished files to `main`; no GitHub Actions)

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
| M4.3 | Remaining trade heads: structural, HVAC, electrical, plumbing/FP | 4 | M4.2, M3.2 | Per-trade eval report | in-progress: eval report + release gate built (`python -m fauxel_cv.evaluate`, [spec](specs/M4.3-trade-heads-eval.md)); training waits on verified samples from real walks |
| M1.5 | APS / Forma adapter: OAuth 2/3-legged, AEC Data Model, Model Derivative, Issues, Photos, Locations, Assets, webhooks | 1 | M1.3 | Issue with a 3D pin created from a deviation in a test hub | done |
| M1.6 | Speckle connector | 1 | M1.1 | Commit streamed and diffed | done |
| M8.1 | Field app: OSC control, floorplan-guided walk, resumable tus upload | 8 | M2.1 | Controls a ONE X2 over OSC (192.168.42.1), captures and uploads to dev | in-progress: resumable direct-multipart slice built Nov 23 (`claude/patches/M8.1-slice2.patch` in the Project, not yet on main); needs on-device test with the ONE X2 |

## Decision log
| Date | Decision | Source |
|---|---|---|
| 2026-11-20 | Open-source/OpenBIM path is the core and gets built first. Autodesk is an adapter. | KA's goal statement |
| 2026-11-20 | C# connectors: shared netstandard2.0 core, Revit host on .NET 10, Navisworks host on net48 (overrides Phase 1 ".NET 8") | Verified platform requirements |
| 2026-11-20 | SLAM default is stella_vslam (BSD-2, native equirect). ORB-SLAM3 is an optional GPL worker | License hygiene |
| 2026-11-20 | Poses stored as quaternions, not roll/pitch/yaw (aligns Phase 0 with Phase 2) | Schema consistency |
| 2026-11-20 | Wall % ladder maps to the 5-state enum through a versioned progress_rules table | Phase 0/4 consistency |
| 2026-11-20 | Revit internal→shared uses `ProjectLocation.GetTotalTransform().Inverse` (GetTotalTransform maps shared→internal). Plugins export internal-frame boxes; the server applies shared_transform once | Senior review + Revit API forum usage |
| 2026-11-20 | Treat "ACC" and "Forma" as one adapter (rebrand, same endpoints) | Mar 2026 rebrand |
| 2026-11-20 | License: Apache-2.0 | KA deferred to Claude |
| 2026-11-20 | OpenSpace integration via API (KA has API access). M2.5 unblocked | KA |
| 2026-11-20 | Reference camera: Insta360 ONE X2 (OSC supported). X3/X4/X5 support stays in scope, but tests and calibration target the X2 first | KA |
| 2026-11-20 | Supported hosts: Revit 2025–2027, Navisworks 2025–2027 | KA |
| 2026-11-20 | Navisworks 2027 stays on .NET Framework 4.8 (no .NET 8 move). All Navisworks hosts net48, delivered as an ApplicationPlugins bundle | KA confirmed; third-party 2027 SDK builds |
| 2026-11-20 | Test fixtures: public IFC samples only (buildingSMART Sample-Test-Files), downloaded by script, not committed | KA |
| 2026-11-20 | Repo: VDC-Austin-KA/Fauxel. This Project doc is KA's live status, mirrored to `docs/ROADMAP.md` on every push | KA |
| 2026-11-20 | Hosting: portal on Cloudflare Pages + Worker gateway at fauxel.ackerworks.com (Access-protected), media in R2; Postgres/PostGIS/pgvector, API and GPU compute on KA's PC in Docker, reached only via Cloudflare Tunnel | KA + Cloudflare limits (no GPU containers, no PostGIS) |
| 2026-11-20 | v1 backend is one FastAPI service plus a Postgres job-queue worker, with outbox + Redpanda events so modules can split out later | Minimalism ladder |
| 2026-11-20 | v1 CV = BIM-prior change detection; anything under 0.8 confidence goes to the human verification queue and never writes verified status; verified samples train the ONNX trade models | Senior review finding #1 |
| 2026-11-20 | Large files upload straight to R2/S3 with presigned multipart PUTs (Cloudflare caps request bodies at 100 MB) | Field app review |
| 2026-11-20 | Machine clients (plugins, phone app, CLI) pass Cloudflare Access with a service token (CF-Access-Client-Id/Secret) | Senior review finding #5 |
| 2026-11-20 | Local S3 is RustFS (MinIO stopped publishing community images) | Infra build |
| 2026-11-20 | R2 is optional: media can stay in the PC's S3 storage, published through the same Tunnel at fauxel-files.<domain> (presigned uploads; Worker without R2 via `FAUXEL_MEDIA=local`). KA's live setup uses this | KA (no extra paid service) |
| 2026-11-20 | Portal 3D model page: level/trade filters; color by installation status, current condition, or schedule variance (verified % vs. activity plan, as-of date, on-time range) | KA (ACC Model Coordination-style quick look) |
| 2026-11-20 | Trade model release gate on capture-held-out data: n ≥ 50, precision ≥ 95% for predictions at conf ≥ 0.8 (they write verified status unreviewed), ECE ≤ 0.10, within one rung ≥ 90%. Eval scores the exported ONNX with slot preprocessing | M4.3 sprint |
| 2026-11-20 | One-command Windows setup: `Start-Fauxel.ps1` in C:\Fauxel (prereqs, clone/update, plugins, stack, key, project, sample IFC; `-Camera`, `-Capture`, `-Cloudflare`, `-DeployWeb`). `.gitattributes` keeps container scripts LF on Windows | KA |
| 2026-11-20 | Public sharing is a server-enforced, expiring, read-only window (`FAUXEL_PUBLIC_READ_UNTIL`; `-PublicToday`/`-Private`): keyless GET only, integrations/uploads/training closed, Access identity header ignored while open. `FAUXEL_TRUST_ACCESS_HEADER` is no longer forced on by `-Production` (unsafe under a Bypass policy) | KA (share for a day) |
| 2026-11-20 | Demo data = three building types (data center, MOB, podium multifamily) as federated discipline IFCs (A/S/M/E/P/FP) with a P6 schedule, schedule-driven progress and QC issues (`scripts/demo/make_demo.py`). The 3D view loads every current IFC model; mock projects get type-specific fit-outs. Schedule mapping: zone activities must match their level; `Roof` is a level | KA |
| 2026-11-20 | No GitHub Actions, Copilot or GitHub-hosted agents. Claude builds and tests locally (`scripts/check.sh`) and pushes finished files straight to `main` (no PR flow). CI workflow removed | KA |
| 2026-11-22 | Portal offers a real Autodesk sign-in and a demo Autodesk connection side by side. Demo is a saved choice (`fauxel.mode`), not a silent fallback; a failed /health probe is retried once and the reason is shown. The APS callback sends browsers back to the portal (`?aps=connected` / `aps_error`) and still returns JSON to API clients | KA could not reach his real Forma account |
| 2026-11-23 | Field app uploads captures through the direct multipart API (`/uploads`), part-level resume via `GET /uploads/{id}`; the "tus" in M8.1 is met by this instead of a tus server (no new dependency) | Minimalism ladder; Cloudflare 100 MB cap |
| 2026-11-24 | Autodesk browser sign-in is stored account-wide (it is the user's identity, not a project setting). Portfolio › "Add from Autodesk Forma" creates or reopens the Fauxel project linked to a Forma hub/project (`POST /integrations/aps/projects`, one Fauxel project per Forma project) | KA: Portfolio only showed demo projects |
| 2026-11-25 | "Add from Autodesk Forma" never reopens silently: if the Forma project is already linked (e.g. to a demo project), the portal names that project and offers "create a new project and move the link there" (`relink`). The whole APS link row moves, so there is still one Fauxel project per Forma project; imported models stay where they are | KA: Create kept reopening Cedar Park MOB |
| 2026-11-24 | The account-wide Autodesk sign-in wins over a legacy project-scoped row; migration 004 moves a lone legacy row to account-wide (idempotent, newest kept) | Morning build: KA's PC had both rows |
| 2026-11-24 | Revit/Navisworks exports: model name drops the host extension (`4D.nwd` = `4D`, Windows "hide extensions" changes doc.Title), older models recount their elements after a re-send, and an export with no building joins the project's main building instead of adding "Building 1" | Shakedown on Cedar Park MOB |
| 2026-11-24 | Reality viewer layouts: 360°+model, 360°+plan, 360° only, model only (remembered per browser). The floorplan is the capture path: click or drag empty plan to turn the 360° camera, click an element footprint to select and look at it; the wedge follows the camera live. The element panel docks on wide screens instead of covering the viewers | KA |
| 2026-11-25 | Model Derivative Revit imports take the category from the object tree when the property collection has no `__category__` group (current MD output), and parse `ft-and-fractional-in` values. Reality viewer element panel collapses to a pill and the viewers stack while it is open below 1280 px, so it never covers the 360° view or plan | Morning build: Snowdon Towers import returned 0 elements; KA (panel must not block the viewers) |
| 2026-11-24 | `Start-Fauxel.ps1` no longer resets local `main` when it has commits not on origin (it silently discarded them); it warns and skips the update | Morning build lost its PC commit to `-DeployWeb` once |

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
- APS: live sign-in, hub list (hub "Fauxel") and Forma model list verified Nov 24 on KA's PC (Cedar Park MOB linked, 43 Forma files). Nov 25: Snowdon Towers Sample Structural.rvt imported through Model Derivative (1,616 elements, placeholder boxes: MD carries no geometry, so real boxes arrive with a Revit plugin export or an IFC of the same model). Speckle against a live server
- Portal through Cloudflare Access: neither KA's Chrome nor the built-in browser is signed in to Access for scheduled runs, so live checks ran against the API on KA's PC (Nov 25: every GET endpoint 200 on all 4 projects) and the portal UI was walked in demo mode with Playwright. Sign in once in Chrome (Access email code) to let runs check the live pages
- Real captures: no project has a 360° capture yet, so the Reality viewer has only been exercised on demo data
- M4.3: trade models need verified samples from real walks (≥ 2 captures, ≥ 50 held-out crops per trade), then train + `python -m fauxel_cv.evaluate --strict` on KA's PC
- M2.5: blocked on KA's OpenSpace API documentation (no endpoints guessed)

## Review follow-ups (medium/low, open)
Job heartbeat for long stitches; row locking between CV processing and human checks; bbox-local masks + batched mesh loads for 10k+ element models; skip boxes that contain the camera; streamed multipart uploads; webhook body cap; defusedxml everywhere; Revit workshared checkout handling; Navisworks single-pass property reads; SharedParams restore order; TimeLiner date compare; phone app Access headers + direct-to-R2 uploads.

## Open questions for KA
- Scheduled runs push from KA's PC (C:\Fauxel git credentials); the cloud token for VDC-Austin-KA/Fauxel is invalid. Fine as is unless KA wants runs to work with the PC off
- Forma models import with placeholder boxes (no geometry in Model Derivative). Want an SVF2/IFC geometry pass for Forma-only models (bigger job), or rely on the Revit plugin export for boxes?
- A legacy project-scoped Autodesk token row remains for Cedar Park MOB (now unused). Delete it? (`DELETE FROM integration_tokens WHERE provider='aps' AND kind='three_legged' AND project_id IS NOT NULL`)
- Answered Nov 20: license→Apache-2.0, OpenSpace→API, camera→ONE X2, hosts→2025–2027, fixtures→public IFC, repo→VDC-Austin-KA/Fauxel
- **Build sprint scheduled task has no push access to VDC-Austin-KA/Fauxel** (its git proxy only allows authorized repos; the Nov 20 sprint's M4.3 slice was applied and pushed from KA's main session as e509b7f). Fix: recreate the sprint task from a chat that has the repo attached, or grant the repo to scheduled runs
- M4.3: should the API model slot refuse a trade model whose `eval.json` is not `pass` (heuristic fallback instead)? Default today: it loads any valid manifest
- To verify in the sprints: the timing of Autodesk's Revit 2025/2026 .NET 10 update

## Scheduled tasks
| Task | Schedule (America/Chicago) | ID | State |
|---|---|---|---|
| Fauxel morning build: finishes open work, full stack shakedown on real data, then the roadmap; replaces "Fauxel Daily" and the build sprint | Daily, morning | — | active, pushes from KA's PC |
| Fauxel Daily | Daily 1:17 PM | trig_01DzJPeYofrffjdJatV4xSWg | replaced by the morning build |
| Fauxel build sprint | Tue + Fri 1:17 PM | trig_018XatVShzJ5uCt9YrEUwLfZ | disabled (replaced) |
| Fauxel ecosystem watch: weekly dependency/API/license check, updates ecosystem-watch.md | Mon 5:37 AM | — | not created (blocked by the approval check; KA to approve) |
