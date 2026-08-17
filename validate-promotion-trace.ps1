[CmdletBinding()]
param(
  [string]$ApiBase = "https://srv1170872.tail86523.ts.net:8444",
  [ValidateRange(20,500)][int]$Limit = 300
)
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Get-Command python -ErrorAction SilentlyContinue
if (-not $Python) { throw "Python was not found on PATH." }
Push-Location $ProjectRoot
try {
  & $Python.Source ".\scripts\promotion_trace.py" --api-base $ApiBase --limit "$Limit"
  if ($LASTEXITCODE -ne 0) { throw "Promotion trace audit failed with exit code $LASTEXITCODE." }
} finally {
  Pop-Location
}
