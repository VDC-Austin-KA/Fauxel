# Phase 4 — cv-inference

> Original design brief. Decision log: the wall % ladder maps onto the 5-state enum through a versioned `progress_rules` table. The first trade head built is arch_interior (roadmap M4.2).

Act as a Lead Computer Vision Scientist specializing in Construction Automation. Build Fauxel's modular `cv-inference` microservice in Python (PyTorch, ONNX Runtime / TensorRT, FastAPI).

**Architecture Requirement:**
Use a shared high-resolution spherical feature backbone (handling equirectangular distortion via spherical padding / tangent-plane projection) paired with **projected BIM prior masks** from Phase 3 (guided segmentation/change-detection), followed by **5 specialized Trade Detection Heads**:

1. **Trade Module A — Civil, Structural & Concrete (`structural_detector`):**
   - Detect formwork placement, rebar cages/mesh, post-tensioning tendons, anchor bolts/embeds, poured concrete columns, shear walls, slabs-on-grade, elevated decks, and structural steel wide-flange beams/columns/bracing.
   - Compute volumetric (`m³` / `yd³`), area (`m²` / `ft²`), and linear tonnage progress.

2. **Trade Module B — Architectural, Framing & Drywall (`arch_interior_detector`):**
   - Classify multi-stage wall assembly states per BIM wall segment: `0%` (layout chalk lines only) -> `25%` (top/bottom tracks) -> `50%` (metal/wood studs & backing) -> `65%` (in-wall MEP rough-in verified & acoustic/thermal insulation) -> `80%` (one-sided or two-sided gypsum board hung) -> `95%` (taped & mudded) -> `100%` (primed/painted).
   - Detect hollow-metal door frames, doors, glazing/curtain wall mullions, and acoustic ceiling grids (ACT).

3. **Trade Module C — Mechanical / HVAC (`hvac_detector`):**
   - Detect clevis/trapeze hangers, unistrut supports, rectangular sheet-metal ductwork, spiral round ducts, flex ducts, fire/smoke dampers, VAV boxes, and major mechanical equipment (AHUs, chillers, pumps).
   - Measure linear feet installed and count discrete equipment pieces against BIM GUIDs.

4. **Trade Module D — Electrical & Low Voltage (`electrical_detector`):**
   - Detect EMT/IMC/rigid conduit runs, conduit racks, cable trays, junction/pull boxes, panelboards, transformers, switchgear, and rough-in vs. trim-out lighting fixtures.

5. **Trade Module E — Plumbing & Fire Protection (`plumbing_fp_detector`):**
   - Detect cast-iron/PVC/copper risers and horizontal mains, insulation jacketing, isolation valves, roof drains, and fire sprinkler mains, branch lines, and sprinkler heads.

**Multi-View Fusion & State Machine:**
- Aggregate detections across multiple overlapping Insta360 keyframes along a walk using Bayesian confidence weighting.
- Enforce monotonic progression rules (preventing false regressions unless rework/demolition is flagged) and handle occluded elements by locking their last high-confidence verified state when drywall closes up a wall.
