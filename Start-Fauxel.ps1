<#
.SYNOPSIS
  One command to get Fauxel running on this PC (and, with -Cloudflare, on https://fauxel.<domain>).
  Run from C:\Fauxel in PowerShell:   powershell -ExecutionPolicy Bypass -File .\Start-Fauxel.ps1

.DESCRIPTION
  Local (default, safe to re-run any time):
    1. Prerequisites: WSL2, Docker Desktop, Git, .NET SDK 10 + 8 (installs what is missing with winget)
    2. Updates this checkout to the latest main (keeps infra\.env, which git ignores)
    3. Builds the Revit 2025-2027 and Navisworks 2025-2027 plugins
    4. Runs scripts\windows\Setup-Fauxel.ps1: secrets, docker compose (GPU when present), health, plugin install
    5. Creates an API key + a project, writes %APPDATA%\Fauxel\settings.json for the plugins and the tools
    6. Loads a public sample IFC into the project if it has no model yet, and checks elements were created
  -Camera            Connects to the Insta360 ONE X2 over its Wi-Fi (join the camera's Wi-Fi first) and shows its state
  -Capture <paths>   Uploads ONE X2 files or folders (.insv/.insp raw, or Insta360 Studio JPG/MP4 exports) to the project
  -Cloudflare        Guided production setup for https://fauxel.<domain>: Tunnel, Worker, Pages, Access; media stays on this PC
                     (served through the tunnel at fauxel-files.<domain>, free) or goes to R2 (optional)

.EXAMPLE
  .\Start-Fauxel.ps1
  .\Start-Fauxel.ps1 -Camera
  .\Start-Fauxel.ps1 -Capture D:\DCIM\Camera01
  .\Start-Fauxel.ps1 -Cloudflare
#>
[CmdletBinding()]
param(
  [switch]$Cloudflare,
  [switch]$Camera,
  [string[]]$Capture,
  [string]$ProjectName = 'Fauxel Pilot',
  [string]$Domain = 'ackerworks.com',
  [switch]$SkipPrereqs,
  [switch]$SkipPlugins,
  [switch]$SkipUpdate,
  [switch]$SkipStack     # stack already running: only key/project/settings/sample/camera/capture
)
$ErrorActionPreference = 'Continue'   # native tools (git, docker, wrangler) write progress to stderr
$Root = $PSScriptRoot
Set-Location $Root
$EnvFile = Join-Path $Root 'infra/.env'
$SettingsPath = Join-Path $env:APPDATA 'Fauxel/settings.json'
$Results = New-Object System.Collections.Generic.List[object]

function Say([string]$m) { Write-Host "`n== $m" -ForegroundColor Cyan }
function Note([string]$step, [string]$status, [string]$detail = '') {
  $Results.Add([pscustomobject]@{ Step = $step; Status = $status; Detail = $detail }) | Out-Null
  $c = @{ OK = 'Green'; WARN = 'Yellow'; FAIL = 'Red'; TODO = 'Magenta'; SKIP = 'DarkGray' }[$status]
  Write-Host ("[{0,-4}] {1}  {2}" -f $status, $step, $detail) -ForegroundColor $c
}
function Finish([int]$code = 0) {
  Write-Host "`n================ Fauxel summary ================" -ForegroundColor Cyan
  $Results | Format-Table -AutoSize -Wrap | Out-String | Write-Host
  exit $code
}
function Has([string]$cmd) { [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }
function Refresh-Path {
  $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
}
function Is-Admin { ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator) }
function Winget-Install([string]$id, [string]$label) {
  if (-not (Has 'winget')) { Note $label 'FAIL' "winget missing: install '$label' manually"; return $false }
  Write-Host "   installing $label ($id) ..."
  winget install --id $id -e --silent --accept-source-agreements --accept-package-agreements | Out-Host
  Refresh-Path
  return $true
}
function Get-EnvValue([string]$key) {
  if (-not (Test-Path $EnvFile)) { return $null }
  $m = Select-String -Path $EnvFile -Pattern ("^{0}=(.*)$" -f [regex]::Escape($key)) | Select-Object -First 1
  if ($m) { return $m.Matches[0].Groups[1].Value.Trim() } else { return $null }
}
function Set-EnvValue([string]$key, [string]$value) {
  $lines = [System.Collections.Generic.List[string]](Get-Content $EnvFile)
  $i = -1
  for ($n = 0; $n -lt $lines.Count; $n++) { if ($lines[$n] -match ("^{0}=" -f [regex]::Escape($key))) { $i = $n; break } }
  if ($i -ge 0) { $lines[$i] = "$key=$value" } else { $lines.Add("$key=$value") }
  [IO.File]::WriteAllText($EnvFile, (($lines -join "`n") + "`n"))   # UTF-8 without BOM, LF line endings
}
function Can-Prompt { -not [Console]::IsInputRedirected }
function Read-Secret([string]$prompt) {
  if (-not (Can-Prompt)) { return '' }
  $s = Read-Host -AsSecureString $prompt
  [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($s))
}
function Api([string]$method, [string]$path, $body = $null, [hashtable]$headers = @{}) {
  $p = @{ Method = $method; Uri = "$ApiBase$path"; Headers = $headers; TimeoutSec = 120; ErrorAction = 'Stop'; UseBasicParsing = $true }
  if ($null -ne $body) { $p.Body = ($body | ConvertTo-Json -Depth 8); $p.ContentType = 'application/json' }
  Invoke-RestMethod @p
}
function ApiList([string]$path, [hashtable]$headers) { @((Api 'GET' $path $null $headers) | ForEach-Object { $_ } | Where-Object { $null -ne $_ }) }   # [] -> 0 items on PS 5.1 and 7
function Run-Child([string]$file, [string[]]$argList) {
  $exe = (Get-Process -Id $PID).Path
  & $exe -NoProfile -ExecutionPolicy Bypass -File $file @argList | Out-Host
  return $LASTEXITCODE
}

# ------------------------------------------------------------------ 1. prerequisites
if (-not $SkipPrereqs) {
  Say 'Prerequisites'
  $wsl = & wsl.exe --status 2>&1; $wslOk = ($LASTEXITCODE -eq 0)
  if ($wslOk) { Note 'WSL2' 'OK' } else {
    if (Is-Admin) { wsl.exe --install --no-distribution | Out-Host; Note 'WSL2' 'TODO' 'installed: REBOOT, then run this script again'; Finish 1 }
    Note 'WSL2' 'TODO' 'run once in an ADMIN PowerShell:  wsl --install --no-distribution   then reboot and re-run'; Finish 1
  }
  if (-not (Has 'git')) { Winget-Install 'Git.Git' 'Git' | Out-Null }
  if (Has 'git') { Note 'Git' 'OK' } else { Note 'Git' 'FAIL' 'open a new terminal after install and re-run'; Finish 1 }

  if (-not (Has 'docker')) {
    Winget-Install 'Docker.DockerDesktop' 'Docker Desktop' | Out-Null
    Note 'Docker Desktop' 'TODO' 'installed: sign out/in (or reboot), start Docker Desktop once, accept its terms, then re-run'; Finish 1
  }
  docker info --format '{{.ServerVersion}}' *> $null
  if ($LASTEXITCODE -ne 0) {
    $dd = Join-Path $env:ProgramFiles 'Docker/Docker/Docker Desktop.exe'
    if (Test-Path $dd) { Write-Host '   starting Docker Desktop ...'; Start-Process $dd }
    $t = 0; do { Start-Sleep 5; $t += 5; docker info --format '{{.ServerVersion}}' *> $null } until ($LASTEXITCODE -eq 0 -or $t -ge 240)
  }
  if ($LASTEXITCODE -eq 0) { Note 'Docker Desktop' 'OK' (docker info --format 'engine {{.ServerVersion}}, {{.MemTotal}} bytes RAM') }
  else { Note 'Docker Desktop' 'FAIL' 'not running: open Docker Desktop, wait for "Engine running", re-run'; Finish 1 }

  if (-not $SkipPlugins) {
    $sdks = if (Has 'dotnet') { (dotnet --list-sdks) -join ' ' } else { '' }
    if ($sdks -notmatch '(^|\s)10\.') { Winget-Install 'Microsoft.DotNet.SDK.10' '.NET SDK 10' | Out-Null }
    if ($sdks -notmatch '(^|\s)8\.') { Winget-Install 'Microsoft.DotNet.SDK.8' '.NET SDK 8' | Out-Null }
    if (Has 'dotnet') { Note '.NET SDK' 'OK' ((dotnet --list-sdks) -join '; ') } else { Note '.NET SDK' 'WARN' 'not found: plugins will be skipped (open a new terminal and re-run)' }
  }
  if (($Capture -or $Camera) -and -not (Has 'py') -and -not (Has 'python')) { Winget-Install 'Python.Python.3.12' 'Python 3.12' | Out-Null }
  if ($Cloudflare -and -not (Has 'node')) { Winget-Install 'OpenJS.NodeJS.LTS' 'Node.js LTS' | Out-Null }
}

# ------------------------------------------------------------------ 2. code
if (-not $SkipUpdate) {
  Say 'Code'
  $repoUrl = 'https://github.com/VDC-Austin-KA/Fauxel.git'
  if (-not (Test-Path (Join-Path $Root '.git'))) {
    # Bootstrap: this folder only holds Start-Fauxel.ps1 (and maybe .env). Untracked files such as .env are kept.
    git init -q -b main; git remote add origin $repoUrl
    git -c core.autocrlf=false fetch --quiet origin main
    if ($LASTEXITCODE -ne 0) { Note 'Repository' 'FAIL' "cannot fetch $repoUrl (signed in to GitHub? try: git ls-remote $repoUrl)"; Finish 1 }
    git config core.autocrlf false
    git checkout -q -f -B main origin/main
    Note 'Repository' 'OK' ("cloned main @ " + (git rev-parse --short HEAD))
  } else {
    $dirty = git status --porcelain --untracked-files=no
    if ($dirty) { Note 'Repository' 'WARN' 'local edits found: not updating (commit or discard them to get the latest main)' }
    else {
      git fetch --quiet origin main
      git checkout -q -B main origin/main
      # Line endings: .gitattributes keeps container scripts LF; re-checkout once so an old CRLF checkout is fixed.
      git config core.autocrlf false
      if ([IO.File]::ReadAllText((Join-Path $Root 'infra/smoke-test.sh')).Contains("`r`n")) { git rm -rq --cached . ; git reset -q --hard HEAD }
      Note 'Repository' 'OK' ("main @ " + (git rev-parse --short HEAD))
    }
  }
}

# ------------------------------------------------------------------ 3. settings file
Say 'Settings (infra\.env)'
$rootEnv = Join-Path $Root '.env'
if (-not (Test-Path $EnvFile) -and (Test-Path $rootEnv)) { Move-Item $rootEnv $EnvFile; Note 'infra/.env' 'OK' 'moved C:\Fauxel\.env to infra\.env' }
if (-not (Test-Path $EnvFile)) { Copy-Item (Join-Path $Root 'infra/.env.example') $EnvFile; Note 'infra/.env' 'OK' 'created from the template' }
else {
  $added = 0   # add any setting the template gained since this .env was made; existing values are never touched
  foreach ($l in Get-Content (Join-Path $Root 'infra/.env.example')) {
    if ($l -match '^([A-Z0-9_]+)=(.*)$' -and $null -eq (Get-EnvValue $Matches[1])) { Set-EnvValue $Matches[1] $Matches[2]; $added++ }
  }
  Note 'infra/.env' 'OK' ("found (gitignored)" + $(if ($added) { ", $added new setting(s) added" } else { '' }))
}
if (-not (Get-EnvValue 'FAUXEL_APS_CLIENT_ID')) { Note 'Autodesk APS' 'SKIP' 'FAUXEL_APS_CLIENT_ID empty: Forma connector off (everything else works)' }
elseif (-not (Get-EnvValue 'FAUXEL_APS_CLIENT_SECRET')) {
  $sec = Read-Secret 'APS Client Secret (typed here, saved only to infra\.env; Enter to skip)'
  if ($sec) { Set-EnvValue 'FAUXEL_APS_CLIENT_SECRET' $sec; Note 'Autodesk APS' 'OK' 'client id + secret set' } else { Note 'Autodesk APS' 'SKIP' 'no secret entered' }
} else { Note 'Autodesk APS' 'OK' 'client id + secret set' }

# ------------------------------------------------------------------ 4. plugins
if (-not $SkipPlugins -and (Has 'dotnet')) {
  Say 'Revit + Navisworks plugins'
  if ((Run-Child (Join-Path $Root 'connectors/Build-Connectors.ps1') @('-SkipTests')) -eq 0) { Note 'Plugin build' 'OK' 'Revit R25-R27, Navisworks bundle v22-v24' }
  else { Note 'Plugin build' 'WARN' 'build failed: see output above (stack setup continues)' }
}

# ------------------------------------------------------------------ 5. stack
Say 'Stack (docker compose)'
$setupArgs = @('-InstallDir', $Root, '-SkipClone', '-Domain', $Domain)
if ($SkipPlugins) { $setupArgs += '-SkipPlugins' }
if ((Get-EnvValue 'FAUXEL_PUBLIC_URL') -like 'https://*') { $setupArgs += '-Production' }
$rc = if ($SkipStack) { 0 } else { Run-Child (Join-Path $Root 'scripts/windows/Setup-Fauxel.ps1') $setupArgs }
$ApiPort = Get-EnvValue 'API_PORT'; if (-not $ApiPort) { $ApiPort = '8000' }
$PortalPort = Get-EnvValue 'PORTAL_PORT'; if (-not $PortalPort) { $PortalPort = '8080' }
$ApiBase = "http://127.0.0.1:$ApiPort/api/v1"
try { $h = Api 'GET' '/health'; Note 'API health' 'OK' ("db={0} storage={1} gpu={2}" -f $h.db, $h.storage, $h.gpu) }
catch { Note 'API health' 'FAIL' "setup exit $rc; check: docker compose -f infra/docker-compose.yml logs api"; Finish 1 }

# ------------------------------------------------------------------ 6. key, project, sample model
Say 'API key + project'
$cfg = $null
if (Test-Path $SettingsPath) { try { $cfg = Get-Content $SettingsPath -Raw | ConvertFrom-Json } catch { $cfg = $null } }
$key = if ($cfg) { $cfg.api_key } else { $null }
$ok = $false
if ($key) { try { Api 'GET' '/projects' $null @{ 'X-Fauxel-Key' = $key } | Out-Null; $ok = $true } catch { $ok = $false } }
if (-not $ok) {
  $admin = Get-EnvValue 'FAUXEL_ADMIN_TOKEN'
  $k = Api 'POST' '/api-keys' @{ name = "$env:COMPUTERNAME $(Get-Date -Format yyyy-MM-dd)" } @{ 'X-Fauxel-Admin' = $admin }
  $key = $k.key; Note 'API key' 'OK' 'created'
} else { Note 'API key' 'OK' 'reused' }
$H = @{ 'X-Fauxel-Key' = $key }
$projects = ApiList '/projects' $H
$proj = $null
if ($cfg -and $cfg.project_id) { $proj = $projects | Where-Object { $_.id -eq $cfg.project_id } | Select-Object -First 1 }
if (-not $proj) { $proj = $projects | Where-Object { $_.name -eq $ProjectName } | Select-Object -First 1 }
if (-not $proj) { $proj = Api 'POST' '/projects' @{ name = $ProjectName; description = 'Created by Start-Fauxel.ps1' } $H; Note 'Project' 'OK' "created '$ProjectName'" }
else { Note 'Project' 'OK' "'$($proj.name)'" }
$ProjectId = $proj.id

$out = [ordered]@{
  api_url = "http://localhost:$ApiPort"; api_key = $key; project_id = $ProjectId; timeout_seconds = 120
  cf_access_client_id = $(if ($cfg) { $cfg.cf_access_client_id } else { '' })
  cf_access_client_secret = $(if ($cfg) { $cfg.cf_access_client_secret } else { '' })
}
New-Item -ItemType Directory -Force -Path (Split-Path $SettingsPath) | Out-Null
[IO.File]::WriteAllText($SettingsPath, ($out | ConvertTo-Json))
Note 'Plugin settings' 'OK' "$SettingsPath (Revit/Navisworks read this)"

$models = ApiList "/projects/$ProjectId/models" $H
if ($models.Count -eq 0) {
  Say 'Sample model'
  $ifc = Join-Path $env:TEMP 'fauxel-sample.ifc'
  $url = 'https://media.githubusercontent.com/media/buildingsmart-community/Community-Sample-Test-Files/main/IFC%204.0.2.1%20(IFC%204)/ISO%20Spec%20archive/wall-with-opening-and-window.ifc'
  try {
    Invoke-WebRequest -Uri $url -OutFile $ifc -UseBasicParsing -ErrorAction Stop
    $r = curl.exe -s -S -f -H "X-Fauxel-Key: $key" -F "file=@$ifc;type=application/octet-stream" "$ApiBase/projects/$ProjectId/models"
    if ($LASTEXITCODE -ne 0) { throw "upload failed: $r" }
    $t = 0; do { Start-Sleep 3; $t += 3; $els = ApiList "/projects/$ProjectId/elements?limit=5" $H } until ($els.Count -gt 0 -or $t -ge 120)
    if ($els.Count -gt 0) { Note 'Sample IFC' 'OK' "buildingSMART wall-with-window ingested ($($els.Count)+ elements)" } else { Note 'Sample IFC' 'WARN' 'uploaded, no elements yet (check the worker logs)' }
  } catch { Note 'Sample IFC' 'WARN' "skipped: $($_.Exception.Message)" }
} else { Note 'Model' 'OK' "$($models.Count) model(s) in the project" }

# ------------------------------------------------------------------ tools venv (camera + capture upload)
function Ensure-Tools {
  $venv = Join-Path $Root '.venv-tools'
  $py = Join-Path $venv 'Scripts/python.exe'
  if (-not (Test-Path $py)) {
    $base = if (Has 'py') { 'py' } elseif (Has 'python') { 'python' } else { $null }
    if (-not $base) { Note 'Python' 'FAIL' 'install Python 3.12 (winget install Python.Python.3.12), open a new terminal, re-run'; Finish 1 }
    if ($base -eq 'py') { & py -3 -m venv $venv } else { & python -m venv $venv }
  }
  & $py -m pip install -q --upgrade pip | Out-Null
  & $py -m pip install -q -e (Join-Path $Root 'tools/insta360-osc') | Out-Null
  return (Join-Path $venv 'Scripts/fauxel-osc.exe')
}

if ($Camera) {
  Say 'Insta360 ONE X2 (join the camera's Wi-Fi first; its name and password are in the camera's Wi-Fi settings)'
  $osc = Ensure-Tools
  & $osc info; $a = $LASTEXITCODE
  & $osc state
  if ($a -eq 0) { Note 'Camera' 'OK' 'ONE X2 answered over OSC' } else { Note 'Camera' 'WARN' 'no answer at 192.168.42.1: is this PC on the camera Wi-Fi?' }
  Write-Host '   capture preset:  fauxel-osc set-options --mode video     then  start-video / stop-video --download .\captures'
}

if ($Capture) {
  Say 'Capture upload'
  $osc = Ensure-Tools
  $env:FAUXEL_API_URL = "http://localhost:$ApiPort"; $env:FAUXEL_API_KEY = $key
  & $osc upload @Capture --project $ProjectId --camera-model insta360_one_x2
  if ($LASTEXITCODE -eq 0) { Note 'Capture upload' 'OK' 'uploaded: raw .insv/.insp are stitched by the worker; open the portal Reality viewer' }
  else { Note 'Capture upload' 'FAIL' 'see the output above' }
}

# ------------------------------------------------------------------ 7. Cloudflare (guided)
if ($Cloudflare) {
  Say "Cloudflare: https://fauxel.$Domain"
  if (-not (Has 'npx')) { Note 'Node.js' 'FAIL' 'open a new terminal (Node was just installed) and re-run with -Cloudflare'; Finish 1 }
  Write-Host @"
   Dashboard steps first (one time, https://one.dash.cloudflare.com):
   A. Networks > Tunnels > Create a tunnel (Cloudflared) named "fauxel". Copy its token.
      Public hostname: fauxel-origin.$Domain  ->  Service HTTP  api:8000
      Without R2 (step D skipped) also: fauxel-files.$Domain  ->  Service HTTP  s3:9000   (no Access app on it: uploads use signed URLs)
   B. Access > Service credentials > Service Tokens > Create "fauxel-clients" (plugins, phone app, CLI) and "fauxel-worker". Copy both ids + secrets.
   C. Access > Applications (see infra\cloudflare\access-policy.md):
        - "Fauxel portal"  fauxel.$Domain       Allow: your email; Service Auth: fauxel-clients
        - "Fauxel origin"  fauxel-origin.$Domain Service Auth: fauxel-worker only
        - Bypass: fauxel.$Domain/api/v1/integrations/aps/callback and /api/v1/integrations/aps/webhook
   D. (Optional, R2 must be enabled on the account) R2 > Create bucket "fauxel-media". R2 > Manage API tokens > Create (Object Read & Write, that bucket). Copy account id, key id, secret.
"@ -ForegroundColor Gray
  Read-Host '   Press Enter once A-D are done' | Out-Null

  $tok = Read-Secret 'Tunnel token (step A)'
  if ($tok) { Set-EnvValue 'CLOUDFLARE_TUNNEL_TOKEN' $tok; docker compose --env-file $EnvFile -f infra/docker-compose.yml --profile tunnel up -d cloudflared | Out-Host; Note 'Tunnel' 'OK' "cloudflared container -> fauxel-origin.$Domain" }

  $acct = Read-Host 'R2 account id (step D; Enter = no R2, keep media on this PC)'
  $env:FAUXEL_MEDIA = $(if ($acct) { '' } else { 'local' })   # render-config.mjs drops the R2 binding + /media route
  if (-not $acct) {
    Set-EnvValue 'FAUXEL_S3_PUBLIC_ENDPOINT' "https://fauxel-files.$Domain"
    Note 'Media' 'OK' "stays on this PC; browsers/phones upload via https://fauxel-files.$Domain (tunnel hostname from step A)"
  }
  if ($acct) {
    $ak = Read-Secret 'R2 access key id'; $sk = Read-Secret 'R2 secret access key'
    $r2 = "https://$acct.r2.cloudflarestorage.com"
    foreach ($kv in @(@('FAUXEL_S3_ENDPOINT', $r2), @('FAUXEL_S3_PUBLIC_ENDPOINT', $r2), @('FAUXEL_S3_ADDRESSING', 'path'), @('FAUXEL_S3_REGION', 'auto'),
                      @('FAUXEL_S3_BUCKET', 'fauxel-media'), @('FAUXEL_S3_ACCESS_KEY', $ak), @('FAUXEL_S3_SECRET_KEY', $sk))) { Set-EnvValue $kv[0] $kv[1] }
    $rules = Get-Content (Join-Path $Root 'infra/cloudflare/r2-cors.json') -Raw
    $corsFile = Join-Path $env:TEMP 'fauxel-r2-cors.json'
    [IO.File]::WriteAllText($corsFile, '{"CORSRules": ' + $rules + '}')
    docker run --rm -e AWS_ACCESS_KEY_ID=$ak -e AWS_SECRET_ACCESS_KEY=$sk -e AWS_DEFAULT_REGION=auto -v "${env:TEMP}:/w" amazon/aws-cli `
      s3api put-bucket-cors --bucket fauxel-media --cors-configuration file:///w/fauxel-r2-cors.json --endpoint-url $r2 | Out-Host
    if ($LASTEXITCODE -eq 0) { Note 'R2' 'OK' 'bucket CORS set; media now goes to R2' } else { Note 'R2' 'WARN' 'CORS not set: check the token has write access to fauxel-media' }
  }

  Push-Location (Join-Path $Root 'infra/cloudflare/worker')
  $env:FAUXEL_DOMAIN = $Domain
  npm ci --silent; node render-config.mjs
  npx wrangler login
  npx wrangler deploy
  Write-Host '   Paste the "fauxel-worker" service token (step B) when asked:'
  npx wrangler secret put ORIGIN_ACCESS_CLIENT_ID
  npx wrangler secret put ORIGIN_ACCESS_CLIENT_SECRET
  Pop-Location
  if ($LASTEXITCODE -eq 0) { Note 'Worker' 'OK' "routes fauxel.$Domain/api/* and /media/*" } else { Note 'Worker' 'WARN' 'see wrangler output' }

  Push-Location (Join-Path $Root 'apps/web-portal'); npm ci --silent; npm run build; Pop-Location
  Push-Location (Join-Path $Root 'infra/cloudflare/pages')
  npx wrangler pages project create fauxel --production-branch main 2>$null
  npx wrangler pages deploy --project-name fauxel --branch main
  Pop-Location
  Note 'Portal (Pages)' 'TODO' "dashboard: Workers & Pages > fauxel > Custom domains > fauxel.$Domain"

  $cid = Read-Host 'Service token "fauxel-clients" id for the plugins/tools (step B; Enter to skip)'
  if ($cid) {
    $csec = Read-Secret 'Service token "fauxel-clients" secret'
    $out.api_url = "https://fauxel.$Domain"; $out.cf_access_client_id = $cid; $out.cf_access_client_secret = $csec
    [IO.File]::WriteAllText($SettingsPath, ($out | ConvertTo-Json)); Note 'Plugin settings' 'OK' "now point at https://fauxel.$Domain"
  }
  Set-EnvValue 'FAUXEL_PUBLIC_URL' "https://fauxel.$Domain"; Set-EnvValue 'FAUXEL_TRUST_ACCESS_HEADER' '1'
  Run-Child (Join-Path $Root 'scripts/windows/Setup-Fauxel.ps1') @('-InstallDir', $Root, '-SkipClone', '-Domain', $Domain, '-Production', '-SkipPlugins') | Out-Null
  Note 'Production mode' 'OK' 'API restarted with the public URL and the Access header trusted'
}

Write-Host @"

  Portal (this PC):  http://localhost:$PortalPort      (Settings > API key: paste the key below once)
  API key:           $key
  Project id:        $ProjectId
  Revit / Navisworks: Fauxel tab > Sync Progress / Export Model (settings already written)
  Next: walk with the ONE X2, then  .\Start-Fauxel.ps1 -Capture <folder>   (or Uploads in the portal)
"@ -ForegroundColor Green
Finish 0
