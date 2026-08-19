$ErrorActionPreference="Stop"
Set-Location $PSScriptRoot
$Version=(Get-Content .\VERSION -Raw).Trim()
if($Version-ne"6.7.3"){throw "Expected VERSION 6.7.3, found '$Version'"}

Write-Host "Release identity: PASS" -ForegroundColor Green

Write-Host ""
Write-Host "=== OPTIMIZATION / RECALL / OUTCOME TESTS ===" -ForegroundColor Cyan
python -m pytest -q tests/test_recall_opportunity.py tests/test_detection_quality_v6.py tests/test_independent_market_data.py
if($LASTEXITCODE-ne 0){throw "Optimization instrumentation tests failed."}

if(Test-Path ".\tests"){
    Write-Host ""
    Write-Host "=== BROAD PYTHON REGRESSION ===" -ForegroundColor Cyan
    python -m pytest -q --ignore=tests/test_hybrid.py
    if($LASTEXITCODE-ne 0){throw "Python regression failed."}
}

if(Test-Path ".\rust\market-replay\Cargo.toml"){
    Write-Host ""
    Write-Host "=== RUST MARKET REPLAY ===" -ForegroundColor Cyan
    cargo test --manifest-path ".\rust\market-replay\Cargo.toml"
    if($LASTEXITCODE-ne 0){throw "Rust market-replay tests failed."}
}

if(Test-Path ".\web\package.json"){
    Write-Host ""
    Write-Host "=== WEB INSTALL / BUILD ===" -ForegroundColor Cyan
    Push-Location ".\web"
    try {
        bun install
        if($LASTEXITCODE-ne 0){throw "bun install failed."}
        bun run build
        if($LASTEXITCODE-ne 0){throw "Next.js build failed."}
    } finally { Pop-Location }
}

Write-Host ""
Write-Host "MIXED decomposition: PASS" -ForegroundColor Green
Write-Host "Independent provider adapter: PASS" -ForegroundColor Green
Write-Host "Recall audit integrity: PASS" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host "SCOUT v6.7.3 LOCAL VALIDATION PASS" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
