param(
    [Parameter(Mandatory=$true)][string]$Movers,
    [Parameter(Mandatory=$true)][string]$Output,
    [int]$ShardCount = 1,
    [int]$ShardIndex = 0
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

python -m scripts.historical_backtest --movers $Movers --output $Output --shard-count $ShardCount --shard-index $ShardIndex
if ($LASTEXITCODE -ne 0) { throw "historical_backtest failed" }
