param(
  [Parameter(Mandatory=$true)][string]$Symbol,
  [Parameter(Mandatory=$true)][string]$Date,
  [string]$Feed = "sip"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

if (Test-Path ".env") {
  Get-Content ".env" | ForEach-Object {
    $Line = $_.Trim()
    if ($Line -and -not $Line.StartsWith("#") -and $Line.Contains("=")) {
      $Name, $Value = $Line -split "=", 2
      [Environment]::SetEnvironmentVariable($Name.Trim(), $Value.Trim().Trim('"'), "Process")
    }
  }
}

$Ticker = $Symbol.ToUpperInvariant()
$Dataset = Join-Path $ProjectRoot "data\replay-datasets\$Ticker-$Date-$Feed.ndjson"
python -m scripts.build_alpaca_replay --symbol $Ticker --date $Date --feed $Feed --output $Dataset
if ($LASTEXITCODE -ne 0) { throw "Alpaca dataset build failed." }

python -m scripts.run_replay $Dataset --output .\data\replays
if ($LASTEXITCODE -ne 0) { throw "Scout replay failed." }

Write-Host "Replay complete. Open data\replays\latest.json or refresh Scout." -ForegroundColor Green
