# Fauxel quickstart: from zero to progress in Revit

Target: KA's Windows PC (NVIDIA GPU, under 12 GB VRAM) runs the database, API and processing in Docker; Cloudflare serves the portal at `https://fauxel.ackerworks.com` and reaches the PC only through a Tunnel.

## One command (recommended)

Put `Start-Fauxel.ps1` (repo root) in `C:\Fauxel`, keep your `.env` in `C:\Fauxel\infra\.env` (or `C:\Fauxel\.env`; it gets moved), then:

```powershell
cd C:\Fauxel
powershell -ExecutionPolicy Bypass -File .\Start-Fauxel.ps1                 # install, update, build, start, key, project, sample IFC
powershell -ExecutionPolicy Bypass -File .\Start-Fauxel.ps1 -Camera         # on the ONE X2 Wi-Fi: check the camera
powershell -ExecutionPolicy Bypass -File .\Start-Fauxel.ps1 -Capture D:\DCIM # upload a walk
powershell -ExecutionPolicy Bypass -File .\Start-Fauxel.ps1 -Cloudflare     # guided Tunnel, Worker, Pages, Access (R2 optional)
powershell -ExecutionPolicy Bypass -File .\Start-Fauxel.ps1 -PublicToday    # anyone with the link can view (read-only) until 23:59; see infra/cloudflare/access-policy.md
powershell -ExecutionPolicy Bypass -File .\Start-Fauxel.ps1 -DeployWeb -SkipPlugins   # after a pull: rebuild the portal, redeploy Pages + Worker (no prompts)
```

It is safe to re-run; it reuses the API key and project in `%APPDATA%\Fauxel\settings.json`. Sections 1 to 5 below are the manual equivalent.

## 1. Install on the PC (once)

| Tool | Install |
|---|---|
| WSL2 | `wsl --install` (reboot) |
| Docker Desktop | WSL2 backend; Settings › Resources: 12+ GB memory. GPU support comes with a current NVIDIA driver |
| Git | `winget install Git.Git` |
| .NET SDK 10 and 8 (to build the plugins) | `winget install Microsoft.DotNet.SDK.10` and `winget install Microsoft.DotNet.SDK.8` |
| Node.js 22 (to deploy the portal to Cloudflare) | `winget install OpenJS.NodeJS.LTS` |
| cloudflared | `winget install Cloudflare.cloudflared` |

## 2. Get the code and your private settings

```powershell
git clone https://github.com/VDC-Austin-KA/Fauxel.git C:\Fauxel     # already cloned the wip branch? run: cd C:\Fauxel; git checkout main; git pull
cd C:\Fauxel\infra
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
```

Fill in only what you have; leave `CHANGE_ME` values alone (step 3 generates them):

| Line | Value |
|---|---|
| `FAUXEL_APS_CLIENT_ID` / `FAUXEL_APS_CLIENT_SECRET` | From your APS app (Traditional Web App) |
| `FAUXEL_S3_*` | Leave the local defaults (section 5 explains the two production options) |

`infra\.env` is ignored by git: it never leaves the PC.

## 3. Build the plugins and start the stack

```powershell
cd C:\Fauxel
.\connectors\Build-Connectors.ps1                       # Revit 2025-2027 + Navisworks 2025-2027
.\scripts\windows\Setup-Fauxel.ps1 -InstallDir C:\Fauxel -SkipClone
```

Setup checks WSL2, Docker, the GPU and git, generates the missing secrets, starts the containers (with the GPU override when an NVIDIA GPU is present), waits for health, installs the Revit add-in and the Navisworks bundle, and prints a summary table. Re-running it is safe.

Local check: open `http://localhost:8080` (portal) and `http://localhost:8000/api/v1/health` (API: `{"status":"ok","db":true,"storage":true,"gpu":true}`).
Create your first API key (the admin token is in `infra\.env` as `FAUXEL_ADMIN_TOKEN`):

```powershell
$t = (Select-String -Path C:\Fauxel\infra\.env -Pattern '^FAUXEL_ADMIN_TOKEN=(.*)').Matches.Groups[1].Value
Invoke-RestMethod -Method Post http://localhost:8000/api/v1/api-keys -Headers @{ 'X-Fauxel-Admin' = $t } -ContentType 'application/json' -Body '{"name":"KA"}'
```

Keep the returned key: the portal, plugins, phone app and CLI use it.

## 4. Put it on fauxel.ackerworks.com (Cloudflare)

Follow these in order; each file has the exact commands:

1. Tunnel: `infra/cloudflare/tunnel/` — create tunnel `fauxel`, public hostname `fauxel-origin.ackerworks.com` → `http://localhost:8000` (`http://api:8000` when cloudflared runs in Docker: `docker compose --profile tunnel up -d cloudflared` with `CLOUDFLARE_TUNNEL_TOKEN` in `infra\.env`), then re-run setup with `-TunnelToken <token> -InstallCloudflaredService` in an admin PowerShell. Without R2 (section 5) add `fauxel-files.ackerworks.com` → `http://s3:9000`.
2. Access: `infra/cloudflare/access-policy.md` — app `Fauxel portal` (your email), app `Fauxel origin` (service token only), a Bypass for exactly `/api/v1/integrations/aps/callback` and `/api/v1/integrations/aps/webhook`, and one service token for machine clients (plugins, phone app, CLI).
3. Worker: `infra/cloudflare/worker/` — `npx wrangler deploy` (routes `/api/*` and `/media/*`; with `FAUXEL_MEDIA=local` set, `/api/*` only and no R2 binding), then `wrangler secret put` the origin service token.
4. Portal: `infra/cloudflare/pages.md` — `npx wrangler pages deploy`, custom domain `fauxel.ackerworks.com`.
5. Re-run setup with `-Production` so the API trusts the Access login header.

## 5. Media storage: this PC (free) or R2

**This PC (default, no extra Cloudflare service):** files stay in the stack's S3 storage (`s3` container). Browsers and phones upload to it through the tunnel at `fauxel-files.ackerworks.com` with presigned URLs, so the bucket stays private (no Access app on that hostname; anonymous requests get 403). Set `FAUXEL_S3_PUBLIC_ENDPOINT=https://fauxel-files.ackerworks.com` in `infra\.env`, add the tunnel hostname from section 4, deploy the Worker with `FAUXEL_MEDIA=local`, re-run setup. `Start-Fauxel.ps1 -Cloudflare` does this when you leave the R2 account id empty. Trade-offs: the PC must be on, and uploads made on the PC itself also go out through Cloudflare and back.

**R2 (optional, needs R2 enabled on the Cloudflare account):** create bucket `fauxel-media`, apply `infra/cloudflare/r2-cors.json`, create an R2 API token, then set in `infra\.env`: `FAUXEL_S3_ENDPOINT` and `FAUXEL_S3_PUBLIC_ENDPOINT` = `https://<account-id>.r2.cloudflarestorage.com`, `FAUXEL_S3_ADDRESSING=path`, `FAUXEL_S3_REGION=auto`, the token's access key and secret. Re-run setup. Large uploads then go straight from the browser or phone to R2, around Cloudflare's 100 MB request limit.

## 6. Autodesk Forma (ACC)

APS app: Traditional Web App, callback `https://fauxel.ackerworks.com/api/v1/integrations/aps/callback`, APIs: Data Management, Model Derivative, Autodesk Forma (ACC), BIM 360, Webhooks, AEC Data Model. In Forma, pick your hub, open Hub Admin › Custom Integrations and add the Client ID (needs hub admin rights; Fauxel works without this step through IFC + the Revit plugin). Then in the portal: Settings › Integrations › Connect Autodesk Forma.

## 7. First progress cycle

No data yet? `scripts/demo/make_demo.py` builds three demo projects, each with six discipline IFC models (A, S, M, E, P, FP) federated in the 3D view, a P6 schedule with WBS and costs around today, field progress that follows it, and QC deviations: **Bastrop Data Center - Hall A** (data hall, rack pods, CRAHs, switchgear/UPS, generators, pre-action), **Cedar Park Medical Office Building** (3 storeys, 72 exam rooms) and **Riverside Lofts - Building 2** (5-storey podium multifamily, 48 units). Run commands are at the top of the file; `--replace` rebuilds, `--delete` removes them, `--ifc-only DIR` just writes the IFC + XER files.

1. Portal › Uploads: the IFC model (or Revit › Fauxel › Export Model).
2. Place 4+ AprilTags per level (print sheets: `python -m fauxel_capture.tags sheet 101 102 103 104 -o tags`), survey their BIM coordinates.
3. Walk with the ONE X2 (5.7K video); upload the raw `.insv` or the Insta360 Studio export in Uploads (or the phone app).
4. Reality viewer: align (tags, or the 3-point tool), then Process. Low-confidence proposals land in Verification; confirm them (keys 1-5, Enter).
   3D model: filter by level and trade; color by installation status, current condition (built today, by trade), or schedule (verified % vs. each element's activity plan, with an as-of date and an on-time range). Click an element for its activity, variance in days, and its Navisworks / Revit / IFC ids.
5. Revit › Fauxel › Settings (API URL `https://fauxel.ackerworks.com`, API key, project id, Access service token) › Sync Progress: shared parameters filled, "Fauxel Progress" 3D view colored. Navisworks › Fauxel › Sync TimeLiner / Apply Status Colors.

## Status of live verification

Built and tested in Linux containers here: every suite in `scripts/check.sh` and a full Docker stack smoke test (`infra/smoke-test.sh`, 15/15).
Running on KA's PC (2026-11-20): the Docker stack on the GPU (GTX 1070), the plugins built (Revit/Navisworks 2025-2027), and fauxel.ackerworks.com live (Tunnel, Access, Worker, Pages; media on the PC through the tunnel, no R2). Not yet run on real hardware: the plugins inside Revit/Navisworks, the ONE X2 over Wi-Fi, OpenSpace (needs your API docs), and a live APS sign-in.
