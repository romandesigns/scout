[CmdletBinding()]
param(
  [string]$ApiBase = "https://srv1170872.tail86523.ts.net:8444",
  [ValidateRange(10,500)][int]$Limit = 100,
  [switch]$IncludeDeveloping,
  [ValidateRange(30,3600)][int]$MinAgeSeconds = 300
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Get-Command python -ErrorAction SilentlyContinue
if (-not $Python) { throw "Python was not found on PATH." }

Push-Location $ProjectRoot
try {
  $Args = @(
    ".\scripts\detection_quality.py",
    "--api-base", $ApiBase,
    "--limit", "$Limit",
    "--min-age-seconds", "$MinAgeSeconds"
  )
  if ($IncludeDeveloping) { $Args += "--include-developing" }
  & $Python.Source @Args
  if ($LASTEXITCODE -ne 0) { throw "Detection-quality audit failed with exit code $LASTEXITCODE." }
} finally {
  Pop-Location
}
