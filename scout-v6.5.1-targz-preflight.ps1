$ErrorActionPreference = "Stop"

$Version        = "6.5.1"
$Root           = "D:\wavystack\scout-v6.2.0-repo"
$Work           = "D:\wavystack\release-v$Version"
$Archive        = Join-Path $Work "scout-v$Version-production-source.tar.gz"
$EnvFile        = Join-Path $Root ".env"
$Evidence       = Join-Path $Root "scout-v$Version-vps-targz-preflight.txt"

$VpsUser        = "wavystack"
$VpsHost        = "srv1170872"
$RemoteArchive  = "/tmp/scout-v$Version-production-source.tar.gz"
$RemoteEnv      = "/tmp/scout-v$Version.production.env"
$RemoteStage    = "/home/wavystack/scout-v$Version-stage"
$CandidateImage = "scout-v651-candidate:latest"

function Assert-Exit([string]$Message) {
    if ($LASTEXITCODE -ne 0) { throw $Message }
}

Set-Location $Root

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "SCOUT v$Version TAR.GZ STAGE PREFLIGHT" -ForegroundColor Cyan
Write-Host "NO PRODUCTION CUTOVER WILL OCCUR" -ForegroundColor Yellow
Write-Host "============================================================" -ForegroundColor Cyan

# ---------------------------------------------------------------------
# 1. LOCAL RELEASE IDENTITY / TEST GATES
# ---------------------------------------------------------------------
Write-Host "`n=== LOCAL RELEASE GATES ===" -ForegroundColor Cyan

if (-not (Test-Path $EnvFile)) { throw "Missing local .env: $EnvFile" }

$ActualVersion = (Get-Content ".\VERSION" -Raw).Trim()
if ($ActualVersion -ne $Version) {
    throw "VERSION is '$ActualVersion'; expected '$Version'."
}

$TauriMatch = Select-String `
    -Path ".\web\src-tauri\Cargo.toml" `
    -Pattern '^version\s*=\s*"([^"]+)"' |
    Select-Object -First 1

if (-not $TauriMatch) { throw "Could not find Tauri version." }

$TauriVersion = $TauriMatch.Matches[0].Groups[1].Value
if ($TauriVersion -ne $Version) {
    throw "Tauri version is '$TauriVersion'; expected '$Version'."
}

$Sw = Get-Content ".\web\public\sw.js" -Raw
if ($Sw -notmatch 'const VERSION="6\.5\.1";') {
    throw "Service worker version is not 6.5.1."
}

if (-not (Test-Path ".\rust\market-replay\Cargo.toml")) {
    throw "Local Rust source is missing."
}

git diff --check
Assert-Exit "git diff --check failed."

python -m pytest -q
Assert-Exit "Python tests failed."

cargo test --manifest-path ".\rust\market-replay\Cargo.toml"
Assert-Exit "Rust tests failed."

Write-Host "Local gates passed." -ForegroundColor Green

# ---------------------------------------------------------------------
# 2. CREATE CLEAN TAR.GZ DIRECTLY FROM REPO
# ---------------------------------------------------------------------
Write-Host "`n=== CREATE TAR.GZ RELEASE ARTIFACT ===" -ForegroundColor Cyan

if (Test-Path $Work) { Remove-Item $Work -Recurse -Force }
New-Item -ItemType Directory -Path $Work -Force | Out-Null

# Windows ships bsdtar as tar.exe. Exclusions are recursive.
$TarArgs = @(
    "-czf", $Archive,

    "--exclude=.git",
    "--exclude=.venv",
    "--exclude=venv",
    "--exclude=env",
    "--exclude=__pycache__",
    "--exclude=.pytest_cache",
    "--exclude=.mypy_cache",
    "--exclude=.ruff_cache",
    "--exclude=node_modules",
    "--exclude=.next",
    "--exclude=target",
    "--exclude=data",
    "--exclude=charts",
    "--exclude=ntfy-data",

    "--exclude=.env",
    "--exclude=.env.local",
    "--exclude=.env.production",
    "--exclude=.env.development",

    "--exclude=scout-v6.5.1-env-key-audit.txt",
    "--exclude=scout-v6.5.1-source-deployment-state.txt",
    "--exclude=scout-v6.5.1-post-deployment-state.txt",
    "--exclude=scout-v6.5.1-production-health.txt",
    "--exclude=scout-v6.5.1-production-deployment.txt",
    "--exclude=scout-v6.5.1-vps-preflight.txt",
    "--exclude=scout-v6.5.1-vps-targz-preflight.txt",
    "--exclude=scout-v6.5.1-stage-diagnostic.txt",
    "--exclude=validation-v6.5.0-hybrid-integration.txt",
    "--exclude=validation-v6.5.0-hybrid-integration-final.txt",
    "--exclude=validation-v6.5.1-final.txt",

    "."
)

& tar @TarArgs
Assert-Exit "tar.gz creation failed."

# ---------------------------------------------------------------------
# 3. VERIFY THE ACTUAL ARCHIVE BEFORE UPLOAD
# ---------------------------------------------------------------------
Write-Host "`n=== VERIFY ARCHIVE CONTENT ===" -ForegroundColor Cyan

$ArchiveEntries = & tar -tzf $Archive
Assert-Exit "Could not list archive."

function Assert-ArchiveContains([string]$Pattern, [string]$Label) {
    if (-not ($ArchiveEntries | Where-Object { $_ -match $Pattern })) {
        throw "Archive missing required item: $Label"
    }
}

Assert-ArchiveContains '(^|^\./)VERSION$' "VERSION"
Assert-ArchiveContains '(^|^\./)Dockerfile$' "Dockerfile"
Assert-ArchiveContains '(^|^\./)compose\.yaml$' "compose.yaml"
Assert-ArchiveContains '(^|^\./)requirements\.txt$' "requirements.txt"
Assert-ArchiveContains '(^|^\./)rust/market-replay/Cargo\.toml$' "rust/market-replay/Cargo.toml"
Assert-ArchiveContains '(^|^\./)rust/market-replay/src/lib\.rs$' "rust/market-replay/src/lib.rs"

$PrivateEnvEntries = $ArchiveEntries | Where-Object {
    $leaf = Split-Path $_ -Leaf
    $leaf -eq ".env" -or
    ($leaf -like ".env.*" -and $leaf -ne ".env.example")
}
if ($PrivateEnvEntries) {
    $PrivateEnvEntries | ForEach-Object { Write-Host "PRIVATE ENV IN ARCHIVE: $_" -ForegroundColor Red }
    throw "Private .env-style file entered archive."
}

$TargetEntries = $ArchiveEntries | Where-Object {
    $_ -match '(^|/)target(/|$)' -or
    $_ -match '(^|/)node_modules(/|$)' -or
    $_ -match '(^|/)\.next(/|$)'
}
if ($TargetEntries) {
    $TargetEntries | Select-Object -First 20
    throw "Build-output directories entered archive."
}

$ArchiveHash = (Get-FileHash $Archive -Algorithm SHA256).Hash
$ArchiveMB = [math]::Round((Get-Item $Archive).Length / 1MB, 2)

Write-Host "Archive verified." -ForegroundColor Green
Write-Host "Archive: $Archive"
Write-Host "Size MB: $ArchiveMB"
Write-Host "SHA256: $ArchiveHash"

# ---------------------------------------------------------------------
# 4. UPLOAD ARCHIVE AND ENV SEPARATELY
# ---------------------------------------------------------------------
Write-Host "`n=== UPLOAD TO VPS ===" -ForegroundColor Cyan

scp $Archive "${VpsUser}@${VpsHost}:$RemoteArchive"
Assert-Exit "Archive upload failed."

scp $EnvFile "${VpsUser}@${VpsHost}:$RemoteEnv"
Assert-Exit ".env upload failed."

# ---------------------------------------------------------------------
# 5. STAGE + BUILD + PREFLIGHT ON VPS
#    DOES NOT STOP OR RECREATE LIVE PRODUCTION
# ---------------------------------------------------------------------
Write-Host "`n=== VPS STAGE + BUILD PREFLIGHT ===" -ForegroundColor Cyan

$RemoteScript = @'
set -Eeuo pipefail
trap 'rc=$?; echo; echo "ERROR: preflight failed at line $LINENO (exit $rc)"; exit $rc' ERR

VERSION="6.5.1"
LIVE="/opt/apps/scout"
STAGE="/home/wavystack/scout-v6.5.1-stage"
ARCHIVE="/tmp/scout-v6.5.1-production-source.tar.gz"
NEWENV="/tmp/scout-v6.5.1.production.env"
IMAGE="scout-v651-candidate:latest"

echo "============================================================"
echo "SCOUT v${VERSION} TAR.GZ VPS PREFLIGHT"
echo "UTC: $(date -u)"
echo "LIVE PRODUCTION WILL NOT BE STOPPED"
echo "============================================================"

test -d "$LIVE"
test -f "$ARCHIVE"
test -f "$NEWENV"

echo
echo "===== CURRENT PRODUCTION ====="
cat "$LIVE/VERSION" 2>/dev/null || true
docker compose -f "$LIVE/compose.yaml" ps

echo
echo "===== EXTRACT CANDIDATE ====="
rm -rf "$STAGE"
mkdir -p "$STAGE"
tar --warning=no-timestamp --touch -xzf "$ARCHIVE" -C "$STAGE"

ACTUAL="$(tr -d '\r\n' < "$STAGE/VERSION")"
echo "Staged VERSION: $ACTUAL"
test "$ACTUAL" = "$VERSION"

echo
echo "===== REQUIRED STAGED FILES ====="
for f in \
    Dockerfile \
    compose.yaml \
    requirements.txt \
    rust/market-replay/Cargo.toml \
    rust/market-replay/src/lib.rs
do
    test -f "$STAGE/$f"
    echo "FOUND: $f"
done

echo
echo "===== INSTALL CANDIDATE ENV ====="
cp "$NEWENV" "$STAGE/.env"
chmod 600 "$STAGE/.env"

if grep -q '^APP_VERSION=' "$STAGE/.env"; then
    sed -i "s/^APP_VERSION=.*/APP_VERSION=$VERSION/" "$STAGE/.env"
else
    printf '\nAPP_VERSION=%s\n' "$VERSION" >> "$STAGE/.env"
fi

echo ".env variable count: $(grep -Ec '^[A-Za-z_][A-Za-z0-9_]*=' "$STAGE/.env" || true)"
echo "No secret values printed."

echo
echo "===== BUILD CANDIDATE IMAGE ====="
cd "$STAGE"
docker build -t "$IMAGE" .

echo
echo "===== VERIFY CANDIDATE IMAGE ====="
docker run --rm --entrypoint sh "$IMAGE" -c '
set -e
echo "Rust binary:"
command -v scout-market-replay
test -x /usr/local/bin/scout-market-replay
ls -lh /usr/local/bin/scout-market-replay

echo "Python app:"
test -f /srv/app/main.py

echo "Web output:"
test -d /srv/web-out

echo "IMAGE CONTENT: PASS"
'

echo
echo "===== ALPACA AUTH PREFLIGHT ====="
docker run --rm \
    --env-file "$STAGE/.env" \
    --entrypoint python \
    "$IMAGE" \
    -c '
import os
import requests

key = os.getenv("ALPACA_API_KEY")
secret = os.getenv("ALPACA_API_SECRET")
base = os.getenv("ALPACA_TRADING_BASE", "https://paper-api.alpaca.markets").rstrip("/")

if not key or not secret:
    raise SystemExit("FAIL: Alpaca credentials missing")

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
    raise SystemExit("FAIL: Alpaca auth preflight did not return HTTP 200")

payload = r.json()
print("Alpaca authentication: PASS")
print("Active assets returned:", len(payload))
'

echo
echo "===== RUST PROCESS SMOKE ====="
docker run --rm "$IMAGE" scout-market-replay --help >/tmp/scout-rust-help.txt 2>&1 || true
head -20 /tmp/scout-rust-help.txt || true

echo
echo "===== LIVE PRODUCTION UNTOUCHED CHECK ====="
docker compose -f "$LIVE/compose.yaml" ps
curl -fsS http://127.0.0.1:18081/healthz
echo

echo
echo "============================================================"
echo "PREFLIGHT PASS"
echo "Candidate image: $IMAGE"
echo "Candidate stage: $STAGE"
echo "LIVE PRODUCTION REMAINS UNCHANGED"
echo "============================================================"
'@

$RemoteScript |
    ssh "${VpsUser}@${VpsHost}" "bash -s 2>&1" |
    Tee-Object -FilePath $Evidence

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "PREFLIGHT FAILED." -ForegroundColor Red
    Write-Host "Production was not intentionally stopped/replaced." -ForegroundColor Yellow
    Write-Host "Upload this file:" -ForegroundColor Yellow
    Write-Host $Evidence
    exit 1
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "TAR.GZ PREFLIGHT COMPLETE" -ForegroundColor Green
Write-Host "NO PRODUCTION CUTOVER HAS OCCURRED" -ForegroundColor Yellow
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Upload this file:"
Write-Host $Evidence
