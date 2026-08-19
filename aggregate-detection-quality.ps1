param(
    [string]$Glob = ".\detection-quality-*.json",
    [string]$Output = ".\data\optimization\detection-quality-trend.json"
)
$ErrorActionPreference="Stop"
Set-Location $PSScriptRoot
python ".\scripts\aggregate_detection_quality.py" --glob $Glob --output $Output
if ($LASTEXITCODE -ne 0) { throw "Detection-quality trend aggregation failed." }
