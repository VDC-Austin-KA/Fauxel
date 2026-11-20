<#
.SYNOPSIS  Installs the Fauxel Navisworks bundle (2025, 2026, 2027) for the current user. Close Navisworks first.
.EXAMPLE   .\Install-FauxelNavisworks.ps1
.EXAMPLE   .\Install-FauxelNavisworks.ps1 -Uninstall
Copies Fauxel.bundle to %APPDATA%\Autodesk\ApplicationPlugins\. If Contents\ has not been assembled (build-bundle.sh),
it is assembled here from Fauxel.Navisworks\bin\N25|N26|N27 (build those with: dotnet build -c N25 etc.).
#>
param([switch]$Uninstall)
$ErrorActionPreference = 'Stop'
if (Get-Process Roamer -ErrorAction SilentlyContinue) { throw 'Close Navisworks before (un)installing Fauxel.' }
$bundle = Join-Path $PSScriptRoot 'Fauxel.bundle'
$target = Join-Path $env:APPDATA 'Autodesk\ApplicationPlugins\Fauxel.bundle'

if ($Uninstall) { Remove-Item $target -Recurse -Force -ErrorAction SilentlyContinue; Write-Host 'Removed'; return }

$map = [ordered]@{ N25 = 'v22'; N26 = 'v23'; N27 = 'v24' }
foreach ($cfg in $map.Keys) {
    $dest = Join-Path $bundle "Contents\$($map[$cfg])"
    if (Test-Path (Join-Path $dest 'Fauxel.Navisworks.dll')) { continue }
    $src = Join-Path $PSScriptRoot "Fauxel.Navisworks\bin\$cfg"
    if (-not (Test-Path (Join-Path $src 'Fauxel.Navisworks.dll'))) { Write-Warning "No build for $cfg ($src); that Navisworks year will not load Fauxel."; continue }
    New-Item -ItemType Directory -Force $dest | Out-Null
    Get-ChildItem $src -File | Where-Object { $_.Extension -ne '.pdb' } | Copy-Item -Destination $dest -Force
}
if (Test-Path $target) { Remove-Item $target -Recurse -Force }
New-Item -ItemType Directory -Force (Split-Path $target) | Out-Null
Copy-Item $bundle $target -Recurse -Force
Write-Host "Installed to $target"
