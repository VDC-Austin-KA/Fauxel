<#
.SYNOPSIS
  Sets up (or updates) Fauxel on a Windows PC with Docker Desktop (WSL2). Idempotent: safe to re-run.

.DESCRIPTION
  1. Preflight: Windows build, WSL2, Docker Desktop + compose, NVIDIA driver/VRAM (nvidia-smi), git
  2. Clone or fast-forward the repo into -InstallDir
  3. Create infra\.env from infra\.env.example, generating secrets only for values still set to CHANGE_ME
  4. docker compose up -d (adds docker-compose.gpu.yml when an NVIDIA GPU is present)
  5. Wait for /api/v1/health
  6. Optional: install cloudflared as a Windows service (-TunnelToken)
  7. Install the Revit add-in (connectors\revit\Fauxel.Revit\bin\R25..R27 -> %APPDATA%\Autodesk\Revit\Addins\2025..2027) and the Navisworks bundle (-> %APPDATA%\Autodesk\ApplicationPlugins) if built
  8. Print a summary table

.EXAMPLE
  .\Setup-Fauxel.ps1 -InstallDir C:\Fauxel -Production
.EXAMPLE
  .\Setup-Fauxel.ps1 -TunnelToken eyJh... -InstallCloudflaredService     # run in an elevated shell
#>
[CmdletBinding()]
param(
  [string]$InstallDir = (Join-Path $env:USERPROFILE 'fauxel'),
  [string]$RepoUrl    = 'https://github.com/VDC-Austin-KA/Fauxel.git',
  [string]$Branch     = 'main',
  [string]$Domain     = $(if ($env:FAUXEL_DOMAIN) { $env:FAUXEL_DOMAIN } else { 'ackerworks.com' }),
  [switch]$Production,                 # sets FAUXEL_PUBLIC_URL=https://fauxel.<domain> and trusts the Access header
  [string]$TunnelToken,                # Cloudflare Tunnel token (stored in infra\.env)
  [switch]$InstallCloudflaredService,  # install cloudflared as a Windows service (needs admin + -TunnelToken)
  [switch]$NoGpu,                      # never use the GPU override
  [switch]$SkipClone,                  # use -InstallDir as is (already a checkout)
  [switch]$SkipPlugins,                # do not install Revit/Navisworks plugins
  [switch]$BuildPortal,                # run npm ci && npm run build in apps\web-portal when dist is missing (needs Node)
  [int]$HealthTimeoutSec = 300
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$script:Summary = New-Object System.Collections.Generic.List[object]

function Add-Result {
  param([string]$Step, [ValidateSet('OK', 'WARN', 'FAIL', 'SKIP')][string]$Status, [string]$Detail = '')
  $script:Summary.Add([pscustomobject]@{ Step = $Step; Status = $Status; Detail = $Detail }) | Out-Null
  $color = @{ OK = 'Green'; WARN = 'Yellow'; FAIL = 'Red'; SKIP = 'DarkGray' }[$Status]
  Write-Host ('[{0,-4}] {1}: {2}' -f $Status, $Step, $Detail) -ForegroundColor $color
}

function Test-Command([string]$Name) { [bool](Get-Command $Name -ErrorAction SilentlyContinue) }

function Test-IsAdmin {
  $id = [Security.Principal.WindowsIdentity]::GetCurrent()
  (New-Object Security.Principal.WindowsPrincipal $id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# Run a native command, return @{ Code; Out } without throwing on non-zero exit.
function Invoke-Native {
  param([string]$File, [string[]]$Arguments)
  $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  try { $out = & $File @Arguments 2>&1 | Out-String; $code = $LASTEXITCODE } finally { $ErrorActionPreference = $prev }
  [pscustomobject]@{ Code = $code; Out = $out.Trim() }
}

# wsl.exe prints UTF-16; strip NULs so -match works.
function Get-WslVersions {
  $r = Invoke-Native 'wsl.exe' @('-l', '-v')
  ($r.Out -replace "`0", '')
}

function New-Secret([int]$Length = 32) {
  $chars = 'abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789'.ToCharArray()
  $bytes = New-Object byte[] ($Length)
  $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
  try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
  -join ($bytes | ForEach-Object { $chars[$_ % $chars.Length] })
}

# Fernet key = urlsafe base64 of 32 random bytes (what `cryptography.fernet.Fernet.generate_key()` returns).
function New-FernetKey {
  $bytes = New-Object byte[] 32
  $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
  try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
  [Convert]::ToBase64String($bytes).Replace('+', '-').Replace('/', '_')
}

function Get-EnvValue([string[]]$Lines, [string]$Key) {
  foreach ($l in $Lines) { if ($l -match ('^\s*' + [regex]::Escape($Key) + '=(.*)$')) { return $Matches[1] } }
  return $null
}

# Returns updated lines with KEY=Value set (replace the first match or append).
function Set-EnvValue([string[]]$Lines, [string]$Key, [string]$Value) {
  $out = New-Object System.Collections.Generic.List[string]
  $done = $false
  foreach ($l in $Lines) {
    if (-not $done -and $l -match ('^\s*' + [regex]::Escape($Key) + '=')) { $out.Add("$Key=$Value"); $done = $true } else { $out.Add($l) }
  }
  if (-not $done) { $out.Add("$Key=$Value") }
  , $out.ToArray()
}

function Write-LfFile([string]$Path, [string[]]$Lines) {
  $enc = New-Object System.Text.UTF8Encoding($false)
  [IO.File]::WriteAllText($Path, (($Lines -join "`n") + "`n"), $enc)
}

# Parse "name, memoryMiB, driver" lines from nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader,nounits
function ConvertFrom-NvidiaSmi([string]$Text) {
  foreach ($line in ($Text -split "`r?`n")) {
    if ($line -match '^\s*(.+?)\s*,\s*(\d+)\s*,\s*([\d\.]+)\s*$') {
      [pscustomobject]@{ Name = $Matches[1]; VramMiB = [int]$Matches[2]; Driver = $Matches[3] }
    }
  }
}

function Test-Preflight {
  # Windows
  $build = [Environment]::OSVersion.Version.Build
  if ($build -ge 19044) { Add-Result 'Windows' 'OK' ("build $build") }
  else { Add-Result 'Windows' 'FAIL' ("build $build; Windows 10 21H2 (19044) or Windows 11 required for WSL2 GPU support") }

  # WSL2
  if (Test-Command 'wsl.exe') {
    $v = Get-WslVersions
    if ($v -match '\s2\s*$' -or $v -match '(?m)\s2\s*$') { Add-Result 'WSL2' 'OK' 'a WSL2 distro is installed' }
    elseif ($v) { Add-Result 'WSL2' 'WARN' 'WSL present but no WSL2 distro listed (Docker Desktop uses its own docker-desktop distro; fine if Docker works)' }
    else { Add-Result 'WSL2' 'WARN' 'wsl -l -v returned nothing; run: wsl --install' }
  } else { Add-Result 'WSL2' 'FAIL' 'wsl.exe not found; run: wsl --install' }

  # Docker Desktop
  if (-not (Test-Command 'docker')) {
    Add-Result 'Docker Desktop' 'FAIL' 'docker not found; install Docker Desktop with the WSL2 backend: https://www.docker.com/products/docker-desktop/'
  } else {
    $info = Invoke-Native 'docker' @('info', '--format', '{{.OperatingSystem}}|{{.ServerVersion}}')
    if ($info.Code -ne 0) { Add-Result 'Docker Desktop' 'FAIL' 'docker daemon not reachable; start Docker Desktop and wait for the whale icon to settle' }
    else {
      $c = Invoke-Native 'docker' @('compose', 'version', '--short')
      if ($c.Code -ne 0) { Add-Result 'Docker Desktop' 'FAIL' 'docker compose v2 missing; update Docker Desktop' }
      else { Add-Result 'Docker Desktop' 'OK' ("engine {0}; compose {1}" -f ($info.Out -split '\|')[1], $c.Out) }
    }
  }

  # git
  if (Test-Command 'git') { Add-Result 'git' 'OK' ((Invoke-Native 'git' @('--version')).Out) }
  else { Add-Result 'git' 'FAIL' 'git not found; winget install Git.Git' }
}

function Get-GpuInfo {
  if (-not (Test-Command 'nvidia-smi')) { return $null }
  $r = Invoke-Native 'nvidia-smi' @('--query-gpu=name,memory.total,driver_version', '--format=csv,noheader,nounits')
  if ($r.Code -ne 0) { return $null }
  @(ConvertFrom-NvidiaSmi $r.Out)[0]
}

function Sync-Repo {
  if ($SkipClone) { Add-Result 'Repo' 'SKIP' "-SkipClone; using $InstallDir"; return }
  if (Test-Path (Join-Path $InstallDir '.git')) {
    $dirty = (Invoke-Native 'git' @('-C', $InstallDir, 'status', '--porcelain')).Out
    if ($dirty) { Add-Result 'Repo' 'WARN' 'local changes present; not pulling' ; return }
    $f = Invoke-Native 'git' @('-C', $InstallDir, 'fetch', '--prune', 'origin')
    $p = Invoke-Native 'git' @('-C', $InstallDir, 'pull', '--ff-only', 'origin', $Branch)
    if ($f.Code -eq 0 -and $p.Code -eq 0) { Add-Result 'Repo' 'OK' ("updated {0} ({1})" -f $InstallDir, $Branch) }
    else { Add-Result 'Repo' 'FAIL' ($p.Out) }
  } else {
    $r = Invoke-Native 'git' @('clone', '--branch', $Branch, $RepoUrl, $InstallDir)
    if ($r.Code -eq 0) { Add-Result 'Repo' 'OK' ("cloned to $InstallDir") } else { Add-Result 'Repo' 'FAIL' $r.Out }
  }
}

function Initialize-EnvFile([bool]$UseGpu) {
  $infra = Join-Path $InstallDir 'infra'
  $envPath = Join-Path $infra '.env'
  $example = Join-Path $infra '.env.example'
  if (-not (Test-Path $example)) { Add-Result 'infra/.env' 'FAIL' "missing $example"; return $false }
  $created = -not (Test-Path $envPath)
  $lines = if ($created) { [string[]](Get-Content $example) } else { [string[]](Get-Content $envPath) }

  $filled = 0
  foreach ($key in @('POSTGRES_PASSWORD', 'LOCAL_S3_SECRET_KEY', 'FAUXEL_KEY_SALT', 'FAUXEL_ADMIN_TOKEN', 'FAUXEL_APS_WEBHOOK_SECRET')) {
    $cur = Get-EnvValue $lines $key
    if ($null -eq $cur -or $cur -eq '' -or $cur -eq 'CHANGE_ME') { $lines = Set-EnvValue $lines $key (New-Secret); $filled++ }
  }
  $tk = Get-EnvValue $lines 'FAUXEL_TOKEN_KEY'
  if ($null -eq $tk -or $tk -eq '' -or $tk -eq 'CHANGE_ME') { $lines = Set-EnvValue $lines 'FAUXEL_TOKEN_KEY' (New-FernetKey); $filled++ }
  $lines = Set-EnvValue $lines 'FAUXEL_DOMAIN' $Domain
  $lines = Set-EnvValue $lines 'FAUXEL_APS_CALLBACK_URL' "https://fauxel.$Domain/api/v1/integrations/aps/callback"
  if ($Production) {
    $lines = Set-EnvValue $lines 'FAUXEL_PUBLIC_URL' "https://fauxel.$Domain"
    $lines = Set-EnvValue $lines 'FAUXEL_TRUST_ACCESS_HEADER' '1'
  }
  if ($TunnelToken) { $lines = Set-EnvValue $lines 'CLOUDFLARE_TUNNEL_TOKEN' $TunnelToken }
  if ($UseGpu -and -not (Get-EnvValue $lines 'FAUXEL_INFER_BATCH')) { $lines = Set-EnvValue $lines 'FAUXEL_INFER_BATCH' '2' }

  Write-LfFile $envPath $lines
  New-Item -ItemType Directory -Force -Path (Join-Path $infra 'models') | Out-Null   # FAUXEL_MODEL_DIR_HOST (trained ONNX models)
  Add-Result 'infra/.env' 'OK' $(if ($created) { "created; generated $filled secrets" } else { "kept; filled $filled missing secrets" })
  return $true
}

function Start-Stack([bool]$UseGpu) {
  $infra = Join-Path $InstallDir 'infra'
  if ($BuildPortal -or -not (Test-Path (Join-Path $InstallDir 'apps\web-portal\dist'))) {
    $wp = Join-Path $InstallDir 'apps\web-portal'
    if ($BuildPortal -and (Test-Command 'npm') -and (Test-Path $wp)) {
      Push-Location $wp
      try {
        $a = Invoke-Native 'npm' @('ci'); $b = Invoke-Native 'npm' @('run', 'build')
        if ($a.Code -eq 0 -and $b.Code -eq 0) { Add-Result 'Portal build' 'OK' 'apps/web-portal/dist built' } else { Add-Result 'Portal build' 'WARN' 'npm build failed; the local portal container will be empty' }
      } finally { Pop-Location }
    } elseif (-not (Test-Path (Join-Path $wp 'dist'))) {
      New-Item -ItemType Directory -Force -Path (Join-Path $wp 'dist') | Out-Null
      Add-Result 'Portal build' 'WARN' 'apps/web-portal/dist missing (empty dir created). Build it (npm run build) or pass -BuildPortal'
    }
  }

  $args2 = @('compose', '-f', 'docker-compose.yml')
  if ($UseGpu) { $args2 += @('-f', 'docker-compose.gpu.yml') }
  if ($TunnelToken -and -not $InstallCloudflaredService) { $args2 += @('--profile', 'tunnel') }
  $args2 += @('up', '-d', '--build')
  Push-Location $infra
  try {
    $r = Invoke-Native 'docker' $args2
    if ($r.Code -ne 0 -and $UseGpu) {
      Add-Result 'GPU override' 'WARN' 'compose failed with the GPU override (Docker Desktop GPU support off?); retrying CPU only'
      $UseGpu = $false
      $cpu = @('compose', '-f', 'docker-compose.yml'); if ($TunnelToken -and -not $InstallCloudflaredService) { $cpu += @('--profile', 'tunnel') }
      $r = Invoke-Native 'docker' ($cpu + @('up', '-d', '--build'))
    }
  } finally { Pop-Location }
  if ($r.Code -eq 0) { Add-Result 'docker compose up' 'OK' $(if ($UseGpu) { 'with GPU override' } else { 'CPU only' }) }
  else { Add-Result 'docker compose up' 'FAIL' (($r.Out -split "`n" | Select-Object -Last 6) -join ' | '); return $false }
  return $true
}

function Wait-Health {
  $envLines = [string[]](Get-Content (Join-Path $InstallDir 'infra\.env'))
  $port = Get-EnvValue $envLines 'API_PORT'; if (-not $port) { $port = '8000' }
  $url = "http://127.0.0.1:$port/api/v1/health"
  $deadline = (Get-Date).AddSeconds($HealthTimeoutSec)
  $last = ''
  while ((Get-Date) -lt $deadline) {
    try {
      $h = Invoke-RestMethod -Uri $url -TimeoutSec 5
      if ($h.status -eq 'ok') { Add-Result 'API health' 'OK' ("{0} db={1} storage={2} gpu={3}" -f $url, $h.db, $h.storage, $h.gpu); return $true }
      $last = ($h | ConvertTo-Json -Compress)
    } catch { $last = $_.Exception.Message }
    Start-Sleep -Seconds 3
  }
  Add-Result 'API health' 'FAIL' ("no healthy answer from $url in ${HealthTimeoutSec}s ($last). See: docker compose -f infra\docker-compose.yml logs api")
  return $false
}

function Install-CloudflaredService {
  if (-not $InstallCloudflaredService) { Add-Result 'cloudflared service' 'SKIP' 'not requested'; return }
  if (-not $TunnelToken) { Add-Result 'cloudflared service' 'FAIL' 'pass -TunnelToken'; return }
  if (-not (Test-IsAdmin)) { Add-Result 'cloudflared service' 'FAIL' 'needs an elevated PowerShell'; return }
  if (-not (Test-Command 'cloudflared')) {
    if (Test-Command 'winget') { Invoke-Native 'winget' @('install', '--id', 'Cloudflare.cloudflared', '-e', '--accept-source-agreements', '--accept-package-agreements') | Out-Null }
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
  }
  if (-not (Test-Command 'cloudflared')) { Add-Result 'cloudflared service' 'FAIL' 'cloudflared not installed; winget install Cloudflare.cloudflared'; return }
  $svc = Get-Service -Name 'Cloudflared' -ErrorAction SilentlyContinue
  if ($svc) {
    if ($svc.Status -ne 'Running') { Start-Service 'Cloudflared' }
    Add-Result 'cloudflared service' 'OK' 'already installed (to change the token: cloudflared service uninstall, then re-run)'
    return
  }
  $r = Invoke-Native 'cloudflared' @('service', 'install', $TunnelToken)
  if ($r.Code -eq 0) { Add-Result 'cloudflared service' 'OK' 'installed. In Zero Trust set the public hostname service URL to http://localhost:8000' }
  else { Add-Result 'cloudflared service' 'FAIL' $r.Out }
}

# Copy a folder's contents, skipping files whose content hash is unchanged. Returns number of files copied.
function Copy-Tree([string]$Src, [string]$Dst) {
  $n = 0
  New-Item -ItemType Directory -Force -Path $Dst | Out-Null
  $srcFull = (Resolve-Path $Src).Path.TrimEnd('\')
  Get-ChildItem -Path $srcFull -Recurse -File | ForEach-Object {
    $rel = $_.FullName.Substring($srcFull.Length).TrimStart('\')
    $to = Join-Path $Dst $rel
    New-Item -ItemType Directory -Force -Path (Split-Path $to) | Out-Null
    if (-not (Test-Path $to) -or (Get-FileHash $_.FullName).Hash -ne (Get-FileHash $to).Hash) { Copy-Item $_.FullName $to -Force; $n++ }
  }
  $n
}

# Revit layout: Addins\<year>\Fauxel.addin (manifest) + Addins\<year>\Fauxel\*.dll (the manifest points at Fauxel\Fauxel.Revit.dll).
function Install-RevitAddin([string]$Src, [int]$Year) {
  $addins = Join-Path $env:APPDATA "Autodesk\Revit\Addins\$Year"
  $target = Join-Path $addins 'Fauxel'
  New-Item -ItemType Directory -Force -Path $target | Out-Null
  $n = 0
  foreach ($f in Get-ChildItem $Src -File | Where-Object { $_.Name -ne 'Fauxel.addin' -and $_.Extension -ne '.pdb' }) {
    $to = Join-Path $target $f.Name
    if (-not (Test-Path $to) -or (Get-FileHash $f.FullName).Hash -ne (Get-FileHash $to).Hash) { Copy-Item $f.FullName $to -Force; $n++ }
  }
  $manifest = Join-Path $Src 'Fauxel.addin'
  $mto = Join-Path $addins 'Fauxel.addin'
  if (-not (Test-Path $mto) -or (Get-FileHash $manifest).Hash -ne (Get-FileHash $mto).Hash) { Copy-Item $manifest $mto -Force; $n++ }
  $n
}

function Install-Plugins {
  if ($SkipPlugins) { Add-Result 'Plugins' 'SKIP' '-SkipPlugins'; return }
  $any = $false
  if (Get-Process -Name Revit, Navisworks, Roamer -ErrorAction SilentlyContinue) { Add-Result 'Plugins' 'WARN' 'Revit/Navisworks is running: restart it to load the updated plugin' }
  foreach ($year in 2025, 2026, 2027) {
    $src = Join-Path $InstallDir ("connectors\revit\Fauxel.Revit\bin\R{0}" -f ($year % 100))
    if (Test-Path (Join-Path $src 'Fauxel.Revit.dll')) {
      $any = $true
      try { $n = Install-RevitAddin $src $year; Add-Result "Revit $year add-in" 'OK' ("$n file(s) updated in %APPDATA%\Autodesk\Revit\Addins\$year") }
      catch { Add-Result "Revit $year add-in" 'FAIL' $_.Exception.Message }
    }
  }
  $bundle = Join-Path $InstallDir 'connectors\navisworks\Fauxel.bundle'
  if (Test-Path (Join-Path $bundle 'PackageContents.xml')) {
    $any = $true
    $dst = Join-Path $env:APPDATA 'Autodesk\ApplicationPlugins\Fauxel.bundle'
    try { $n = Copy-Tree $bundle $dst; Add-Result 'Navisworks bundle' 'OK' ("$n file(s) updated in $dst (restart Navisworks)") }
    catch { Add-Result 'Navisworks bundle' 'FAIL' $_.Exception.Message }
  }
  if (-not $any) { Add-Result 'Plugins' 'SKIP' 'no build outputs (connectors\revit\Fauxel.Revit\bin\R25..R27, connectors\navisworks\Fauxel.bundle); build them first' }
}

function Show-Summary {
  Write-Host ''
  $script:Summary | Format-Table -AutoSize -Wrap | Out-String | Write-Host
  $fails = @($script:Summary | Where-Object Status -eq 'FAIL').Count
  if ($fails -eq 0) {
    Write-Host ("Fauxel is up. Local portal: http://localhost:8080   API: http://127.0.0.1:8000/api/v1/health   Public: https://fauxel.{0}" -f $Domain) -ForegroundColor Green
  } else { Write-Host "$fails step(s) failed. Fix them and re-run this script (it is idempotent)." -ForegroundColor Red }
}

function Invoke-Main {
  Write-Host "Fauxel setup (domain $Domain, dir $InstallDir)" -ForegroundColor Cyan
  Test-Preflight
  $pre = @($script:Summary | Where-Object { $_.Status -eq 'FAIL' })
  if ($pre.Count -gt 0) { Show-Summary; exit 1 }

  $gpu = Get-GpuInfo
  $useGpu = $false
  if ($NoGpu) { Add-Result 'NVIDIA GPU' 'SKIP' '-NoGpu' }
  elseif ($gpu) {
    $gb = [math]::Round($gpu.VramMiB / 1024, 1)
    $useGpu = $true
    if ($gpu.VramMiB -lt 8000) { Add-Result 'NVIDIA GPU' 'WARN' ("{0}, {1} GB VRAM, driver {2}: below 8 GB, keep FAUXEL_INFER_BATCH=1" -f $gpu.Name, $gb, $gpu.Driver) }
    else { Add-Result 'NVIDIA GPU' 'OK' ("{0}, {1} GB VRAM, driver {2} (FAUXEL_INFER_BATCH=2 suits 8-12 GB)" -f $gpu.Name, $gb, $gpu.Driver) }
  } else { Add-Result 'NVIDIA GPU' 'WARN' 'nvidia-smi not available; running CPU only' }

  Sync-Repo
  if (@($script:Summary | Where-Object { $_.Step -eq 'Repo' -and $_.Status -eq 'FAIL' }).Count) { Show-Summary; exit 1 }
  if (-not (Initialize-EnvFile $useGpu)) { Show-Summary; exit 1 }
  if (Start-Stack $useGpu) { [void](Wait-Health) }
  Install-CloudflaredService
  Install-Plugins
  Show-Summary
  if (@($script:Summary | Where-Object Status -eq 'FAIL').Count) { exit 1 }
}

# Dot-sourcing (for tests) defines the functions without running anything.
if ($MyInvocation.InvocationName -ne '.') { Invoke-Main }
