# Phase 5 — deviation-qc-engine

> Original design brief.

Act as a VDC Quality Control & Spatial Geometry Engineer. Build Fauxel's `deviation-qc-engine` in Python and C# that compares CV-detected field installations against the coordinated BIM model.

**Implement the following features:**
1. **3D Back-Projection & Positional Deviation Check:**
   - Triangulate multi-view 2D segmentation masks from consecutive Insta360 frames (using stereo baseline along the walk trajectory + monocular metric depth refinement) into 3D field point clouds/bounding boxes.
   - Compare detected field centroids and centerlines against design BIM element coordinates.
   - Flag **Positional Deviations** exceeding trade-specific tolerances (e.g., > 0.5 inches for anchor bolts/sleeves, > 1.5 inches for overhead ductwork/piping).
2. **Pre-Cover Rough-In Verification ("In-Wall X-Ray" Audit):**
   - Cross-check Architectural drywall closure detections against Electrical, Plumbing, and HVAC rough-in states in the same wall/ceiling zone.
   - Trigger an immediate **Critical QC Alert** if drywall installation is detected over unverified or incomplete in-wall MEP/backing elements.
3. **Bi-Directional Issue & Clash Sync:**
   - Correlate field deviations with existing Navisworks Clash Detective reports and Forma Model Coordination spaces.
   - Automatically generate structured QC Issues containing: element GUID, measured offset vector `(dx, dy, dz)`, annotated 360° panorama crop with BIM wireframe overlay, and priority severity.
   - Push issues directly to **Autodesk Forma/ACC Issues API**, **Navisworks Saved Viewpoints**, and **BCF 2.1/3.0** exports.
