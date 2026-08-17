$ErrorActionPreference = "Stop"

$VpsUser = "wavystack"
$VpsHost = "srv1170872"
$OutFile = "D:\wavystack\scout-v6.2.0-repo\scout-v6.5.1-sustained-runtime-gate.txt"

$RemoteScript = @'
set -Eeuo pipefail

IMAGE="scout-v651-candidate:latest"
STAGE="/home/wavystack/scout-v6.5.1-stage"
TESTROOT="/home/wavystack/scout-v6.5.1-sustained"
CONTAINER="scout-v651-sustained"
PORT="18083"
LIVE="/opt/apps/scout"
DURATION_SECONDS=600
SAMPLE_SECONDS=30

cleanup() {
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
}
trap 'rc=$?; echo; echo "GATE ERROR at line $LINENO (exit $rc)"; docker logs --tail=250 "$CONTAINER" 2>&1 || true; cleanup; exit $rc' ERR

echo "============================================================"
echo "SCOUT v6.5.1 SUSTAINED ISOLATED RUNTIME GATE"
echo "UTC: $(date -u)"
echo "Duration: ${DURATION_SECONDS}s"
echo "Sample interval: ${SAMPLE_SECONDS}s"
echo "NO PRODUCTION CUTOVER"
echo "============================================================"

echo
echo "===== PRECHECK: LIVE PRODUCTION ====="
docker compose -f "$LIVE/compose.yaml" ps
curl -fsS http://127.0.0.1:18081/healthz
echo

echo
echo "===== PRECHECK: CANDIDATE ====="
docker image inspect "$IMAGE" >/dev/null
test -f "$STAGE/.env"
echo "Candidate image and env present."

echo
echo "===== DISCOVER PRODUCTION NETWORK ====="
PROD_NET="$(docker inspect stockhunter-scout --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}' | head -1)"
if [ -z "$PROD_NET" ]; then
  echo "FAIL: could not determine production Docker network"
  exit 20
fi
echo "Production network: $PROD_NET"

echo
echo "===== PREPARE ISOLATED STORAGE ====="
cleanup
rm -rf "$TESTROOT"
mkdir -p "$TESTROOT/data" "$TESTROOT/charts"

echo
echo "===== CREATE UNIQUE NTFY SMOKE TOPIC ====="
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
SMOKE_TOPIC="scout-v651-gate-${STAMP}"
echo "Smoke topic: $SMOKE_TOPIC"
echo "This avoids duplicate alerts on the production topic."

echo
echo "===== START CANDIDATE ====="
docker run -d \
  --name "$CONTAINER" \
  --restart no \
  --network "$PROD_NET" \
  --env-file "$STAGE/.env" \
  -e APP_VERSION=6.5.1 \
  -e DATA_DIR=/data \
  -e CHART_DIR=/charts \
  -e EMAIL_EVERY_FINDING=false \
  -e NTFY_TOPIC="$SMOKE_TOPIC" \
  -p "127.0.0.1:${PORT}:8080" \
  -v "$TESTROOT/data:/data" \
  -v "$TESTROOT/charts:/charts" \
  "$IMAGE"

echo
echo "===== WAIT FOR INITIAL HEALTH ====="
READY=0
for i in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:${PORT}/healthz" >/tmp/scout-v651-gate-health.json 2>/dev/null; then
    READY=1
    echo "Healthy on attempt $i"
    break
  fi
  echo "Attempt $i: $(docker inspect -f '{{.State.Status}}' "$CONTAINER" 2>/dev/null || true)"
  sleep 3
done
test "$READY" -eq 1

echo
echo "===== VERSION / HYBRID ASSERTION ====="
python3 - <<'PY'
import json
with open("/tmp/scout-v651-gate-health.json", encoding="utf-8") as f:
    h=json.load(f)
print("version:", h.get("version"))
print("hybrid_ready:", h.get("hybrid_ready"))
hy=h.get("hybrid") or {}
print("rust_running:", hy.get("running"))
print("rust_binary:", hy.get("binary"))
if str(h.get("version")) != "6.5.1":
    raise SystemExit("FAIL: wrong version")
if h.get("hybrid_ready") is not True:
    raise SystemExit("FAIL: hybrid_ready != true")
if hy.get("running") is not True:
    raise SystemExit("FAIL: Rust bridge not running")
PY

echo
echo "===== REAL NTFY TRANSPORT TEST (UNIQUE TOPIC) ====="
docker exec "$CONTAINER" python - <<'PY'
import os, time, requests
server=os.getenv("NTFY_SERVER","").rstrip("/")
topic=os.getenv("NTFY_TOPIC","")
if not server or not topic:
    raise SystemExit("FAIL: NTFY_SERVER/NTFY_TOPIC missing")
url=f"{server}/{topic}"
payload={
    "topic": topic,
    "title": "Scout v6.5.1 pre-cutover transport test",
    "message": "Isolated candidate transport gate",
    "priority": 3,
    "tags": ["test_tube"],
}
t0=time.perf_counter()
r=requests.post(server, json=payload, timeout=15)
dt=time.perf_counter()-t0
print("NTFY server:", server)
print("NTFY topic:", topic)
print("NTFY HTTP status:", r.status_code)
print("NTFY round_trip_seconds:", round(dt,4))
if r.status_code < 200 or r.status_code >= 300:
    raise SystemExit(f"FAIL: ntfy HTTP {r.status_code}")
PY

echo
echo "===== WAIT FOR FEEDS / UNIVERSE ====="
FEEDS_READY=0
for i in $(seq 1 30); do
  curl -fsS "http://127.0.0.1:${PORT}/api/status" >/tmp/scout-v651-gate-status.json
  if python3 - <<'PY'
import json
s=json.load(open("/tmp/scout-v651-gate-status.json"))
feeds=s.get("feeds") or {}
u=int(s.get("universe") or 0)
sip=int(s.get("sip_subscribed") or 0)
boats=int(s.get("overnight_subscribed") or 0)
ok=bool(feeds.get("sip")) and bool(feeds.get("boats")) and u>0 and sip==u and boats==u
raise SystemExit(0 if ok else 1)
PY
  then
    FEEDS_READY=1
    echo "Feeds/universe ready on attempt $i"
    break
  fi
  sleep 3
done
test "$FEEDS_READY" -eq 1

echo
echo "===== SUSTAINED OBSERVATION ====="
echo "sample,utc,cpu,mem,queue,submitted,dropped,candidates,restarts,rust_error,sip,boats,universe,sip_sub,boats_sub"

SAMPLES=$((DURATION_SECONDS / SAMPLE_SECONDS))
for i in $(seq 1 "$SAMPLES"); do
  curl -fsS "http://127.0.0.1:${PORT}/api/status" >/tmp/scout-v651-sample.json

  STATS="$(docker stats --no-stream --format '{{.CPUPerc}}|{{.MemUsage}}' "$CONTAINER")"

  python3 - "$i" "$STATS" <<'PY'
import json,sys,datetime
i=sys.argv[1]
stats=sys.argv[2]
s=json.load(open("/tmp/scout-v651-sample.json"))
rb=((s.get("hybrid") or {}).get("rust_bridge") or {})
feeds=s.get("feeds") or {}
print(",".join(map(str,[
    i,
    datetime.datetime.now(datetime.timezone.utc).isoformat(),
    stats.replace(",",";"),
    rb.get("queue_depth"),
    rb.get("submitted"),
    rb.get("dropped"),
    rb.get("candidates"),
    rb.get("restarts"),
    repr(rb.get("last_error")),
    feeds.get("sip"),
    feeds.get("boats"),
    s.get("universe"),
    s.get("sip_subscribed"),
    s.get("overnight_subscribed"),
])))
PY

  sleep "$SAMPLE_SECONDS"
done

echo
echo "===== PRE-RESTART STATUS ====="
curl -fsS "http://127.0.0.1:${PORT}/api/status" >/tmp/scout-v651-prerestart.json
cat /tmp/scout-v651-prerestart.json
echo

echo
echo "===== CONTROLLED CANDIDATE RESTART ====="
docker restart "$CONTAINER" >/dev/null

RECOVERED=0
for i in $(seq 1 40); do
  if curl -fsS "http://127.0.0.1:${PORT}/api/status" >/tmp/scout-v651-postrestart.json 2>/dev/null; then
    if python3 - <<'PY'
import json
s=json.load(open("/tmp/scout-v651-postrestart.json"))
feeds=s.get("feeds") or {}
rb=((s.get("hybrid") or {}).get("rust_bridge") or {})
u=int(s.get("universe") or 0)
ok=(str(s.get("version"))=="6.5.1"
    and bool(feeds.get("sip"))
    and bool(feeds.get("boats"))
    and u>0
    and int(s.get("sip_subscribed") or 0)==u
    and int(s.get("overnight_subscribed") or 0)==u
    and rb.get("running") is True
    and int(rb.get("dropped") or 0)==0
    and rb.get("last_error") is None)
raise SystemExit(0 if ok else 1)
PY
    then
      RECOVERED=1
      echo "Recovered on attempt $i"
      break
    fi
  fi
  sleep 3
done
test "$RECOVERED" -eq 1

echo
echo "===== POST-RESTART STATUS ====="
cat /tmp/scout-v651-postrestart.json
echo

echo
echo "===== FINAL ASSERTIONS ====="
python3 - <<'PY'
import json
s=json.load(open("/tmp/scout-v651-postrestart.json"))
rb=((s.get("hybrid") or {}).get("rust_bridge") or {})
feeds=s.get("feeds") or {}
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
}
for k,v in checks.items():
    print(f"{k}: {'PASS' if v else 'FAIL'}")
if not all(checks.values()):
    raise SystemExit("FAIL: one or more final assertions failed")
PY

echo
echo "===== IMPORTANT LOGS ====="
docker logs --tail=800 "$CONTAINER" 2>&1 | \
grep -Ei 'rust|hybrid|bridge|queue|drop|restart|alpaca|unauthorized|401|sip|boats|news|connected|disconnect|error|warning|exception|traceback' || true

echo
echo "===== VERIFY PRODUCTION STILL UNTOUCHED ====="
docker compose -f "$LIVE/compose.yaml" ps
curl -fsS http://127.0.0.1:18081/healthz
echo

echo
echo "===== CLEANUP ====="
cleanup
echo "Candidate removed."
echo "Test data retained: $TESTROOT"

echo
echo "============================================================"
echo "SUSTAINED ISOLATED GATE PASS"
echo "PRODUCTION WAS NOT REPLACED"
echo "============================================================"
'@

$RemoteScript |
    ssh "${VpsUser}@${VpsHost}" "bash -s 2>&1" |
    Tee-Object -FilePath $OutFile

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "Sustained gate failed. Production was not intentionally replaced." -ForegroundColor Red
    Write-Host "Upload this file:" -ForegroundColor Yellow
    Write-Host $OutFile
    exit 1
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "SUSTAINED v6.5.1 GATE COMPLETE" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Upload this file:"
Write-Host $OutFile
