param(
    [Parameter(Mandatory=$true)][string]$Start,
    [Parameter(Mandatory=$true)][string]$End,
    [int]$ControlRate = 40,
    [int]$MaxSymbols = 0,
    [string]$Symbols = "",
    [string]$Tag = "",
    [switch]$Sample,
    [int]$CapPerTier = 60,
    [int]$ControlCap = 150
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Get-Content ".env" | ForEach-Object {
    $Line = $_.Trim()
    if ($Line -and -not $Line.StartsWith("#") -and $Line.Contains("=")) {
        $Name, $Value = $Line -split "=", 2
        [Environment]::SetEnvironmentVariable($Name.Trim(), $Value.Trim().Trim('"'), "Process")
    }
}

$Stamp = if ($Tag) { $Tag } else { "$Start-to-$End" }
$Dir = ".\data\optimization\backtest"
New-Item -ItemType Directory -Force -Path $Dir | Out-Null
$Movers = "$Dir\movers-$Stamp.jsonl"
$ReplaySet = $Movers
$Findings = "$Dir\findings-$Stamp.jsonl"
$Report = "$Dir\report-$Stamp.json"

$FinderArgs = @("--start", $Start, "--end", $End, "--output", $Movers, "--control-rate", $ControlRate)
if ($MaxSymbols -gt 0) { $FinderArgs += @("--max-symbols", $MaxSymbols) }
if ($Symbols) { $FinderArgs += @("--symbols", $Symbols) }

$StageCount = if ($Sample) { 4 } else { 3 }
Write-Host "=== Stage 1/${StageCount}: ground-truth mover finder (full population) ===" -ForegroundColor Cyan
python -m scripts.historical_mover_finder @FinderArgs
if ($LASTEXITCODE -ne 0) { throw "historical_mover_finder failed" }

if ($Sample) {
    $ReplaySet = "$Dir\movers-$Stamp-sample.jsonl"
    Write-Host "`n=== Stage 2/${StageCount}: stratified sampling (bounds replay cost) ===" -ForegroundColor Cyan
    python -m scripts.sample_movers --input $Movers --output $ReplaySet --cap-per-tier $CapPerTier --control-cap $ControlCap
    if ($LASTEXITCODE -ne 0) { throw "sample_movers failed" }
}

Write-Host "`n=== Stage $($StageCount - 1)/${StageCount}: replay through Scout's real detector ===" -ForegroundColor Cyan
python -m scripts.historical_backtest --movers $ReplaySet --output $Findings
if ($LASTEXITCODE -ne 0) { throw "historical_backtest failed" }

Write-Host "`n=== Stage $StageCount/${StageCount}: recall + precision scoring ===" -ForegroundColor Cyan
python -m scripts.backtest_scorer --movers $ReplaySet --findings $Findings --output $Report
if ($LASTEXITCODE -ne 0) { throw "backtest_scorer failed" }

Write-Host "`nReport: $Report" -ForegroundColor Green
