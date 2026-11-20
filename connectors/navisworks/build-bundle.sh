#!/usr/bin/env bash
# Builds Fauxel.Navisworks for 2025/2026/2027 and assembles Fauxel.bundle/Contents/v22|v23|v24.
set -euo pipefail
cd "$(dirname "$0")"
export PATH="$HOME/.dotnet:$PATH" DOTNET_ROOT="${DOTNET_ROOT:-$HOME/.dotnet}" DOTNET_CLI_TELEMETRY_OPTOUT=1

declare -A SERIES=([N25]=v22 [N26]=v23 [N27]=v24)
for cfg in N25 N26 N27; do
  dotnet build Fauxel.Navisworks/Fauxel.Navisworks.csproj -c "$cfg" --nologo -v q
  dest="Fauxel.bundle/Contents/${SERIES[$cfg]}"
  rm -rf "$dest"; mkdir -p "$dest"
  # our binaries + managed dependencies only (no Autodesk DLLs are ever in bin/)
  find "Fauxel.Navisworks/bin/$cfg" -maxdepth 1 -type f ! -name '*.pdb' ! -name 'Autodesk.*' ! -name 'navisworks.*' ! -name 'AdWindows.dll' -exec cp {} "$dest/" \;
done
echo "Bundle ready: $(pwd)/Fauxel.bundle"
