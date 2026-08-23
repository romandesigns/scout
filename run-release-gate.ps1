[CmdletBinding()]
param(
  [switch]$SkipRust,
  [switch]$SkipWebBuild,
  [switch]$Docker
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$PSNativeCommandUseErrorActionPreference = $true
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

python .\scripts\check_release_integrity.py
python -m pytest -q

Push-Location .\web
try {
  bun run typecheck
  if (-not $SkipWebBuild) { bun run build }
}
finally { Pop-Location }

if (-not $SkipRust) {
  cargo test --manifest-path .\rust\market-replay\Cargo.toml
}

if ($Docker) {
  $Version = (Get-Content .\VERSION -Raw).Trim()
  $Image = "stockhunter-scout:$Version-gate"
  docker build --tag $Image .
  docker run --rm $Image python -c "from app.config import settings; assert settings.app_version == '$Version'; print(settings.app_version)"
}

git diff --check
Write-Host "Scout release gate passed." -ForegroundColor Green
