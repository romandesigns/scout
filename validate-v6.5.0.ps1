param(
    [string]$Root = "D:\wavystack\scout-v6.2.0-repo",
    [string]$BaseUrl = "https://srv1170872.tail86523.ts.net:8444"
)

$ErrorActionPreference = "Stop"
Set-Location $Root

Write-Host "=== Scout v6.5.0 validation ===" -ForegroundColor Cyan

git diff --check

Write-Host "`n--- Python tests ---" -ForegroundColor Cyan
python -m pytest -q

Write-Host "`n--- Rust tests ---" -ForegroundColor Cyan
cargo test --manifest-path .\rust\market-replay\Cargo.toml

Write-Host "`n--- Rust release build ---" -ForegroundColor Cyan
cargo build --release --locked --manifest-path .\rust\market-replay\Cargo.toml

Write-Host "`n--- Web dependencies/build ---" -ForegroundColor Cyan
Push-Location .\web
try {
    bun install --frozen-lockfile
    bun run build
}
finally {
    Pop-Location
}

Write-Host "`n--- Docker build ---" -ForegroundColor Cyan
docker compose build scout

Write-Host "`nLocal build validation complete." -ForegroundColor Green
Write-Host "After deployment, run the following health checks:" -ForegroundColor Yellow
Write-Host "  Invoke-RestMethod '$BaseUrl/healthz' | ConvertTo-Json -Depth 8"
Write-Host "  Invoke-RestMethod '$BaseUrl/api/status' | ConvertTo-Json -Depth 12"
