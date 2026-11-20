#!/usr/bin/env bash
# Builds every connector configuration on Linux (EnableWindowsTargeting) and runs the unit tests.
#   Core + tests           netstandard2.0 / net10.0
#   Fauxel.Revit           R25, R26 (net8.0-windows), R27 (net10.0-windows)  -> revit/Fauxel.Revit/bin/R2x
#   Fauxel.Navisworks      N25, N26, N27 (net48)                             -> navisworks/Fauxel.bundle/Contents/v22|v23|v24
set -euo pipefail
cd "$(dirname "$0")"
export PATH="$HOME/.dotnet:$PATH" DOTNET_ROOT="${DOTNET_ROOT:-$HOME/.dotnet}" DOTNET_CLI_TELEMETRY_OPTOUT=1

echo "== tests";  dotnet test dotnet-core/Fauxel.Connectors.Tests --nologo -v q
for cfg in R25 R26 R27; do
  echo "== Revit $cfg"; dotnet build revit/Fauxel.Revit/Fauxel.Revit.csproj -c "$cfg" --nologo -v q
done
echo "== Navisworks N25/N26/N27 + bundle"; ./navisworks/build-bundle.sh
echo "All connector configurations built and tests passed."
