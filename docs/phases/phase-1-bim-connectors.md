# Phase 1 — bim-connectors

> Original design brief. Superseded by the decision log: the C# core is netstandard2.0, Revit hosts target net8/net10 (2025–2027) and Navisworks hosts target net48 (2025–2027), not ".NET 8" across the board.

Act as a Senior BIM Software Engineer specializing in C# (.NET 8), Autodesk Platform Services (APS), and OpenBIM toolkits. Build the `bim-connectors` service for Fauxel using the canonical schema from Phase 0.

**Implement the following 5 integration adapters behind a unified `IBimConnector` interface:**

1. **Autodesk Revit Plugin (C# Revit API):**
   - Extract all modeled elements across Structural, Architectural, Mechanical, Electrical, Plumbing, and Fire Protection categories with `UniqueId`, `IfcGUID`, Level, Zone/Room, Phase, Family/Type, and volumetric/linear/count quantities.
   - Read Project Base Point, Survey Point, and True North rotation to output the exact 4x4 transformation matrix.
   - Write back Fauxel's verified `% Complete`, `Installation_Status`, and `Last_Verified_Date` into custom shared parameters and apply color-coded view overrides.

2. **Autodesk Navisworks Manage Plugin (C# .NET API):**
   - Parse federated `.nwd`/`.nwc` models, preserving the Selection Tree hierarchy, Search Sets, and Selection Sets.
   - Export Clash Detective test results (hard clashes, clearance tolerances) with element GUID pairs and 3D viewpoints.
   - Sync TimeLiner 4D tasks (Planned Start/End vs. Actual Start/End) directly with Fauxel's schedule engine.

3. **Autodesk Forma & ACC Cloud Connector (APS REST + GraphQL APIs):**
   - Implement OAuth 2.0 (3-legged and 2-legged) authentication.
   - Connect to **AEC Data Model API** and **Model Derivative API** for cloud model/property extraction without desktop software.
   - Sync **Forma Locations** (LBS tree), **Forma Assets** (status updates per element), **Forma Photos** (pushing geo-tagged 360° keyframes), **Forma Model Coordination** (clash sets), and **Forma Issues** (auto-creating issues with 3D camera pins).

4. **Open-Source IFC & Speckle Connector (Python + TypeScript):**
   - Use **IfcOpenShell (Python)** to parse IFC2X3/IFC4/IFC4X3 files, generate explicit triangulated meshes, extract `IfcBuildingStorey` and `IfcZone` spatial trees, and build Bounding Volume Hierarchies (BVH) for fast raycasting.
   - Implement a **Speckle Server** GraphQL/REST connector to stream commits, branches, and object diffs as an open-source alternative to ACC/Forma.
   - Support **BCF (BIM Collaboration Format)** XML/API export/import for open-source issue tracking.

Provide complete, production-ready code skeletons, authentication flows, webhook listeners for model version updates, and unit tests.
