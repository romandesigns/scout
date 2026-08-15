[CmdletBinding()]
param(
  [string]$ApiBase = "https://srv1170872.tail86523.ts.net:8444",
  [switch]$SkipApiCheck
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$PSNativeCommandUseErrorActionPreference = $true

if ($PSVersionTable.PSVersion.Major -lt 7) {
  throw "Use PowerShell 7 or newer."
}

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$WebRoot = Join-Path $ProjectRoot "web"
$ReleaseRoot = Join-Path $ProjectRoot "release\windows"

foreach ($Command in @("bun", "cargo", "rustc")) {
  if (-not (Get-Command $Command -ErrorAction SilentlyContinue)) {
    throw "$Command is required. See WINDOWS-BUILD.md for the installation command."
  }
}

if (-not $SkipApiCheck) {
  try {
    $Health = Invoke-RestMethod -Uri "$ApiBase/healthz" -TimeoutSec 10
    if (-not $Health.ok) {
      throw "Scout health returned ok=false."
    }
    Write-Host "Scout API reachable: $ApiBase" -ForegroundColor Green
  }
  catch {
    Write-Warning "Scout API is not reachable yet. The installer can still be built before VPS deployment."
  }
}

$env:NEXT_PUBLIC_SCOUT_API_BASE = $ApiBase.TrimEnd("/")
$env:NEXT_PUBLIC_SCOUT_SAME_ORIGIN = "0"

Push-Location $WebRoot
try {
  bun install
  if ($LASTEXITCODE -ne 0) { throw "bun install failed with exit code $LASTEXITCODE." }
  bun run typecheck
  if ($LASTEXITCODE -ne 0) { throw "TypeScript validation failed with exit code $LASTEXITCODE." }
  bun tauri build --bundles nsis
  if ($LASTEXITCODE -ne 0) { throw "Tauri build failed with exit code $LASTEXITCODE." }
}
finally {
  Pop-Location
}

New-Item -ItemType Directory -Force -Path $ReleaseRoot | Out-Null
$InstallerPath = Join-Path $WebRoot "src-tauri\target\release\bundle\nsis"
if (-not (Test-Path $InstallerPath)) {
  throw "Tauri completed without creating the NSIS output directory."
}
$Installers = Get-ChildItem -Path $InstallerPath -Filter "*.exe" -File

if (-not $Installers) {
  throw "Tauri completed without producing an NSIS installer."
}

foreach ($Installer in $Installers) {
  Copy-Item $Installer.FullName -Destination $ReleaseRoot -Force
}

Write-Host "Windows release ready:" -ForegroundColor Green
Get-ChildItem $ReleaseRoot -File | Select-Object Name, Length, LastWriteTime
