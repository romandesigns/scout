$ErrorActionPreference = "Stop"

$VpsUser  = "wavystack"
$VpsHost  = "srv1170872"
$OutFile  = "D:\wavystack\scout-v6.2.0-repo\scout-v6.5.1-isolated-runtime-smoke.txt"

$RemoteScript = @'
set -Eeuo pipefail
trap 'rc=$?; echo; echo "SMOKE ERROR at line $LINENO (exit $rc)"; docker logs --tail=200 scout-v651-smoke 2>&1 || true; docker rm -f scout-v651-smoke >/dev/null 2>&1 || true; exit $rc' ERR

IMAGE="scout-v651-candidate:latest"
STAGE="/home/wavystack/scout-v6.5.1-stage"
SMOKE="/home/wavystack/scout-v6.5.1-smoke"
CONTAINER="scout-v651-smoke"
PORT="18082"
LIVE="/opt/apps/scout"

echo "============================================================"
echo "SCOUT v6.5.1 ISOLATED RUNTIME SMOKE"
echo "UTC: $(date -u)"
echo "NO PRODUCTION CUTOVER"
echo "============================================================"

echo
echo "===== PRECHECK: PRODUCTION ====="
docker compose -f "$LIVE/compose.yaml" ps
curl -fsS http://127.0.0.1:18081/healthz
echo

echo
echo "===== PRECHECK: CANDIDATE IMAGE ====="
docker image inspect "$IMAGE" >/dev/null
echo "FOUND IMAGE: $IMAGE"
test -f "$STAGE/.env"
echo "FOUND STAGED ENV"

echo
echo "===== PREPARE ISOLATED STORAGE ====="
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
rm -rf "$SMOKE"
mkdir -p "$SMOKE/data" "$SMOKE/charts"

echo
echo "===== START ISOLATED v6.5.1 ====="
docker run -d \
  --name "$CONTAINER" \
  --restart no \
  --env-file "$STAGE/.env" \
  -e APP_VERSION=6.5.1 \
  -e DATA_DIR=/data \
  -e CHART_DIR=/charts \
  -e EMAIL_EVERY_FINDING=false \
  -e NTFY_SERVER=http://127.0.0.1:9 \
  -e NTFY_TOPIC=scout-v651-smoke-disabled \
  -p "127.0.0.1:${PORT}:8080" \
  -v "$SMOKE/data:/data" \
  -v "$SMOKE/charts:/charts" \
  "$IMAGE"

echo
echo "===== WAIT FOR HEALTH ====="
READY=0
for i in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:${PORT}/healthz" >/tmp/scout-v651-health.json 2>/dev/null; then
    READY=1
    echo "Healthy on attempt $i"
    break
  fi
  STATUS="$(docker inspect -f '{{.State.Status}}' "$CONTAINER" 2>/dev/null || true)"
  echo "Attempt $i: container=$STATUS"
  sleep 3
done

if [ "$READY" -ne 1 ]; then
  echo "FAIL: candidate did not become healthy"
  docker logs --tail=250 "$CONTAINER" 2>&1 || true
  exit 41
fi

echo
echo "===== CANDIDATE /healthz ====="
cat /tmp/scout-v651-health.json
echo

echo
echo "===== CANDIDATE VERSION ASSERTION ====="
python3 - <<'PY'
import json
with open("/tmp/scout-v651-health.json", "r", encoding="utf-8") as f:
    health = json.load(f)
version = str(health.get("version", ""))
print("Reported version:", version)
if version != "6.5.1":
    raise SystemExit(f"FAIL: expected 6.5.1, got {version!r}")
PY

echo
echo "===== RUST BINARY IN RUNNING CONTAINER ====="
docker exec "$CONTAINER" sh -c '
set -e
command -v scout-market-replay
test -x /usr/local/bin/scout-market-replay
ls -lh /usr/local/bin/scout-market-replay
'

echo
echo "===== INITIAL /api/status ====="
curl -fsS "http://127.0.0.1:${PORT}/api/status" >/tmp/scout-v651-status-initial.json
cat /tmp/scout-v651-status-initial.json
echo

echo
echo "===== OBSERVATION WINDOW: 45 SECONDS ====="
sleep 45

echo
echo "===== FINAL /api/status ====="
curl -fsS "http://127.0.0.1:${PORT}/api/status" >/tmp/scout-v651-status-final.json
cat /tmp/scout-v651-status-final.json
echo

echo
echo "===== IMPORTANT CANDIDATE LOGS ====="
docker logs --tail=500 "$CONTAINER" 2>&1 | \
  grep -Ei 'rust|hybrid|bridge|queue|drop|restart|alpaca|unauthorized|401|sip|boats|news|connected|error|warning|exception|traceback' || true

echo
echo "===== LAST 120 CANDIDATE LOG LINES ====="
docker logs --tail=120 "$CONTAINER" 2>&1 || true

echo
echo "===== PRODUCTION AFTER CANDIDATE TEST ====="
docker compose -f "$LIVE/compose.yaml" ps
curl -fsS http://127.0.0.1:18081/healthz
echo

echo
echo "===== CLEANUP ISOLATED CONTAINER ====="
docker rm -f "$CONTAINER" >/dev/null
echo "Removed: $CONTAINER"
echo "Isolated smoke data retained at: $SMOKE"

echo
echo "============================================================"
echo "ISOLATED RUNTIME SMOKE COMPLETE"
echo "PRODUCTION WAS NOT REPLACED"
echo "============================================================"
'@

$RemoteScript |
    ssh "${VpsUser}@${VpsHost}" "bash -s 2>&1" |
    Tee-Object -FilePath $OutFile

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "Smoke test failed. Production was not intentionally replaced." -ForegroundColor Red
    Write-Host "Upload this file:" -ForegroundColor Yellow
    Write-Host $OutFile
    exit 1
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "ISOLATED v6.5.1 RUNTIME SMOKE FINISHED" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Upload this file:"
Write-Host $OutFile
