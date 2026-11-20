# Fauxel

Open-source construction progress tracking from 360° capture to BIM, and back into the model.

Fauxel aligns 360° site walks (Insta360 cameras, OpenSpace captures) to the BIM model, uses computer vision to verify the installation state of each element, and **writes the verified progress back into Revit and Navisworks** (shared parameters, color-coded views, TimeLiner actuals), so the model itself shows field progress against the schedule. It works on open standards (IFC, BCF, Speckle) with no Autodesk license required, and adds Autodesk adapters (Revit, Navisworks, Forma/ACC via APS) when you have them.

## Status

Early build. See [docs/ROADMAP.md](docs/ROADMAP.md) for milestones and the decision log.

| Area | State |
|---|---|
| Design briefs (phases 0–8) | ✅ [docs/phases](docs/phases) |
| Capture → BIM alignment math + code (equirect projection, AprilTag tangent planes, Umeyama Sim(3)/SE(3), Revit/IFC frames) | ✅ [services/capture-ingestion](services/capture-ingestion), tested |
| Schema, event contracts, dev stack | ⏳ M0.1–M0.3 |
| IFC / BCF connector | ⏳ M1.1–M1.2 |
| Revit write-back plugin (2025–2027) | ⏳ M1.3 |
| Navisworks TimeLiner sync (2025–2027) | ⏳ M1.4 |

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

## Quick start (what exists today)

```bash
cd services/capture-ingestion
pip install -e ".[test]"
pytest -q
python -m fauxel_capture.alignment   # Sim(3) alignment demo
```

## Supported targets

- Revit 2025, 2026, 2027 · Navisworks Manage 2025, 2026, 2027
- IFC2X3 / IFC4 / IFC4X3 · BCF 2.1 / 3.0 · Speckle
- Insta360 ONE X2 (reference), X3, X4, X5 over OSC · OpenSpace via API

## License

[Apache-2.0](LICENSE). GPL tools (ORB-SLAM3, Blender/BlenderProc) are only used as optional, separately run workers.
