<#
.SYNOPSIS
  Postgres backup: pg_dump in the db container -> gzip -> upload to R2 (S3 API via the aws-cli container) -> prune after N days.
.EXAMPLE
  .\Backup-Fauxel.ps1
.EXAMPLE
  # Nightly at 02:30 as a scheduled task (run once in an elevated shell):
  .\Backup-Fauxel.ps1 -RegisterTask
#>
[CmdletBinding()]
param(
  [string]$RepoDir = (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)),
  [string]$BackupDir,
  [switch]$LocalOnly,
  [switch]$RegisterTask,
  [string]$TaskTime = '02:30'
)
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

function Get-EnvMap([string]$Path) {
  $m = @{}
  foreach ($l in (Get-Content $Path)) { if ($l -match '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$') { $m[$Matches[1]] = $Matches[2].Trim() } }
  $m
}
function Get-Cfg($Map, [string]$Key, [string]$Default = '') {
  $v = [Environment]::GetEnvironmentVariable($Key)
  if ($v) { return $v }
  if ($Map.ContainsKey($Key) -and $Map[$Key]) { return $Map[$Key] }
  $Default
}
function Invoke-Native([string]$File, [string[]]$Arguments, [switch]$AllowFail) {
  $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  try { $out = & $File @Arguments 2>&1 | Out-String; $code = $LASTEXITCODE } finally { $ErrorActionPreference = $prev }
  if ($code -ne 0 -and -not $AllowFail) { throw "$File $($Arguments -join ' ') failed ($code): $out" }
  $out.Trim()
}

if ($RegisterTask) {
  $self = $MyInvocation.MyCommand.Path
  $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$self`""
  $trigger = New-ScheduledTaskTrigger -Daily -At $TaskTime
  Register-ScheduledTask -TaskName 'Fauxel nightly backup' -Action $action -Trigger $trigger -Force | Out-Null
  Write-Host "Registered scheduled task 'Fauxel nightly backup' at $TaskTime"
  return
}

$envFile = Join-Path $RepoDir 'infra/.env'
if (-not (Test-Path $envFile)) { throw "missing $envFile" }
$cfg = Get-EnvMap $envFile
$pgUser = Get-Cfg $cfg 'POSTGRES_USER' 'fauxel'
$pgDb = Get-Cfg $cfg 'POSTGRES_DB' 'fauxel'
$endpoint = Get-Cfg $cfg 'BACKUP_S3_ENDPOINT'
$bucket = Get-Cfg $cfg 'BACKUP_S3_BUCKET' 'fauxel-backups'
$ak = Get-Cfg $cfg 'BACKUP_S3_ACCESS_KEY'
$sk = Get-Cfg $cfg 'BACKUP_S3_SECRET_KEY'
$days = [int](Get-Cfg $cfg 'BACKUP_RETENTION_DAYS' '14')
$net = Get-Cfg $cfg 'BACKUP_DOCKER_NETWORK'
if (-not $BackupDir) { $BackupDir = Get-Cfg $cfg 'FAUXEL_BACKUP_DIR' (Join-Path $RepoDir 'backups') }
New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null

$stamp = [DateTime]::UtcNow.ToString("yyyyMMdd'T'HHmmss'Z'")
$name = "fauxel-$stamp.sql.gz"
$composeArgs = @('compose', '--project-directory', (Join-Path $RepoDir 'infra'), '-f', (Join-Path $RepoDir 'infra/docker-compose.yml'))

Write-Host "dumping $pgDb ..."
Invoke-Native 'docker' ($composeArgs + @('exec', '-T', 'db', 'sh', '-c', "pg_dump -U '$pgUser' -d '$pgDb' --no-owner --clean --if-exists | gzip -9 > /tmp/$name")) | Out-Null
$dest = Join-Path $BackupDir $name
Invoke-Native 'docker' ($composeArgs + @('cp', "db:/tmp/$name", $dest)) | Out-Null
Invoke-Native 'docker' ($composeArgs + @('exec', '-T', 'db', 'rm', '-f', "/tmp/$name")) | Out-Null
$size = (Get-Item $dest).Length
if ($size -lt 200) { Remove-Item $dest -Force; throw "dump suspiciously small ($size bytes)" }
Write-Host "wrote $dest ($size bytes)"

function Invoke-Aws([string[]]$AwsArgs) {
  $a = @('run', '--rm')
  if ($net) { $a += @('--network', $net) }
  $a += @('-v', "${BackupDir}:/backup",
          '-e', "AWS_ACCESS_KEY_ID=$ak", '-e', "AWS_SECRET_ACCESS_KEY=$sk", '-e', 'AWS_DEFAULT_REGION=auto',
          '-e', 'AWS_REQUEST_CHECKSUM_CALCULATION=when_required', '-e', 'AWS_RESPONSE_CHECKSUM_VALIDATION=when_required',
          'amazon/aws-cli', '--endpoint-url', $endpoint)
  Invoke-Native 'docker' ($a + $AwsArgs)
}

if ($LocalOnly -or -not $endpoint) {
  Write-Warning 'no BACKUP_S3_ENDPOINT (or -LocalOnly): kept local copy only'
} else {
  Invoke-Aws @('s3', 'cp', "/backup/$name", "s3://$bucket/db/$name", '--only-show-errors') | Out-Null
  Write-Host "uploaded s3://$bucket/db/$name"
  $cutoff = [DateTime]::UtcNow.AddDays(-$days).ToString("yyyy-MM-dd'T'HH:mm:ss'Z'")
  $old = Invoke-Aws @('s3api', 'list-objects-v2', '--bucket', $bucket, '--prefix', 'db/', '--query', "Contents[?LastModified<'$cutoff'].Key", '--output', 'text')
  $keys = @($old -split "\s+" | Where-Object { $_ -and $_ -ne 'None' })
  foreach ($k in $keys) { Invoke-Aws @('s3api', 'delete-object', '--bucket', $bucket, '--key', $k) | Out-Null }
  Write-Host "pruned $($keys.Count) remote backup(s) older than $days days"
}

Get-ChildItem $BackupDir -Filter 'fauxel-*.sql.gz' | Where-Object { $_.LastWriteTimeUtc -lt [DateTime]::UtcNow.AddDays(-$days) } | ForEach-Object {
  Write-Host "pruned local $($_.Name)"; Remove-Item $_.FullName -Force
}
Write-Host "backup OK: $name"
