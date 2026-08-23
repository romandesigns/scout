param(
    [Parameter(Mandatory=$true)][string]$Population,
    [Parameter(Mandatory=$true)][string]$Output,
    [int]$MaxMoversPerDate = 10,
    [int]$MaxControlsPerDate = 10,
    [int]$NegativeRatio = 0
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

python -m scripts.build_imminent_training_data `
    --population $Population `
    --output $Output `
    --max-movers-per-date $MaxMoversPerDate `
    --max-controls-per-date $MaxControlsPerDate `
    --negative-ratio $NegativeRatio
if ($LASTEXITCODE -ne 0) { throw "build_imminent_training_data failed" }
