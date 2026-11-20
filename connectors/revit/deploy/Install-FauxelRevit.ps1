<#
.SYNOPSIS  Installs the Fauxel Revit add-in for the given Revit years (per user, no admin needed).
.EXAMPLE   .\Install-FauxelRevit.ps1                 # every year that has a built bin\R2x folder
.EXAMPLE   .\Install-FauxelRevit.ps1 -Years 2026,2027
.EXAMPLE   .\Install-FauxelRevit.ps1 -Uninstall
Copies bin\R25|R26|R27 to %APPDATA%\Autodesk\Revit\Addins\<year>\Fauxel\ and Fauxel.addin next to it.
Close Revit first.
#>
param(
    [int[]]$Years = @(2025, 2026, 2027),
    [string]$BinRoot = (Join-Path $PSScriptRoot '..\Fauxel.Revit\bin'),
    [switch]$Uninstall
)
$ErrorActionPreference = 'Stop'
if (Get-Process Revit -ErrorAction SilentlyContinue) { throw 'Close Revit before (un)installing Fauxel.' }

foreach ($year in $Years) {
    $addins = Join-Path $env:APPDATA "Autodesk\Revit\Addins\$year"
    $target = Join-Path $addins 'Fauxel'
    if ($Uninstall) {
        Remove-Item $target -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item (Join-Path $addins 'Fauxel.addin') -Force -ErrorAction SilentlyContinue
        Write-Host "Revit ${year}: removed"
        continue
    }
    $src = Join-Path $BinRoot ("R" + ($year % 100))
    if (-not (Test-Path (Join-Path $src 'Fauxel.Revit.dll'))) { Write-Warning "Revit ${year}: no build at $src (run connectors/build.sh or dotnet build -c R$($year % 100))"; continue }
    New-Item -ItemType Directory -Force $target | Out-Null
    Get-ChildItem $src -File | Where-Object { $_.Name -ne 'Fauxel.addin' -and $_.Extension -ne '.pdb' } | Copy-Item -Destination $target -Force
    Copy-Item (Join-Path $src 'Fauxel.addin') (Join-Path $addins 'Fauxel.addin') -Force
    Write-Host "Revit ${year}: installed to $target"
}
