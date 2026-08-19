param(
    [string]$ApiBase = "https://srv1170872.tail86523.ts.net:8444",
    [string]$Dataset = ".\data\optimization\recall-opportunity.jsonl",
    [string]$Output = ".\data\optimization\recall-opportunity-report.json",
    [ValidateSet("Sample","Run","Report")]
    [string]$Mode = "Sample",
    [int]$IntervalSeconds = 30,
    [int]$DurationMinutes = 480,
    [int]$Top = 50,
    [int]$FindingsLimit = 500
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$Python = if (Get-Command python -ErrorAction SilentlyContinue) { "python" }
          elseif (Get-Command py -ErrorAction SilentlyContinue) { "py" }
          else { throw "Python was not found." }

New-Item -ItemType Directory -Path (Split-Path $Dataset -Parent) -Force | Out-Null

switch ($Mode) {
    "Sample" {
        & $Python ".\scripts\recall_opportunity.py" sample `
          --api-base $ApiBase --dataset $Dataset --top $Top --findings-limit $FindingsLimit
    }
    "Run" {
        & $Python ".\scripts\recall_opportunity.py" run `
          --api-base $ApiBase --dataset $Dataset --top $Top --findings-limit $FindingsLimit `
          --interval-seconds $IntervalSeconds --duration-minutes $DurationMinutes
    }
    "Report" {
        & $Python ".\scripts\recall_opportunity.py" report `
          --dataset $Dataset --output $Output
    }
}
if ($LASTEXITCODE -ne 0) { throw "Recall/opportunity audit failed with exit code $LASTEXITCODE." }
