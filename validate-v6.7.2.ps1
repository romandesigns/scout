$ErrorActionPreference="Stop"
Set-Location $PSScriptRoot
$Version=(Get-Content .\VERSION -Raw).Trim()
if($Version-ne"6.7.2"){throw "Expected 6.7.2, found $Version"}
python -m pytest -q tests/test_recall_opportunity.py
if($LASTEXITCODE-ne 0){throw "Recall/opportunity tests failed"}
Write-Host "SCOUT v6.7.2 LOCAL VALIDATION PASS" -ForegroundColor Green
