param(
  [Parameter(Mandatory=$true)][string]$StartDate,
  [Parameter(Mandatory=$true)][string]$EndDate,
  [string]$Symbols = "",
  [ValidateRange(1, 200)][int]$MaxSymbolsPerSession = 40
)

$ErrorActionPreference = "Stop"
if ($PSVersionTable.PSVersion.Major -lt 7) { throw "PowerShell 7 or newer is required." }
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
$Dataset = Join-Path $ProjectRoot "data\replay-datasets\calibration-$StartDate-$EndDate.ndjson"
$Arguments = @(
  "-m", "scripts.build_alpaca_calibration",
  "--start-date", $StartDate,
  "--end-date", $EndDate,
  "--max-symbols-per-session", $MaxSymbolsPerSession,
  "--output", $Dataset
)
if ($Symbols) { $Arguments += @("--symbols", $Symbols) }
python @Arguments
if ($LASTEXITCODE -ne 0) { throw "Historical dataset build failed." }
python -m scripts.run_replay $Dataset --output .\data\replays
if ($LASTEXITCODE -ne 0) { throw "Calibration replay failed." }
$Latest = Get-Content .\data\replays\latest.json -Raw | ConvertFrom-Json
$RustReport = Join-Path $ProjectRoot "data\replays\rust-latest.json"
$ParityReport = Join-Path $ProjectRoot "data\replays\parity-latest.json"
cargo run --release --manifest-path .\rust\market-replay\Cargo.toml -- $Dataset --output $RustReport
if ($LASTEXITCODE -ne 0) { throw "Rust market replay failed." }
python -m scripts.compare_replay_parity --python-report $Latest.report_path --rust-report $RustReport --dataset $Dataset --output $ParityReport
if ($LASTEXITCODE -ne 0) { throw "Python/Rust parity comparison failed." }
Write-Host "Calibration complete. Review latest.json, rust-latest.json, and parity-latest.json." -ForegroundColor Green
