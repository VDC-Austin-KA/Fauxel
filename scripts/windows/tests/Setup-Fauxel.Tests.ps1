# Run: pwsh -NoProfile -File scripts/windows/tests/Setup-Fauxel.Tests.ps1  (pure-function tests, no Docker needed)
$ErrorActionPreference='Stop'
$tmp = Join-Path ([IO.Path]::GetTempPath()) ("fx" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path "$tmp/infra" | Out-Null
Copy-Item (Join-Path $PSScriptRoot "../../../infra/.env.example") "$tmp/infra/.env.example"
. (Join-Path $PSScriptRoot "../Setup-Fauxel.ps1") -InstallDir $tmp -Production -TunnelToken tok123
$s = New-Secret; if ($s.Length -ne 32 -or $s -notmatch '^[A-Za-z0-9]+$') { throw "secret" }
if ((New-Secret) -eq (New-Secret)) { throw "not random" }
$g = @(ConvertFrom-NvidiaSmi "NVIDIA GeForce RTX 3080, 10240, 552.22`n")[0]
if ($g.VramMiB -ne 10240 -or $g.Name -notmatch 'RTX 3080') { throw "nvsmi" }
[void](Initialize-EnvFile $true)
$a = Get-Content "$tmp/infra/.env" -Raw
$pw1 = Get-EnvValue ([string[]](Get-Content "$tmp/infra/.env")) 'POSTGRES_PASSWORD'
[void](Initialize-EnvFile $true)   # idempotent: secrets unchanged
$pw2 = Get-EnvValue ([string[]](Get-Content "$tmp/infra/.env")) 'POSTGRES_PASSWORD'
if ($pw1 -ne $pw2 -or $pw1 -eq 'CHANGE_ME') { throw "not idempotent" }
$l = [string[]](Get-Content "$tmp/infra/.env")
foreach ($k in 'FAUXEL_DOMAIN=ackerworks.com','FAUXEL_PUBLIC_URL=https://fauxel.ackerworks.com','FAUXEL_TRUST_ACCESS_HEADER=1','CLOUDFLARE_TUNNEL_TOKEN=tok123') { if ($l -notcontains $k) { throw "missing $k" } }
if ($a -match "`r") { throw "CRLF" }
if ((Get-Content "$tmp/infra/.env" -Raw) -match '=CHANGE_ME') { throw "CHANGE_ME left" }
# Copy-Tree idempotent
New-Item -ItemType Directory "$tmp/src/sub" | Out-Null; 'x' | Set-Content "$tmp/src/a.txt"; 'y' | Set-Content "$tmp/src/sub/b.txt"
$n1 = Copy-Tree "$tmp/src" "$tmp/dst"; $n2 = Copy-Tree "$tmp/src" "$tmp/dst"
if ($n1 -ne 2 -or $n2 -ne 0) { throw "copytree $n1 $n2" }
# Fernet key: 44 chars urlsafe base64 of 32 bytes, generated once and kept on re-run
$fk = Get-EnvValue $l 'FAUXEL_TOKEN_KEY'
if ($fk -notmatch '^[A-Za-z0-9_-]{43}=$' -or [Convert]::FromBase64String($fk.Replace('-','+').Replace('_','/')).Length -ne 32) { throw "fernet key $fk" }
if ((Get-EnvValue ([string[]](Get-Content "$tmp/infra/.env")) 'FAUXEL_TOKEN_KEY') -ne $fk) { throw "fernet key regenerated" }
if ((Get-EnvValue $l 'FAUXEL_APS_CALLBACK_URL') -ne 'https://fauxel.ackerworks.com/api/v1/integrations/aps/callback') { throw "aps callback" }
if (-not (Test-Path "$tmp/infra/models")) { throw "models dir" }
# Revit add-in layout
$env:APPDATA = Join-Path $tmp 'appdata'
$rsrc = Join-Path $tmp 'R25'; New-Item -ItemType Directory $rsrc | Out-Null
'a' | Set-Content "$rsrc/Fauxel.Revit.dll"; 'b' | Set-Content "$rsrc/Fauxel.Revit.pdb"; '<x/>' | Set-Content "$rsrc/Fauxel.addin"
$r1 = Install-RevitAddin $rsrc 2025; $r2 = Install-RevitAddin $rsrc 2025
$ad = Join-Path $env:APPDATA 'Autodesk/Revit/Addins/2025'
if ($r1 -ne 2 -or $r2 -ne 0 -or -not (Test-Path "$ad/Fauxel.addin") -or -not (Test-Path "$ad/Fauxel/Fauxel.Revit.dll") -or (Test-Path "$ad/Fauxel/Fauxel.Revit.pdb")) { throw "revit layout $r1 $r2" }
$script:Summary | Format-Table | Out-String | Write-Host
"ALL PS TESTS PASSED"
