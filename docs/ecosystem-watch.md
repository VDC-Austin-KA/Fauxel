# Fauxel Ecosystem Watch

Baseline: 2026-11-20. Updated weekly by the "Fauxel – ecosystem watch" scheduled task. Newest entries at the top of the Change log.

## Platform baselines
| Dependency | Current baseline | Fauxel impact | Source |
|---|---|---|---|
| Revit API | 2027 runs on .NET 10. Autodesk is moving 2025/2026 from .NET 8 to .NET 10 (.NET 8 EOL Nov 10 2026) | Revit host targets net10. Watch whether 2025/2026 need dual builds | blog.autodesk.io Revit 2027 SDK; Autodesk forum ".NET 10 migration" |
| Navisworks API | .NET Framework 4.8. One binary per major version (bundle folders v22=2025, v23=2026) | Navisworks host targets net48, multi-version bundle | APS Navisworks publisher guidelines |
| Autodesk Construction Cloud | Renamed Autodesk Forma on Mar 24 2026. No URL, login or API changes | Single ACC/Forma adapter on existing APS endpoints | Autodesk construction blog |
| OpenVSLAM | Taken down over license concerns. Community successor stella_vslam (BSD-2), supports equirectangular | Default SLAM worker | stella-cv/stella_vslam |
| ORB-SLAM3 | GPLv3 | Optional isolated worker only | — |
| Insta360 OSC | Supported: ONE X, X2, R, RS, X3, X4, X4 Air, X5. Camera IP 192.168.42.1. Pro/Pro 2/Titan use a separate Pro Camera API | Phase 8 needs two camera drivers | Insta360 developer docs |
| Insta360 SDK | X4/X5 SDK does photo in-camera stitching but not video. X4 encoding and bitrate are only settable on the camera | Field guide must tell users to set H.265/H.264 on the camera | Insta360 developer FAQ |

## License matrix (verify before first release)
| Dependency | License (to verify) | Use |
|---|---|---|
| IfcOpenShell | LGPL-3.0 | IFC parse (dynamic link/process OK) |
| web-ifc | MPL-2.0 | Browser IFC |
| That Open Engine (@thatopen/components) | MIT | Viewer |
| Speckle server/SDKs | Apache-2.0 | Open model streaming |
| stella_vslam | BSD-2-Clause | SLAM |
| SAM 2 | Apache-2.0 | Segmentation foundation |
| Mask2Former | MIT | Segmentation |
| Open3D | MIT | Geometry |
| PyTorch3D | BSD-3 | Rendering |
| BlenderProc / Blender | GPL-3.0 | Synthetic data (isolated worker) |
| pupil-apriltags | BSD-2 (wraps AprilTag 3, BSD-2) | Fiducials |

## Change log
- 2026-11-20: Baseline created.
