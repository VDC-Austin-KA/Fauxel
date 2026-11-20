# Phase 3 — bim-projection-engine and synthetic data

> Original design brief. Blender/BlenderProc are GPL: they run as an isolated out-of-process worker (decision log).

Act as a 3D Graphics & Machine Learning Infrastructure Engineer. Build Fauxel's `bim-projection-engine` and synthetic dataset generator using Python, Open3D, IfcOpenShell/Trimesh, and PyTorch3D/BlenderProc.

**Implement two core capabilities:**
1. **Runtime 3D-to-2D Equirectangular Visibility & Occlusion Projection:**
   - Given a calibrated 6-DoF camera pose `(x, y, z, quaternion)` from Phase 2 and the triangulated 3D BIM geometry from Phase 1:
   - Perform spherical raycasting / Z-buffer rasterization into a 360° equirectangular depth buffer and instance-ID mask.
   - For every `bim_element` within a configurable radius (e.g., 15 meters), compute:
     - Projected 2D equirectangular bounding polygon and pixel mask.
     - **Occlusion Ratio:** Percentage of the element occluded by previously installed walls, slabs, or foreground objects (`0.0` = fully visible, `1.0` = completely hidden behind drywall).
     - **View Angle & Distance Quality Score** so downstream detectors ignore grazing-angle or far-field noise.

2. **Automated Synthetic 360° Training Data Generator (Domain Randomization):**
   - Build a headless Blender/Cycles or PyTorch3D pipeline that places virtual 360° cameras along simulated walking paths inside the project's BIM model.
   - Randomly toggle element installation states (e.g., bare studs vs. insulated vs. drywalled; bare slab vs. overhead MEP hangers + ductwork), randomize construction site textures (concrete dust, temporary LED string lights, scaffolding, clutter), and export paired `(RGB Equirectangular Image, Instance Segmentation Mask, Depth Map, JSON Element State Labels)`.
   - Include a fine-tuning script to adapt foundation segmentation models (e.g., **Mask2Former** / **SAM 2**) to a specific project's custom Revit families in under 2 hours.
