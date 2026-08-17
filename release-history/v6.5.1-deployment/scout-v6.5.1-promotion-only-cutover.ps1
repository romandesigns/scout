$ErrorActionPreference = "Stop"

$VpsUser = "wavystack"
$VpsHost = "srv1170872"
$OutFile = "D:\wavystack\scout-v6.2.0-repo\scout-v6.5.1-promotion-only-cutover.txt"

$RemoteScript = @'
set -Eeuo pipefail

LIVE="/opt/apps/scout"
STAGE="/home/wavystack/scout-v6.5.1-stage"
CANDIDATE="scout-v651-candidate:latest"
EXPECTED_CANDIDATE_ID="sha256:64ede57f699ec1e27215f62059a26c3c4bc47160b0f3a9a8550ffc725d4e34ae"
LIVE_TAG="scout-scout:latest"
SERVICE_CONTAINER="stockhunter-scout"
EXPECTED_VERSION="6.5.1"
BACKUP_ROOT="/home/wavystack/scout-cutover-backups"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP="$BACKUP_ROOT/$STAMP"
ROLLBACK_TAG="scout-v630-rollback:$STAMP"
CUTOVER_DONE=0

mkdir -p "$BACKUP"

rollback() {
    rc="${1:-1}"
    echo
    echo "============================================================"
    echo "PROMOTION FAILURE - AUTOMATIC ROLLBACK"
    echo "exit code: $rc"
    echo "============================================================"

    set +e

    if [ -f "$BACKUP/live.env" ]; then
        cp "$BACKUP/live.env" "$LIVE/.env"
        chmod 600 "$LIVE/.env"
        echo "Restored previous .env"
    fi

    if docker image inspect "$ROLLBACK_TAG" >/dev/null 2>&1; then
        docker tag "$ROLLBACK_TAG" "$LIVE_TAG"
        echo "Restored previous image tag"
    fi

    cd "$LIVE"
    docker compose up -d --no-build --force-recreate

    for i in $(seq 1 40); do
        if curl -fsS http://127.0.0.1:18081/healthz >/tmp/scout-rollback-health.json 2>/dev/null; then
            echo "Rollback health available on attempt $i"
            cat /tmp/scout-rollback-health.json
            echo
            break
        fi
        sleep 3
    done

    docker compose -f "$LIVE/compose.yaml" ps || true
    echo "Rollback backup: $BACKUP"
    exit "$rc"
}

on_error() {
    rc=$?
    if [ "$CUTOVER_DONE" -eq 0 ]; then
        rollback "$rc"
    fi
    exit "$rc"
}

trap on_error ERR

echo "============================================================"
echo "SCOUT v6.5.1 PROMOTION-ONLY PRODUCTION CUTOVER"
echo "UTC: $(date -u)"
echo "Reuses exact already-tested candidate image"
echo "Automatic rollback: ENABLED"
echo "============================================================"

echo
echo "===== 1. VERIFY CURRENT PRODUCTION IS BACK ON v6.3.0 ====="

curl -fsS http://127.0.0.1:18081/healthz >/tmp/scout-current-health.json
cat /tmp/scout-current-health.json
echo

python3 - <<'PY'
import json
h=json.load(open("/tmp/scout-current-health.json"))
v=str(h.get("version",""))
print("Current version:", v)
if v != "6.3.0":
    raise SystemExit(f"Expected current production 6.3.0 before promotion, got {v!r}")
PY

echo
echo "===== 2. WAIT FOR CURRENT v6.3.0 FEEDS TO BE STABLE ====="

CURRENT_STABLE=0
for i in $(seq 1 40); do
    if curl -fsS http://127.0.0.1:18081/healthz >/tmp/scout-current-health.json 2>/dev/null; then
        if python3 - <<'PY'
import json
h=json.load(open("/tmp/scout-current-health.json"))
u=int(h.get("universe") or 0)
sip=int(h.get("sip_subscribed") or 0)
boats=int(h.get("overnight_subscribed") or 0)
ok=(str(h.get("version"))=="6.3.0" and u>0 and sip==u and boats==u)
raise SystemExit(0 if ok else 1)
PY
        then
            CURRENT_STABLE=1
            echo "Current production stable on attempt $i"
            break
        fi
    fi
    sleep 3
done

test "$CURRENT_STABLE" -eq 1

echo
echo "===== 3. VERIFY EXACT TESTED CANDIDATE IMAGE ====="

CANDIDATE_ID="$(docker image inspect "$CANDIDATE" --format '{{.Id}}')"
echo "Candidate image ID: $CANDIDATE_ID"
echo "Expected image ID:  $EXPECTED_CANDIDATE_ID"
test "$CANDIDATE_ID" = "$EXPECTED_CANDIDATE_ID"

test -f "$STAGE/.env"
test -f "$STAGE/VERSION"
test "$(tr -d '\r\n' < "$STAGE/VERSION")" = "$EXPECTED_VERSION"

docker run --rm --entrypoint sh "$CANDIDATE" -c '
set -e
test -x /usr/local/bin/scout-market-replay
test -f /srv/app/main.py
test -d /srv/web-out
echo "Candidate runtime content: PASS"
'

echo
echo "===== 4. CREATE FRESH ROLLBACK SNAPSHOT ====="

CURRENT_IMAGE_ID="$(docker inspect -f '{{.Image}}' "$SERVICE_CONTAINER")"
docker tag "$CURRENT_IMAGE_ID" "$ROLLBACK_TAG"
cp "$LIVE/.env" "$BACKUP/live.env"
chmod 600 "$BACKUP/live.env"
cp "$LIVE/compose.yaml" "$BACKUP/compose.yaml"
cp "$LIVE/VERSION" "$BACKUP/VERSION" 2>/dev/null || true
docker inspect "$SERVICE_CONTAINER" > "$BACKUP/container-inspect.json"

echo "Rollback image: $ROLLBACK_TAG"
echo "Rollback backup: $BACKUP"

echo
echo "===== 5. INSTALL TESTED v6.5.1 ENV ====="

cp "$STAGE/.env" "$LIVE/.env"
chmod 600 "$LIVE/.env"

if grep -q '^APP_VERSION=' "$LIVE/.env"; then
    sed -i 's/^APP_VERSION=.*/APP_VERSION=6.5.1/' "$LIVE/.env"
else
    printf '\nAPP_VERSION=6.5.1\n' >> "$LIVE/.env"
fi

echo "Environment installed; secret values not displayed."

echo
echo "===== 6. PROMOTE EXACT TESTED IMAGE ====="

docker tag "$CANDIDATE" "$LIVE_TAG"

PROMOTED_ID="$(docker image inspect "$LIVE_TAG" --format '{{.Id}}')"
echo "Promoted image ID: $PROMOTED_ID"
test "$PROMOTED_ID" = "$EXPECTED_CANDIDATE_ID"

echo
echo "===== 7. RECREATE PRODUCTION ====="

cd "$LIVE"
docker compose up -d --no-build --force-recreate

echo
echo "===== 8. WAIT FOR v6.5.1 HEALTH + HYBRID ====="

READY=0
for i in $(seq 1 40); do
    if curl -fsS http://127.0.0.1:18081/healthz >/tmp/scout-v651-health.json 2>/dev/null; then
        if python3 - <<'PY'
import json
h=json.load(open("/tmp/scout-v651-health.json"))
hy=h.get("hybrid") or {}
ok=(str(h.get("version"))=="6.5.1"
    and h.get("hybrid_ready") is True
    and hy.get("running") is True
    and int(hy.get("dropped") or 0)==0
    and int(hy.get("restarts") or 0)==0
    and hy.get("last_error") is None)
raise SystemExit(0 if ok else 1)
PY
        then
            READY=1
            echo "v6.5.1 healthy on attempt $i"
            break
        fi
    fi
    sleep 3
done

test "$READY" -eq 1

echo
echo "===== 9. WAIT FOR FULL FEED RECOVERY ====="

FEEDS_READY=0
for i in $(seq 1 40); do
    curl -fsS http://127.0.0.1:18081/api/status >/tmp/scout-v651-status.json

    if python3 - <<'PY'
import json
s=json.load(open("/tmp/scout-v651-status.json"))
feeds=s.get("feeds") or {}
rb=((s.get("hybrid") or {}).get("rust_bridge") or {})
u=int(s.get("universe") or 0)
checks=[
    str(s.get("version"))=="6.5.1",
    bool(feeds.get("sip")),
    bool(feeds.get("boats")),
    bool(feeds.get("news")),
    u>0,
    int(s.get("sip_subscribed") or 0)==u,
    int(s.get("overnight_subscribed") or 0)==u,
    rb.get("running") is True,
    int(rb.get("dropped") or 0)==0,
    int(rb.get("restarts") or 0)==0,
    rb.get("last_error") is None,
]
raise SystemExit(0 if all(checks) else 1)
PY
    then
        FEEDS_READY=1
        echo "Full feed recovery on attempt $i"
        break
    fi

    sleep 3
done

test "$FEEDS_READY" -eq 1

echo
echo "===== 10. 60-SECOND PRODUCTION OBSERVATION ====="
sleep 60

curl -fsS http://127.0.0.1:18081/api/status >/tmp/scout-v651-final.json

echo
echo "===== 11. FINAL ASSERTIONS ====="

python3 - <<'PY'
import json
s=json.load(open("/tmp/scout-v651-final.json"))
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
    raise SystemExit("Mandatory production assertion failed")
PY

echo
echo "===== 12. FINAL HEALTH ====="
curl -fsS http://127.0.0.1:18081/healthz
echo

echo
echo "===== 13. FINAL CONTAINER ====="
docker compose -f "$LIVE/compose.yaml" ps

echo
echo "===== 14. STARTUP / HYBRID LOGS ====="
docker logs --tail=250 "$SERVICE_CONTAINER" 2>&1 | \
grep -Ei 'rust|hybrid|alpaca|sip|boats|news|connected|disconnect|drop|restart|unauthorized|401|error|exception|traceback|warning' || true

CUTOVER_DONE=1

echo
echo "============================================================"
echo "PROMOTION CUTOVER PASS"
echo "SCOUT v6.5.1 IS LIVE"
echo "Exact tested image ID: $EXPECTED_CANDIDATE_ID"
echo "Rollback image retained: $ROLLBACK_TAG"
echo "Rollback backup retained: $BACKUP"
echo "============================================================"
'@

$RemoteScript |
    ssh "${VpsUser}@${VpsHost}" "bash -s 2>&1" |
    Tee-Object -FilePath $OutFile

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "PROMOTION DID NOT PASS. Automatic rollback was attempted." -ForegroundColor Red
    Write-Host "Upload this file:" -ForegroundColor Yellow
    Write-Host $OutFile
    exit 1
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "SCOUT v6.5.1 PROMOTION CUTOVER COMPLETE" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Upload this file for verification:"
Write-Host $OutFile
