$ErrorActionPreference = "Stop"

$VpsUser = "wavystack"
$VpsHost = "srv1170872"
$OutFile = "D:\wavystack\scout-v6.2.0-repo\scout-v6.5.1-production-cutover.txt"

$RemoteScript = @'
set -Eeuo pipefail

LIVE="/opt/apps/scout"
STAGE="/home/wavystack/scout-v6.5.1-stage"
CANDIDATE="scout-v651-candidate:latest"
SERVICE_CONTAINER="stockhunter-scout"
EXPECTED_VERSION="6.5.1"
EXPECTED_IMAGE_TAG="scout-scout:latest"
BACKUP_ROOT="/home/wavystack/scout-cutover-backups"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP="$BACKUP_ROOT/$STAMP"
ROLLBACK_TAG="scout-v630-rollback:$STAMP"

mkdir -p "$BACKUP"

rollback() {
  rc=$?
  echo
  echo "============================================================"
  echo "CUTOVER FAILURE - AUTOMATIC ROLLBACK STARTING"
  echo "Failure exit code: $rc"
  echo "============================================================"

  set +e

  echo
  echo "===== ROLLBACK: RESTORE ENV ====="
  if [ -f "$BACKUP/live.env" ]; then
    cp "$BACKUP/live.env" "$LIVE/.env"
    chmod 600 "$LIVE/.env"
    echo "Restored previous live .env"
  else
    echo "WARNING: previous live .env backup missing"
  fi

  echo
  echo "===== ROLLBACK: RESTORE PREVIOUS IMAGE TAG ====="
  if docker image inspect "$ROLLBACK_TAG" >/dev/null 2>&1; then
    docker tag "$ROLLBACK_TAG" "$EXPECTED_IMAGE_TAG"
    echo "Retagged rollback image to $EXPECTED_IMAGE_TAG"
  else
    echo "WARNING: rollback image tag missing: $ROLLBACK_TAG"
  fi

  echo
  echo "===== ROLLBACK: RECREATE PREVIOUS PRODUCTION ====="
  cd "$LIVE"
  docker compose up -d --no-build --force-recreate

  echo
  echo "===== ROLLBACK: WAIT FOR HEALTH ====="
  for i in $(seq 1 40); do
    if curl -fsS http://127.0.0.1:18081/healthz >/tmp/scout-rollback-health.json 2>/dev/null; then
      echo "Rollback healthy on attempt $i"
      cat /tmp/scout-rollback-health.json
      echo
      break
    fi
    sleep 3
  done

  echo
  echo "===== ROLLBACK: FINAL STATE ====="
  docker compose -f "$LIVE/compose.yaml" ps
  curl -fsS http://127.0.0.1:18081/healthz || true
  echo

  echo
  echo "AUTOMATIC ROLLBACK FINISHED"
  echo "Backup directory: $BACKUP"
  exit "$rc"
}

trap rollback ERR

echo "============================================================"
echo "SCOUT v6.5.1 CONTROLLED PRODUCTION CUTOVER"
echo "UTC: $(date -u)"
echo "Automatic rollback: ENABLED"
echo "============================================================"

echo
echo "===== 1. VERIFY CURRENT PRODUCTION ====="
test -d "$LIVE"
test -f "$LIVE/compose.yaml"
test -f "$LIVE/.env"
docker inspect "$SERVICE_CONTAINER" >/dev/null

curl -fsS http://127.0.0.1:18081/healthz >/tmp/scout-precutover-health.json
cat /tmp/scout-precutover-health.json
echo

CURRENT_VERSION="$(python3 - <<'PY'
import json
h=json.load(open("/tmp/scout-precutover-health.json"))
print(h.get("version",""))
PY
)"
echo "Current production version: $CURRENT_VERSION"

if [ "$CURRENT_VERSION" != "6.3.0" ]; then
  echo "FAIL: expected current production 6.3.0, got $CURRENT_VERSION"
  exit 21
fi

echo
echo "===== 2. VERIFY TESTED CANDIDATE ====="
docker image inspect "$CANDIDATE" >/dev/null
test -f "$STAGE/.env"
test -f "$STAGE/VERSION"

STAGED_VERSION="$(tr -d '\r\n' < "$STAGE/VERSION")"
echo "Staged version: $STAGED_VERSION"
test "$STAGED_VERSION" = "$EXPECTED_VERSION"

docker run --rm --entrypoint sh "$CANDIDATE" -c '
set -e
test -x /usr/local/bin/scout-market-replay
test -f /srv/app/main.py
test -d /srv/web-out
echo "Candidate image content verified."
'

echo
echo "===== 3. CAPTURE ROLLBACK STATE ====="

CURRENT_IMAGE_ID="$(docker inspect -f '{{.Image}}' "$SERVICE_CONTAINER")"
echo "Current production image ID: $CURRENT_IMAGE_ID"

docker image inspect "$CURRENT_IMAGE_ID" >/dev/null
docker tag "$CURRENT_IMAGE_ID" "$ROLLBACK_TAG"
echo "Rollback image tag: $ROLLBACK_TAG"

cp "$LIVE/.env" "$BACKUP/live.env"
chmod 600 "$BACKUP/live.env"

cp "$LIVE/compose.yaml" "$BACKUP/compose.yaml"
cp "$LIVE/VERSION" "$BACKUP/VERSION" 2>/dev/null || true

docker inspect "$SERVICE_CONTAINER" > "$BACKUP/container-inspect.json"
docker compose -f "$LIVE/compose.yaml" config > "$BACKUP/compose-rendered.yaml"

echo "Backup directory: $BACKUP"

echo
echo "===== 4. PREPARE v6.5.1 LIVE ENV ====="

cp "$STAGE/.env" "$LIVE/.env"
chmod 600 "$LIVE/.env"

if grep -q '^APP_VERSION=' "$LIVE/.env"; then
  sed -i 's/^APP_VERSION=.*/APP_VERSION=6.5.1/' "$LIVE/.env"
else
  printf '\nAPP_VERSION=6.5.1\n' >> "$LIVE/.env"
fi

echo "Installed tested v6.5.1 environment."
echo "Secret values not displayed."

echo
echo "===== 5. PROMOTE TESTED CANDIDATE IMAGE ====="

docker tag "$CANDIDATE" "$EXPECTED_IMAGE_TAG"

PROMOTED_ID="$(docker image inspect "$EXPECTED_IMAGE_TAG" --format '{{.Id}}')"
CANDIDATE_ID="$(docker image inspect "$CANDIDATE" --format '{{.Id}}')"

echo "Candidate image ID: $CANDIDATE_ID"
echo "Promoted image ID:  $PROMOTED_ID"

test "$PROMOTED_ID" = "$CANDIDATE_ID"

echo
echo "===== 6. CONTROLLED CUTOVER ====="

cd "$LIVE"

docker compose up -d --no-build --force-recreate

echo
echo "===== 7. WAIT FOR v6.5.1 HEALTH ====="

READY=0
for i in $(seq 1 40); do
  if curl -fsS http://127.0.0.1:18081/healthz >/tmp/scout-v651-prod-health.json 2>/dev/null; then
    if python3 - <<'PY'
import json
h=json.load(open("/tmp/scout-v651-prod-health.json"))
ok=(str(h.get("version"))=="6.5.1"
    and h.get("hybrid_ready") is True
    and (h.get("hybrid") or {}).get("running") is True)
raise SystemExit(0 if ok else 1)
PY
    then
      READY=1
      echo "v6.5.1 healthy on attempt $i"
      break
    fi
  fi
  echo "Attempt $i: waiting for v6.5.1 health/hybrid readiness"
  sleep 3
done

test "$READY" -eq 1

echo
echo "===== 8. WAIT FOR FULL FEED RECOVERY ====="

FEEDS_READY=0
for i in $(seq 1 40); do
  curl -fsS http://127.0.0.1:18081/api/status >/tmp/scout-v651-prod-status.json

  if python3 - <<'PY'
import json
s=json.load(open("/tmp/scout-v651-prod-status.json"))
feeds=s.get("feeds") or {}
rb=((s.get("hybrid") or {}).get("rust_bridge") or {})
u=int(s.get("universe") or 0)
sip=int(s.get("sip_subscribed") or 0)
boats=int(s.get("overnight_subscribed") or 0)

ok=(
    str(s.get("version"))=="6.5.1"
    and bool(feeds.get("sip"))
    and bool(feeds.get("boats"))
    and bool(feeds.get("news"))
    and u>0
    and sip==u
    and boats==u
    and rb.get("running") is True
    and int(rb.get("dropped") or 0)==0
    and int(rb.get("restarts") or 0)==0
    and rb.get("last_error") is None
)
raise SystemExit(0 if ok else 1)
PY
  then
    FEEDS_READY=1
    echo "Full feed/hybrid recovery on attempt $i"
    break
  fi

  sleep 3
done

test "$FEEDS_READY" -eq 1

echo
echo "===== 9. PRODUCTION OBSERVATION WINDOW ====="
sleep 60

curl -fsS http://127.0.0.1:18081/api/status >/tmp/scout-v651-prod-final.json

echo
echo "===== 10. FINAL PRODUCTION ASSERTIONS ====="

python3 - <<'PY'
import json

s=json.load(open("/tmp/scout-v651-prod-final.json"))
feeds=s.get("feeds") or {}
rb=((s.get("hybrid") or {}).get("rust_bridge") or {})
u=int(s.get("universe") or 0)

checks={
    "version_6_5_1": str(s.get("version"))=="6.5.1",
    "sip_connected": bool(feeds.get("sip")),
    "boats_connected": bool(feeds.get("boats")),
    "news_connected": bool(feeds.get("news")),
    "universe_positive": u>0,
    "sip_full_subscription": int(s.get("sip_subscribed") or 0)==u,
    "boats_full_subscription": int(s.get("overnight_subscribed") or 0)==u,
    "rust_running": rb.get("running") is True,
    "rust_zero_drops": int(rb.get("dropped") or 0)==0,
    "rust_zero_restarts": int(rb.get("restarts") or 0)==0,
    "rust_no_error": rb.get("last_error") is None,
    "rust_receiving_events": int(rb.get("submitted") or 0)>0,
}

for k,v in checks.items():
    print(f"{k}: {'PASS' if v else 'FAIL'}")

print("universe:", u)
print("rust_submitted:", rb.get("submitted"))
print("rust_candidates:", rb.get("candidates"))
print("rust_queue_depth:", rb.get("queue_depth"))

if not all(checks.values()):
    raise SystemExit("FAIL: mandatory production assertion failed")
PY

echo
echo "===== 11. FINAL /healthz ====="
curl -fsS http://127.0.0.1:18081/healthz
echo

echo
echo "===== 12. FINAL CONTAINER STATE ====="
docker compose -f "$LIVE/compose.yaml" ps

echo
echo "===== 13. IMPORTANT STARTUP LOGS ====="
docker logs --tail=300 "$SERVICE_CONTAINER" 2>&1 | \
grep -Ei 'rust|hybrid|bridge|alpaca|sip|boats|news|connected|disconnect|drop|restart|unauthorized|401|error|exception|traceback|warning' || true

echo
echo "============================================================"
echo "PRODUCTION CUTOVER PASS"
echo "SCOUT v6.5.1 IS LIVE"
echo "Rollback image retained: $ROLLBACK_TAG"
echo "Rollback backup retained: $BACKUP"
echo "============================================================"

trap - ERR
'@

$RemoteScript |
    ssh "${VpsUser}@${VpsHost}" "bash -s 2>&1" |
    Tee-Object -FilePath $OutFile

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "CUTOVER DID NOT PASS. The VPS script attempted automatic rollback." -ForegroundColor Red
    Write-Host "Upload this file immediately:" -ForegroundColor Yellow
    Write-Host $OutFile
    exit 1
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "SCOUT v6.5.1 PRODUCTION CUTOVER COMPLETE" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Upload this file for final verification:"
Write-Host $OutFile
