# Phase 7 — web-portal

> Original design brief. The That Open Engine (IFC) viewer is built first; the APS Viewer comes second.

Act as a Principal Frontend & WebGL Graphics Engineer. Build the Fauxel Web Application in React 19, TypeScript, Tailwind CSS, and Three.js.

**Build two primary workspaces with seamless navigation:**

1. **Split-Screen 3D Spatial & 360° Reality Viewer:**
   - **Dual Viewer Engine:** Support both **Autodesk APS Viewer SDK** (for native `.rvt`/`.nwd` cloud viewing) and open-source **That Open Engine (`web-ifc` + Three.js)** (for client-side IFC viewing) behind a unified `IViewerCanvas` wrapper.
   - **Synchronized Camera Rig:** Render the 360° equirectangular panorama sphere on the left and the 3D BIM model on the right. Rotating, panning, or zooming either view instantaneously updates the camera orientation and frustum cone in the other.
   - **Interactive AR Overlay Mode:** Allow toggling semi-transparent BIM wireframe/solid overlays directly onto the 360° photo, colored by installation status (Green = Installed, Yellow = In Progress, Red = Deviation, Gray = Not Started).
   - **Time-Travel & Floorplan Minimap:** Include an interactive 2D floorplan minimap showing the Insta360 walk path dots, a date scrubber to compare the exact same camera spot across past capture dates (side-by-side split wipe), and a 3-point manual alignment calibration tool.

2. **Executive Portfolio & Trade Analytics Dashboard:**
   - **KPI Summary Cards:** Overall % Complete, Project SPI, Earned Value vs. Planned Value, Open QC Deviations, and Projected Completion Date.
   - **Interactive Floor/Zone Heatmaps:** Color-coded 2D/3D floorplans showing trade completion percentages and bottleneck zones.
   - **Trade Breakdown & Burn-Rate Charts:** Interactive charts (Recharts/ECharts) displaying S-curves, weekly installation velocity by trade (Structural, Framing/Drywall, HVAC, Electrical, Plumbing/FP), and subcontractor pay-app verification tables with one-click drill-down into the 360° photo evidence.
