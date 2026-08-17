$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Gate([string]$Name, [scriptblock]$Body) {
    Write-Host "`n=== $Name ===" -ForegroundColor Cyan
    & $Body
    if ($LASTEXITCODE -ne 0) {
        throw "$Name failed."
    }
}

$Version = (Get-Content .\VERSION -Raw).Trim()
if ($Version -ne "6.6.0") {
    throw "VERSION is '$Version', expected 6.6.0."
}

$PkgVersion = (Get-Content .\web\package.json -Raw | ConvertFrom-Json).version
$TauriVersion = (Get-Content .\web\src-tauri\tauri.conf.json -Raw | ConvertFrom-Json).version

$CargoMatch = Select-String `
    -Path .\web\src-tauri\Cargo.toml `
    -Pattern '^version\s*=\s*"([^"]+)"' |
    Select-Object -First 1

if (-not $CargoMatch) {
    throw "Could not determine Tauri Cargo.toml version."
}

$CargoVersion = $CargoMatch.Matches[0].Groups[1].Value
$Sw = Get-Content .\web\public\sw.js -Raw

foreach ($Pair in @(
    @("web/package.json", $PkgVersion),
    @("tauri.conf.json", $TauriVersion),
    @("Cargo.toml", $CargoVersion)
)) {
    if ($Pair[1] -ne "6.6.0") {
        throw "$($Pair[0]) version is '$($Pair[1])'."
    }
}

if ($Sw -notmatch 'const VERSION="6\.6\.0";') {
    throw "Service worker version mismatch."
}

Write-Host "Release identity: PASS" -ForegroundColor Green

Write-Host "`n=== UI PRIMITIVE AUDIT ===" -ForegroundColor Cyan

$UiSourceFiles = Get-ChildItem .\web -Recurse -File -Include *.tsx,*.ts |
    Where-Object {
        $_.FullName -notmatch '[\\/](node_modules|\.next|out)[\\/]' -and
        $_.FullName -notmatch '[\\/]src-tauri[\\/]target[\\/]'
    }

# IMPORTANT: Select-String is case-insensitive by default.
# We specifically want literal native HTML <select>, not our React <Select> component.
$NativeSelect = $UiSourceFiles |
    Select-String -CaseSensitive -Pattern '<select\b'

$NativeTitles = $UiSourceFiles |
    Select-String -CaseSensitive -Pattern '<(button|span|div|svg|g|a|input|img)[^>]*\stitle='

$BrowserDialogs = $UiSourceFiles |
    Select-String -CaseSensitive -Pattern '\b(window\.)?(alert|confirm|prompt)\s*\('

if ($NativeSelect) {
    $NativeSelect
    throw "Native HTML select remains in Scout UI."
}

if ($NativeTitles) {
    $NativeTitles
    throw "Native title tooltip remains on an interactive/rendered element."
}

if ($BrowserDialogs) {
    $Filtered = $BrowserDialogs |
        Where-Object { $_.Line -notmatch 'beforeinstallprompt|install\.prompt' }

    if ($Filtered) {
        $Filtered
        throw "Browser-native dialog API remains in Scout UI."
    }
}

Write-Host "UI primitive audit: PASS" -ForegroundColor Green

Gate "git diff --check" {
    git diff --check
}

Gate "Python tests" {
    python -m pytest -q
}

Gate "Rust tests" {
    cargo test --manifest-path .\rust\market-replay\Cargo.toml
}

Write-Host "`n=== WEB INSTALL / BUILD ===" -ForegroundColor Cyan

Push-Location .\web
try {
    bun install
    if ($LASTEXITCODE -ne 0) {
        throw "bun install failed."
    }

    bun run build
    if ($LASTEXITCODE -ne 0) {
        throw "web build failed."
    }
}
finally {
    Pop-Location
}

Write-Host "`n============================================================" -ForegroundColor Green
$Page = Get-Content (Join-Path $Root "web\app\page.tsx") -Raw
if ($Page -notmatch 'radar-scope-count') { throw "Radar count badges are missing." }
if ($Page -notmatch '2\*60\*60') { throw "Developing freshness window is missing." }
if ($Page -notmatch '45\*60') { throw "Actionable freshness window is missing." }

$DbSource = Get-Content (Join-Path $Root "app\db.py") -Raw
$CatalystSource = Get-Content (Join-Path $Root "app\catalysts.py") -Raw
if ($DbSource -notmatch 'ix_findings_hybrid_key_time') { throw "hybrid_key performance index is missing." }
if ($DbSource -match 'SELECT GROUP_CONCAT\(DISTINCT COALESCE\(f2\.engine_source') { throw "Correlated hybrid precision subquery remains." }
if ($CatalystSource -notmatch 'await asyncio\.to_thread\(self\.store\.claim_seen') { throw "Alpaca news DB claim is not isolated from the event loop." }

Write-Host "SCOUT v6.6.0 LOCAL VALIDATION PASS" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
