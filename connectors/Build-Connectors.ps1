<#
.SYNOPSIS
  Builds the Fauxel Revit (2025-2027) and Navisworks (2025-2027) plugins on Windows and assembles the Navisworks bundle.
  Needs the .NET SDK 10 and 8 (winget install Microsoft.DotNet.SDK.10 ; winget install Microsoft.DotNet.SDK.8).
  Outputs: revit\Fauxel.Revit\bin\R25..R27 and navisworks\Fauxel.bundle\Contents\v22..v24, which Setup-Fauxel.ps1 installs.
#>
[CmdletBinding()]
param([switch]$SkipTests)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'
if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) { throw 'dotnet not found. Install the .NET SDK 10 and 8 first.' }

if (-not $SkipTests) { dotnet test dotnet-core\Fauxel.Connectors.Tests --nologo -v q; if ($LASTEXITCODE) { throw 'connector tests failed' } }

foreach ($cfg in 'R25', 'R26', 'R27') {
  dotnet build revit\Fauxel.Revit\Fauxel.Revit.csproj -c $cfg --nologo -v q
  if ($LASTEXITCODE) { throw "Revit $cfg build failed" }
}

$series = @{ N25 = 'v22'; N26 = 'v23'; N27 = 'v24' }
foreach ($cfg in 'N25', 'N26', 'N27') {
  dotnet build navisworks\Fauxel.Navisworks\Fauxel.Navisworks.csproj -c $cfg --nologo -v q
  if ($LASTEXITCODE) { throw "Navisworks $cfg build failed" }
  $dest = "navisworks\Fauxel.bundle\Contents\$($series[$cfg])"
  if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
  New-Item -ItemType Directory -Path $dest | Out-Null
  # Never ship Autodesk's own assemblies; the host provides them.
  Get-ChildItem "navisworks\Fauxel.Navisworks\bin\$cfg" -File |
    Where-Object { $_.Extension -ne '.pdb' -and $_.Name -notlike 'Autodesk.*' -and $_.Name -notlike 'navisworks.*' -and $_.Name -ne 'AdWindows.dll' } |
    Copy-Item -Destination $dest
}
Write-Host "Built: Revit R25-R27 and Navisworks bundle at $PSScriptRoot\navisworks\Fauxel.bundle" -ForegroundColor Green
