[CmdletBinding()]
param(
  [string]$ApiBase = "https://srv1170872.tail86523.ts.net:8444",
  [string]$SshHost = "srv1170872.tail86523.ts.net",
  [string]$SshUser = "wavystack",
  [ValidateRange(1,50)][int]$SampleSize = 8,
  [ValidateRange(5,120)][int]$ProgressSeconds = 20,
  [switch]$TestAndroidNotification,
  [switch]$SkipSsh,
  [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
if ($PSVersionTable.PSVersion.Major -lt 7) { throw "Use PowerShell 7 or newer (pwsh)." }

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Version = (Get-Content (Join-Path $Root "VERSION") -Raw).Trim()
$Validator = Join-Path $Root "scripts\e2e_validate.py"
if (-not (Test-Path $Validator)) { throw "Missing $Validator" }
if (-not $OutputPath) {
  $Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
  $OutputPath = Join-Path $Root "e2e-validation-$Stamp.json"
}

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "SCOUT END-TO-END VALIDATION" -ForegroundColor Cyan
Write-Host "Expected source version: $Version"
Write-Host "API: $ApiBase"
Write-Host "============================================================" -ForegroundColor Cyan

$Args = @(
  $Validator,
  "--api-base", $ApiBase,
  "--expected-version", $Version,
  "--sample-size", $SampleSize,
  "--progress-seconds", $ProgressSeconds,
  "--ssh-host", $SshHost,
  "--ssh-user", $SshUser,
  "--output", $OutputPath
)
if ($SkipSsh) { $Args += "--skip-ssh" }
if ($TestAndroidNotification) { $Args += "--test-android-notification" }

python @Args
$Code = $LASTEXITCODE

Write-Host ""
Write-Host "===== LOCAL DESKTOP CHECK =====" -ForegroundColor Cyan
$Exe = Join-Path $env:LOCALAPPDATA "StockHunter Scout\stockhunter-scout.exe"
if (Test-Path $Exe) {
  $Item = Get-Item $Exe
  Write-Host "Installed: $($Item.FullName)"
  Write-Host "Desktop version: $($Item.VersionInfo.ProductVersion)"
  $Running = Get-Process stockhunter-scout -ErrorAction SilentlyContinue
  Write-Host "Running: $([bool]$Running)"
  if ($Item.VersionInfo.ProductVersion -ne $Version) {
    Write-Warning "Desktop version differs from source version $Version."
  }
} else {
  Write-Warning "Installed Scout desktop executable was not found."
}

Write-Host ""
Write-Host "JSON report: $OutputPath" -ForegroundColor Cyan
if ($Code -ne 0) { throw "Scout E2E validation reported one or more hard failures." }
Write-Host "SCOUT END-TO-END AUTOMATED CHECKS PASS" -ForegroundColor Green
Write-Host "Manual phone receipt and Windows toast/sound confirmation may still be required." -ForegroundColor Yellow
