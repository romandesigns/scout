$ErrorActionPreference = "Stop"

$Version       = "6.5.1"
$Root          = "D:\wavystack\scout-v6.2.0-repo"
$Work          = "D:\wavystack\release-v$Version"
$PackageRoot   = Join-Path $Work "scout-v$Version"
$Archive       = Join-Path $Work "scout-v$Version-production-source.zip"
$EnvFile       = Join-Path $Root ".env"
$Evidence      = Join-Path $Root "scout-v$Version-vps-preflight.txt"

$VpsUser       = "wavystack"
$VpsHost       = "srv1170872"
$RemoteStage   = "/opt/apps/scout-v$Version-stage"
$RemoteArchive = "/tmp/scout-v$Version-production-source.zip"
$RemoteEnv     = "/tmp/scout-v$Version.production.env"
$CandidateImage = "scout-v651-candidate:latest"

Set-Location $Root

function Assert-LastExitCode([string]$Message) {
    if ($LASTEXITCODE -ne 0) {
        throw $Message
    }
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "SCOUT v$Version - VPS STAGE + PREFLIGHT ONLY" -ForegroundColor Cyan
Write-Host "NO PRODUCTION CUTOVER WILL OCCUR" -ForegroundColor Yellow
Write-Host "============================================================" -ForegroundColor Cyan

# ---------------------------------------------------------------------
# 1. LOCAL RELEASE GATES
# ---------------------------------------------------------------------
Write-Host "`n=== LOCAL RELEASE GATES ===" -ForegroundColor Cyan

if (-not (Test-Path $EnvFile)) {
    throw "Missing local .env: $EnvFile"
}

$ActualVersion = (Get-Content ".\VERSION" -Raw).Trim()
if ($ActualVersion -ne $Version) {
    throw "VERSION is '$ActualVersion'; expected '$Version'."
}

$TauriVersion = (
    Select-String -Path ".\web\src-tauri\Cargo.toml" -Pattern '^version\s*=\s*"([^"]+)"' |
    Select-Object -First 1
).Matches.Groups[1].Value

if ($TauriVersion -ne $Version) {
    throw "Tauri version is '$TauriVersion'; expected '$Version'."
}

$SwText = Get-Content ".\web\public\sw.js" -Raw
if ($SwText -notmatch 'const VERSION="6\.5\.1";') {
    throw "Service worker is not stamped v$Version."
}

if (-not (Test-Path ".\rust\market-replay\Cargo.toml")) {
    throw "Rust source is missing."
}

git diff --check
Assert-LastExitCode "git diff --check failed."

python -m pytest -q
Assert-LastExitCode "Python tests failed."

cargo test --manifest-path .\rust\market-replay\Cargo.toml
Assert-LastExitCode "Rust tests failed."

Write-Host "Local gates passed." -ForegroundColor Green

# ---------------------------------------------------------------------
# 2. BUILD SANITIZED RELEASE TREE
# ---------------------------------------------------------------------
Write-Host "`n=== BUILD SANITIZED RELEASE TREE ===" -ForegroundColor Cyan

if (Test-Path $Work) {
    Remove-Item $Work -Recurse -Force
}
New-Item -ItemType Directory -Path $PackageRoot -Force | Out-Null

$ExcludeDirectories = @(
    ".git", ".venv", "venv", "env",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "node_modules", ".next", "target",
    "release", "data", "charts", "ntfy-data"
)

$ExcludeFiles = @(
    ".env", ".env.local", ".env.production", ".env.development",
    "scout-v6.5.1-env-key-audit.txt",
    "scout-v6.5.1-source-deployment-state.txt",
    "scout-v6.5.1-post-deployment-state.txt",
    "scout-v6.5.1-production-health.txt",
    "scout-v6.5.1-production-deployment.txt",
    "scout-v6.5.1-vps-preflight.txt",
    "validation-v6.5.0-hybrid-integration.txt",
    "validation-v6.5.0-hybrid-integration-final.txt",
    "validation-v6.5.1-final.txt"
)

Get-ChildItem $Root -Force | ForEach-Object {
    if ($_.PSIsContainer) {
        if ($_.Name -notin $ExcludeDirectories) {
            Copy-Item $_.FullName (Join-Path $PackageRoot $_.Name) -Recurse -Force
        }
    }
    else {
        if ($_.Name -notin $ExcludeFiles) {
            Copy-Item $_.FullName (Join-Path $PackageRoot $_.Name) -Force
        }
    }
}

# Allow .env.example, block private env files.
$PrivateEnvFiles = Get-ChildItem $PackageRoot -Recurse -Force -File |
    Where-Object {
        $_.Name -eq ".env" -or
        ($_.Name -like ".env.*" -and $_.Name -ne ".env.example")
    }

if ($PrivateEnvFiles) {
    $PrivateEnvFiles | Select-Object FullName
    throw "Private .env-style file entered the release package."
}

if ((Get-Content (Join-Path $PackageRoot "VERSION") -Raw).Trim() -ne $Version) {
    throw "Release tree VERSION mismatch."
}

if (-not (Test-Path (Join-Path $PackageRoot "rust\market-replay\Cargo.toml"))) {
    throw "Release tree lost Rust source."
}

Write-Host "Sanitized release tree verified." -ForegroundColor Green

# ---------------------------------------------------------------------
# 3. CREATE ARCHIVE
# ---------------------------------------------------------------------
Write-Host "`n=== CREATE ARCHIVE ===" -ForegroundColor Cyan

Compress-Archive `
    -Path "$PackageRoot\*" `
    -DestinationPath $Archive `
    -CompressionLevel Optimal `
    -Force

$ArchiveHash = (Get-FileHash $Archive -Algorithm SHA256).Hash
$ArchiveSize = [math]::Round((Get-Item $Archive).Length / 1MB, 2)

Write-Host "Archive: $Archive"
Write-Host "Size MB: $ArchiveSize"
Write-Host "SHA256: $ArchiveHash"

# ---------------------------------------------------------------------
# 4. UPLOAD SOURCE + ENV SEPARATELY
# ---------------------------------------------------------------------
Write-Host "`n=== UPLOAD TO VPS ===" -ForegroundColor Cyan
Write-Host "You may be prompted for your SSH key passphrase/password." -ForegroundColor Yellow

scp $Archive "${VpsUser}@${VpsHost}:$RemoteArchive"
Assert-LastExitCode "Source archive upload failed."

scp $EnvFile "${VpsUser}@${VpsHost}:$RemoteEnv"
Assert-LastExitCode "Production .env upload failed."

# ---------------------------------------------------------------------
# 5. REMOTE STAGING + PREFLIGHT
#    IMPORTANT: DOES NOT STOP OR RECREATE PRODUCTION
# ---------------------------------------------------------------------
Write-Host "`n=== VPS STAGING + PREFLIGHT ===" -ForegroundColor Cyan

$RemoteScript = @'
set -Eeuo pipefail

VERSION="6.5.1"
LIVE="/opt/apps/scout"
STAGE="/home/wavystack/scout-v6.5.1-stage"
ARCHIVE="/tmp/scout-v6.5.1-production-source.zip"
NEWENV="/tmp/scout-v6.5.1.production.env"
IMAGE="scout-v651-candidate:latest"

echo "============================================================"
echo "SCOUT v${VERSION} VPS PREFLIGHT"
echo "UTC: $(date -u)"
echo "IMPORTANT: LIVE PRODUCTION WILL NOT BE STOPPED"
echo "============================================================"

test -d "$LIVE"
test -f "$ARCHIVE"
test -f "$NEWENV"

echo
echo "===== CURRENT PRODUCTION BEFORE PREFLIGHT ====="
cat "$LIVE/VERSION" 2>/dev/null || true
docker compose -f "$LIVE/compose.yaml" ps

echo
echo "===== STAGE RELEASE ====="
rm -rf "$STAGE"
mkdir -p "$STAGE"

python3 - <<'PY'
import zipfile
archive = "/tmp/scout-v6.5.1-production-source.zip"
stage = "/home/wavystack/scout-v6.5.1-stage"
with zipfile.ZipFile(archive) as z:
    z.extractall(stage)
PY

ACTUAL="$(tr -d '\r\n' < "$STAGE/VERSION")"
echo "Staged VERSION: $ACTUAL"
test "$ACTUAL" = "$VERSION"
test -f "$STAGE/rust/market-replay/Cargo.toml"
test -f "$STAGE/Dockerfile"
test -f "$STAGE/compose.yaml"

cp "$NEWENV" "$STAGE/.env"
chmod 600 "$STAGE/.env"

if grep -q '^APP_VERSION=' "$STAGE/.env"; then
    sed -i "s/^APP_VERSION=.*/APP_VERSION=$VERSION/" "$STAGE/.env"
else
    printf '\nAPP_VERSION=%s\n' "$VERSION" >> "$STAGE/.env"
fi

echo
echo "===== BUILD CANDIDATE IMAGE ====="
cd "$STAGE"
docker build -t "$IMAGE" .

echo
echo "===== VERIFY CANDIDATE IMAGE CONTENT ====="
docker run --rm --entrypoint sh "$IMAGE" -c '
set -e
test -x /usr/local/bin/scout-market-replay
command -v scout-market-replay
ls -lh /usr/local/bin/scout-market-replay
test -f /srv/app/main.py
test -d /srv/web-out
echo "Rust + Python + web runtime content verified."
'

echo
echo "===== ALPACA AUTH PREFLIGHT ====="
docker run --rm \
    --env-file "$STAGE/.env" \
    --entrypoint python \
    "$IMAGE" \
    -c '
import os, sys, requests

key = os.getenv("ALPACA_API_KEY")
secret = os.getenv("ALPACA_API_SECRET")
base = os.getenv("ALPACA_TRADING_BASE", "https://paper-api.alpaca.markets").rstrip("/")

if not key or not secret:
    print("FAIL: ALPACA_API_KEY or ALPACA_API_SECRET missing")
    raise SystemExit(31)

r = requests.get(
    base + "/v2/assets",
    headers={
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
    },
    params={"status": "active", "asset_class": "us_equity"},
    timeout=20,
)

print("Alpaca HTTP status:", r.status_code)
if r.status_code != 200:
    print("FAIL: Alpaca authentication did not return HTTP 200.")
    raise SystemExit(32)

payload = r.json()
print("Alpaca authentication: PASS")
print("Active assets returned:", len(payload))
'

echo
echo "===== CANDIDATE RUST STREAM SMOKE ====="
printf '%s\n' '{"schema":"scout.market-event.v1","event_type":"trade","symbol":"TEST","ts":1.0,"price":1.0,"size":100.0,"conditions":[]}' \
  | docker run --rm -i "$IMAGE" scout-market-replay --stream \
  | head -5 || true

echo
echo "===== VERIFY LIVE PRODUCTION WAS NOT TOUCHED ====="
docker compose -f "$LIVE/compose.yaml" ps
curl -fsS http://127.0.0.1:18081/healthz
echo

echo
echo "============================================================"
echo "PREFLIGHT PASS"
echo "Candidate image: $IMAGE"
echo "Stage directory: $STAGE"
echo "LIVE PRODUCTION REMAINS UNCHANGED"
echo "============================================================"
'@

$RemoteScript |
    ssh "${VpsUser}@${VpsHost}" "bash -s" *>&1 |
    Tee-Object -FilePath $Evidence

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "PREFLIGHT FAILED. PRODUCTION WAS NOT INTENTIONALLY TOUCHED." -ForegroundColor Red
    Write-Host "Upload this file to ChatGPT:" -ForegroundColor Yellow
    Write-Host $Evidence
    exit 1
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "VPS PREFLIGHT COMPLETE" -ForegroundColor Green
Write-Host "NO PRODUCTION CUTOVER HAS OCCURRED" -ForegroundColor Yellow
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Upload this file to ChatGPT:"
Write-Host $Evidence
