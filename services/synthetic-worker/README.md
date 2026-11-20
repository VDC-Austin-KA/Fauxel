# Fauxel synthetic worker (GPL isolated)

Renders labelled 360 training data from an IFC and writes it in the dataset layout that `services/cv-inference` trains on.

**License note.** Blender is GPL-3.0 and is run strictly out of process (separate container / `blender -b` subprocess). `render_blender.py`
imports `bpy` and is therefore GPL; keep it and this worker out of any Apache-2.0 package. Nothing in `services/api` or `services/cv-inference`
imports this directory; the only coupling is the on-disk dataset format. `prestep.py` / `postprocess.py` / `run.py` use IfcOpenShell (LGPL),
numpy and OpenCV in a normal Python process. Record Blender and IfcOpenShell in `docs/ecosystem-watch.md`.

## Pipeline
1. `prestep.py` (IfcOpenShell): IFC -> `scene.obj` (one `o E<idx>` per product, world metres, Z up), `elements.json` (guid, class, trade, bbox), `spaces.json`.
2. `render_blender.py` (Blender 4.2 LTS, Cycles, CPU by default): per **capture** (one point in time) each labelled element gets a random rung of its trade ladder
   (`vision/ladders.py`), realised as geometry + procedural materials:
   - walls: hidden (0) / track (25) / studs (50) / studs + in-wall conduit + insulation (65) / board with sheet seams (80) / taped joints (95) / painted (100)
   - hvac, electrical, plumbing_fp: absent (0) / trapeze hangers + rods only (30) / element installed (80) / insulated duct or pipe wrap, devices (100)
   - structural beams/columns/slabs: absent (0) / formwork + rebar (40) / concrete (100)
   Randomised per capture: progress bias, materials (colours, dust/grime, noise), 1-3 lights (power, colour), world light, floor clutter.
   Then `--frames` virtual cameras (equirectangular panorama camera, 1.4-1.7 m, random yaw) walk through the spaces.
   Per frame: `rgb.png`, `id.png` (24-bit RGB = element id of the BIM meshes only, flat emission, no AA/dither/colour management; the same "visible BIM pixels" mask the API projection gives),
   `depth_*.png` (uint16 mm, 65535 = no hit), plus `capture.json` (labels: guid, trade, percent, state, camera pose).
3. `postprocess.py`: for each element visible with >= 400 px cuts the tangent-plane crop identical to `GET /training/crop` (`fauxel_cv.crops`, parity-tested against
   `model_slot.crop_for_mask`) into `<out>/dataset/<trade>/<split>/p<percent>/<capture>_<frame>_<guid>.jpg` + `samples.jsonl` + `ladder.json`. Split is by capture.

## Run
```bash
docker build -f services/synthetic-worker/Dockerfile -t fauxel-synthetic-worker .
python3 services/synthetic-worker/make_test_ifc.py model.ifc              # optional tiny test model
docker run --rm -v $PWD/model.ifc:/in/model.ifc:ro -v $PWD/out:/out fauxel-synthetic-worker \
    --ifc /in/model.ifc --out /out --captures 8 --frames 10 --width 1024 --height 512 --samples 16
python -m fauxel_cv.train --data out/dataset --trade hvac --out models/   # services/cv-inference
```
Without Docker: install Blender 4.2, `pip install ifcopenshell numpy opencv-python-headless requests`, then `BLENDER=/path/to/blender python3 run.py --ifc model.ifc --out out`.
GPU: set `sc.cycles.device` in `render_blender.py` and use the NVIDIA container runtime (not needed for 1024x512 at 16 samples).

## Limitations
- Wall/MEP geometry is built from each element's oriented footprint (PCA on XY) and height: straight walls and linear MEP only; curved or sloped elements render as plain context (unlabelled).
- Non-wall `arch_interior` classes, `other` trade and non-beam/column/slab structural classes are rendered as context and not labelled.
- Domain gap to real Insta360/OpenSpace footage is large: use this to pre-train/bootstrap rungs, then fine-tune on human-verified real crops.
- The 65 % wall rung (MEP rough + insulation) and 95 % (taped) are visually subtle here too; expect them to be the weakest classes.
