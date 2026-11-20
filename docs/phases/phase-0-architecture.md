# Phase 0 — System Architecture, Monorepo, Canonical Schema

> Original design brief. Where it conflicts with the decision log in `docs/ROADMAP.md`, the decision log wins.

Act as a Principal Software Architect and ConTech Systems Engineer. Design the complete system architecture, monorepo structure, and canonical data schema for "Fauxel," a Doxel-inspired construction progress-tracking, reality-capture, and 4D BIM analytics platform.

**Core Architectural Mandate:**
Fauxel must operate cleanly in two modes via a unified adapter layer:
1. **Autodesk Enterprise Mode:** Integrating Revit, Navisworks Manage, Autodesk Construction Cloud (ACC), and Autodesk Forma via Autodesk Platform Services (APS).
2. **Open-Source / OpenBIM Mode:** Integrating IFC2X3/IFC4/IFC4X3 files, IfcOpenShell, web-ifc (That Open Engine), Speckle, and BCF without requiring proprietary Autodesk licenses.

**Generate the following deliverables in full technical detail:**
1. **Monorepo Directory Structure:** Microservices/modular layout separating `capture-ingestion` (C++/Python), `cv-inference` (PyTorch/Triton), `bim-connectors` (C# .NET 8 + Python + Node/TS), `analytics-engine` (Python/FastAPI), and `web-portal` (React/TypeScript/Three.js).
2. **Canonical PostgreSQL + PostGIS + pgvector Schema (SQL DDL):**
   - `projects`, `buildings`, `levels`, and `zones` (Location Breakdown Structure / LBS).
   - `bim_models` and `bim_elements` (storing canonical `element_uid`, Revit `UniqueId`, IFC `GlobalId`, Navisworks `ItemGuid`, discipline/trade code, bounding box AABB/OBB, centroid, material, and expected quantity/unit).
   - `capture_sessions`, `trajectory_nodes` (6-DoF camera poses: `x, y, z, qw, qx, qy, qz` in shared project coordinate space), and `panorama_frames`.
   - `detections` (element-to-frame projections, occlusion ratio, installation state enum: `NOT_STARTED`, `FRAMED_OR_ROUGHED`, `INSTALLED`, `INSULATED_OR_FINISHED`, `DEVIATION_FLAGGED`, confidence score, detected quantity).
   - `schedule_activities` and `evm_snapshots` (Planned Value, Earned Value, Actual Cost, SPI, CPI, weekly installation velocity).
3. **Coordinate System & Transform Contract:** Define exact transformation matrices converting Insta360 spherical camera coordinates and VSLAM trajectories into Revit Project Base Point / Survey Point and IFC `IfcSite` world coordinates.
4. **API & Event Bus Specification:** Define gRPC/REST endpoints and Redis/Kafka event topics (`capture.uploaded`, `trajectory.aligned`, `inference.completed`, `deviation.detected`, `progress.synced`).
