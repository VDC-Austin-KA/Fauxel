# Fauxel desktop connectors (Revit + Navisworks)

Plugins that talk to the Fauxel API (`docs/specs/platform-contract.md`): export model elements, and write verified field progress back into the model.

| Project | Target | Configs | Output |
|---|---|---|---|
| `dotnet-core/Fauxel.Connectors.Core` | netstandard2.0 | n/a | DTOs, `FauxelClient`, settings, `StatusPalette`, `TradeClassifier`, `Matrix4` |
| `dotnet-core/Fauxel.Connectors.Tests` | net10.0 | n/a | xUnit |
| `revit/Fauxel.Revit` | net8.0-windows (R25, R26), net10.0-windows (R27) | `R25 R26 R27` | `revit/Fauxel.Revit/bin/R25..R27` |
| `navisworks/Fauxel.Navisworks` | net48 | `N25 N26 N27` | `navisworks/Fauxel.bundle/Contents/v22,v23,v24` |

Reference packages (compile only, no Autodesk DLL is copied or committed): `Nice3point.Revit.Api.RevitAPI/RevitAPIUI/RevitAPIIFC` 2025.4.60 / 2026.4.10 / 2027.4.0; `Speckle.Navisworks.API` 2025.0.0 / 2026.0.1 / 2027.0.0; `Alexpivo394.Navis.Api.Autodesk.Navisworks.Timeliner` 2025.0.0 (the only published TimeLiner reference; used for all three years).

## Build

Linux or Windows, .NET SDK 10 and 8:

```
./build.sh                 # tests + Revit R25/R26/R27 + Navisworks N25/N26/N27 + bundle assembly
dotnet build revit/Fauxel.Revit -c R26        # a single year
dotnet build navisworks/Fauxel.Navisworks -c N27
```

## Install (Windows, per user, close the host first)

```
revit\deploy\Install-FauxelRevit.ps1 [-Years 2026,2027] [-Uninstall]
navisworks\Install-FauxelNavisworks.ps1 [-Uninstall]
```

Revit goes to `%APPDATA%\Autodesk\Revit\Addins\<year>\Fauxel\` plus `Fauxel.addin`. Navisworks goes to `%APPDATA%\Autodesk\ApplicationPlugins\Fauxel.bundle` (Nw22=2025, Nw23=2026, Nw24=2027; 2027 no longer loads the legacy Plugins folder).

## Use

1. **Settings** (Revit: Fauxel tab. Navisworks: Add-ins > External Tools > "Fauxel: Settings"): API URL, API key (sent as `X-Fauxel-Key`), project id. "Test connection" calls `/health` and `/projects/{id}`. Stored in `%APPDATA%\Fauxel\settings.json` (key in plain text).
2. **Revit**
   - *Export Model*: Structural, Architectural, MEP and Fire Protection categories with UniqueId, IfcGUID, level, room, family/type, trade, shared-coordinate AABB in meters, quantity (area for walls/floors/ceilings/roofs, length for linear elements, volume for columns/foundations, else count), levels, `shared_transform` (from `ProjectLocation.GetTotalTransform()`, translation in meters, row-major). POSTs to `/models/import`.
   - *Sync Progress*: GET `/writeback`; binds `Fauxel_PercentComplete, Fauxel_Status, Fauxel_LastVerified, Fauxel_Confidence, Fauxel_EvidenceUrl` (fixed GUIDs in `Fauxel_SharedParameters.txt`) as instance parameters; writes values matched by UniqueId then IfcGUID; colors the 3D view "Fauxel Progress" (solid fill by status) and creates schedule "Fauxel Progress Schedule"; POSTs the ack. One undo step (TransactionGroup).
   - *Clear Overrides*: resets the overrides in "Fauxel Progress".
3. **Navisworks** (Add-ins > External Tools)
   - *Export Selection Tree*: element nodes (geometry owners, or objects whose children own geometry) with ItemGuid, Revit UniqueId and IFC GlobalId when found in properties (matched by value pattern), AABB in meters, level, trade. POSTs `source: navisworks`.
   - *Sync TimeLiner*: GET `/timeliner`; matches tasks by DisplayId (then SynchronizationId, then name), copy-and-replace via `DocumentTimeliner.TaskEdit`, sets actual start/end and progress, lists the changes, acks.
   - *Apply Status Colors* / *Reset Colors*: permanent color overrides by status (not started also 50% transparent). Reset clears all permanent overrides in the document.

## Verified here (Linux, no Autodesk products)

- Every configuration compiles with 0 warnings and 0 errors: Core, Tests, Revit R25/R26/R27, Navisworks N25/N26/N27, and `Fauxel.Connectors.sln`.
- 41 xUnit tests pass: DTO round-trips against the contract JSON shapes, status palette, trade classifier, settings file, `FauxelClient` (URL building, auth header, retries, error messages), and the transform: SLAM point (2,3,0) gives Revit shared coordinates 1012.5583, 1986.0076, 504.7572 ft, identical to `python3 -m fauxel_capture.alignment` within 1e-3 ft.
- `PackageContents.xml` is well-formed; the Nw22/23/24 series codes and `NAVMAN|NAVSIM` platform follow published open-source bundles (Nw21=2024, Nw24=2027).

## Needs a live test on Windows (nothing below has executed)

- Revit 2025/2026/2027: ribbon loads, shared parameter binding and values, the "Fauxel Progress" view overrides and schedule fields, `GetTotalTransform` sign/convention against a model with a rotated survey point, ExporterIFCUtils fallback GUID, WinForms dialog.
- Navisworks 2025/2026/2027: bundle discovery with the Nw series codes, command visibility under Add-ins, TimeLiner `TaskEdit` behavior, the TimeLiner assembly resolve (reference is the 2025 build; `NavisCommon.Init` maps it to the installed assembly on 2026/2027), coexistence of the shipped `System.Text.Json` and its dependencies with assemblies Navisworks already loaded.
- Navisworks element detection and id extraction depend on how each source (Revit NWC, IFC, DWG) lays out the selection tree and properties; adjust `ModelTree.Walk/Describe` after inspecting a real federated model.
- Large models: export is a single POST (timeout from settings, default 120 s).
