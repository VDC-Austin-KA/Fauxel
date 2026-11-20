# Fauxel — Project Instructions

## Mission
Fauxel is an open-source, Doxel-style construction progress tracking platform. 360° reality capture (Insta360 raw walks, OpenSpace exports) is aligned to the BIM model, computer vision verifies installation state per BIM element, and the verified state is **written back into Revit and Navisworks** (shared parameters, view filters, TimeLiner actuals) so the model itself shows field progress against the schedule. The write-back is the main differentiator. Doxel and OpenSpace stop at their own dashboards.

## Source of truth (project docs)
- `docs/phases/phase-0-architecture.md` architecture, monorepo, canonical schema, transforms, events → read first for any design question
- `docs/phases/phase-1..8-*.md`: bim-connectors, capture-ingestion, projection + synthetic data, cv-inference, deviation QC, analytics/EVM, web portal, mobile capture app
- `docs/math/coordinate-alignment.md` and `services/capture-ingestion/fauxel_capture/alignment.py`: equirect projection, AprilTag tangent-plane detection, Umeyama Sim(3)/SE(3), Revit/IFC frames. Reuse this code; don't rewrite it.
- `docs/ROADMAP.md`: milestone checklist, status, decision log, open questions. Update it whenever a milestone moves or a decision is made.
- `docs/ecosystem-watch.md`: verified versions, licenses and API changes of dependencies.
- `docs/specs/*`: detailed specs produced per roadmap item.
Read the relevant docs before answering. When a phase doc and the ROADMAP decision log disagree, the decision log wins.

## Non-negotiable design rules
1. **Two modes, one adapter layer.** Every BIM integration sits behind `IBimConnector` (C#) / `BimConnector` protocol (Python). The OpenBIM path (IFC + IfcOpenShell + web-ifc + Speckle + BCF) must work with zero Autodesk licenses. Build and test it first. Autodesk mode (Revit, Navisworks, Forma/ACC via APS) is an adapter, not the core.
2. **Canonical element identity.** `bim_elements.element_uid` is Fauxel's key. Revit `UniqueId`, IFC `GlobalId`, Navisworks `ItemGuid` and APS/AEC Data Model ids are mapped columns, never primary keys.
3. **Coordinates.** Store meters in a right-handed +X East, +Y North, +Z Up project frame. Camera math uses OpenCV (+X right, +Y down, +Z forward). Poses are quaternions `(qw,qx,qy,qz)` Hamilton, scalar-first, `T_target<-source` naming (`T_bim_from_cam`). Revit feet conversion happens only at the Revit adapter boundary. For Revit internal→shared, use `ProjectLocation.GetTotalTransform()` in the plugin rather than hand-rolled true-north math, and unit test it against the Python transformer.
4. **State model.** The 5-state enum (`NOT_STARTED`, `FRAMED_OR_ROUGHED`, `INSTALLED`, `INSULATED_OR_FINISHED`, `DEVIATION_FLAGGED`) is what gets stored. Trade-specific % ladders (e.g. the Phase 4 wall ladder 0/25/50/65/80/95/100) map onto it through a versioned `progress_rules` table, so write-back % and state stay consistent.
5. **Monotonic progress.** No regression without a rework/demolition flag. Occluded elements keep their last high-confidence state.
6. **Write-back contract.** Shared parameters `Fauxel_PercentComplete`, `Fauxel_Status`, `Fauxel_LastVerified`, `Fauxel_Confidence`, `Fauxel_EvidenceUrl` (GUID-locked shared parameter file in the repo). Status colors: Installed green, In progress yellow, Deviation red, Not started gray.
7. **Events.** Topics `capture.uploaded`, `trajectory.aligned`, `inference.completed`, `deviation.detected`, `progress.synced`. JSON Schema per topic in `/contracts`, versioned. No service reads another service's tables.
8. **License hygiene.** The default target is permissive (Apache-2.0). Copyleft deps (ORB-SLAM3 GPLv3, BlenderProc/Blender GPL) are isolated as optional out-of-process workers. Prefer stella_vslam (BSD-2, equirectangular native) for SLAM. Record every new dependency's license in ecosystem-watch.

## Platform baselines (verified Nov 2026; re-check in ecosystem-watch)
- Supported hosts: Revit 2025–2027, Navisworks 2025–2027. Revit 2027 add-ins: **.NET 10**. Revit 2025/2026: net8.0-windows today, with Autodesk moving them to .NET 10. Multi-target per Revit year.
- Navisworks plugins: **.NET Framework 4.8**, one binary per major version (v22=2025, v23=2026). The `bim-connectors` C# solution therefore needs a shared `netstandard2.0` core with net48 + net10 host projects. Phase 1's ".NET 8" is superseded.
- Autodesk Construction Cloud was renamed **Autodesk Forma** (Mar 2026). API endpoints didn't change, so code against the existing ACC/APS endpoints.
- Reference camera: Insta360 ONE X2. Insta360 OSC: X3/X4/X4 Air/X5, ONE X/X2/R/RS. Pro 2 uses the separate Pro Camera API. OpenSpace integration uses KA's OpenSpace API access. Insta360 Media SDK is application-gated, so the OpenCV stitch path must stand alone in open-source mode.

## How to work in this project
- I'm KA, a VDC lead. Skip BIM/VDC basics. Be terse and specific: plans as short bullets, then do the work.
- Before writing code, apply the minimalism ladder: does it need to exist, is it already in the repo or the phase docs, does stdlib/an existing dep cover it, smallest diff. Edit lines, don't rewrite files. Never drop input validation at trust boundaries (uploads, APS webhooks, OSC responses).
- Delegate boilerplate (DTOs, tests, schema migrations, docs) to cheaper sub-agents with minimal self-contained context, then review the result before merging.
- Every deliverable ends with: what changed, how to run or test it, and which ROADMAP item it closes.
- Present status and results as a dashboard (tables, status chips, KPI tiles), not long prose.
- When an API, endpoint or package fails (404/403/deprecated), find and implement the working alternative, then record it in ecosystem-watch so scheduled runs use it.
- Verify any version, endpoint or license claim with a search before putting it in code or docs.
- Durable outputs go to project docs: specs → `docs/specs/<roadmap-id>-<slug>.md`, decisions → ROADMAP decision log. Don't churn docs that haven't meaningfully changed.

## Repo
`VDC-Austin-KA/Fauxel`, monorepo: `services/{capture-ingestion,cv-inference,bim-projection-engine,deviation-qc-engine,analytics-engine}`, `connectors/{revit,navisworks,aps-forma,ifc-speckle-bcf}`, `apps/{web-portal,field-app}`, `contracts/`, `db/migrations/`, `infra/docker-compose.yml` (Postgres+PostGIS+pgvector, MinIO, Redpanda, Triton), `docs/`. Conventional commits, a draft PR per roadmap item, CI must pass.
