param(
    [int]$IntervalSeconds = 30,
    [int]$DurationMinutes = 480,
    [string]$ApiBase = "https://srv1170872.tail86523.ts.net:8444"
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$Stamp = Get-Date -Format "yyyyMMdd"
$Dataset = ".\data\optimization\recall-opportunity-$Stamp.jsonl"
$Report  = ".\data\optimization\recall-opportunity-$Stamp-report.json"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "SCOUT RIGHT-TAIL / MONSTER-MOVER MONITOR" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "Dataset: $Dataset"
Write-Host "Interval: $IntervalSeconds sec"
Write-Host "Duration: $DurationMinutes min"
Write-Host "Press Ctrl+C any time; collected samples remain valid." -ForegroundColor Yellow

try {
    & ".\validate-recall-opportunity.ps1" `
      -Mode Run -ApiBase $ApiBase -Dataset $Dataset `
      -IntervalSeconds $IntervalSeconds -DurationMinutes $DurationMinutes
}
finally {
    Write-Host ""
    Write-Host "Building report from accumulated samples..." -ForegroundColor Cyan
    & ".\validate-recall-opportunity.ps1" -Mode Report -Dataset $Dataset -Output $Report
    Write-Host ""
    Write-Host "Report: $Report" -ForegroundColor Green
}
