$ErrorActionPreference = "Stop"

$VpsUser = "wavystack"
$VpsHost = "srv1170872"
$OutFile = "D:\wavystack\scout-v6.2.0-repo\scout-v6.5.1-production-functional-validation.txt"

$RemoteScript = @'
set -Eeuo pipefail

LIVE="/opt/apps/scout"
CONTAINER="stockhunter-scout"
BASE="http://127.0.0.1:18081"
DURATION_SECONDS=900
SAMPLE_SECONDS=60

echo "============================================================"
echo "SCOUT v6.5.1 PRODUCTION FUNCTIONAL VALIDATION"
echo "UTC: $(date -u)"
echo "Duration: ${DURATION_SECONDS}s"
echo "Sample interval: ${SAMPLE_SECONDS}s"
echo "READ-ONLY VALIDATION: NO RESTART / NO CUTOVER / NO CONFIG CHANGE"
echo "============================================================"

echo
echo "===== 1. BASELINE HEALTH ====="
curl -fsS "$BASE/healthz" >/tmp/scout-functional-health.json
cat /tmp/scout-functional-health.json
echo

python3 - <<'PY'
import json
h=json.load(open("/tmp/scout-functional-health.json"))
hy=h.get("hybrid") or {}
checks={
    "version_6_5_1": str(h.get("version"))=="6.5.1",
    "hybrid_ready": h.get("hybrid_ready") is True,
    "rust_running": hy.get("running") is True,
    "rust_zero_drops": int(hy.get("dropped") or 0)==0,
    "rust_zero_restarts": int(hy.get("restarts") or 0)==0,
    "rust_no_error": hy.get("last_error") is None,
}
for k,v in checks.items():
    print(f"{k}: {'PASS' if v else 'FAIL'}")
if not all(checks.values()):
    raise SystemExit("Baseline health assertion failed")
PY

echo
echo "===== 2. BASELINE STATUS ====="
curl -fsS "$BASE/api/status" >/tmp/scout-functional-baseline.json

python3 - <<'PY'
import json
s=json.load(open("/tmp/scout-functional-baseline.json"))
feeds=s.get("feeds") or {}
rb=((s.get("hybrid") or {}).get("rust_bridge") or {})
prec=((s.get("hybrid") or {}).get("precision") or {})
notif=s.get("notifications") or {}
queues=notif.get("queues") or {}
print("version:", s.get("version"))
print("architecture:", (s.get("hybrid") or {}).get("architecture"))
print("sip:", feeds.get("sip"))
print("boats:", feeds.get("boats"))
print("news:", feeds.get("news"))
print("universe:", s.get("universe"))
print("sip_subscribed:", s.get("sip_subscribed"))
print("overnight_subscribed:", s.get("overnight_subscribed"))
print("rust_submitted:", rb.get("submitted"))
print("rust_candidates:", rb.get("candidates"))
print("rust_dropped:", rb.get("dropped"))
print("rust_restarts:", rb.get("restarts"))
print("rust_queue_depth:", rb.get("queue_depth"))
print("precision_completed_episodes:", prec.get("completed_episodes"))
print("precision_successful_episodes:", prec.get("successful_episodes"))
print("precision:", prec.get("precision"))
print("source_mix:", prec.get("source_mix"))
print("notification_queues:", queues)
print("latest_findings:", len(s.get("latest_findings") or []))
PY

echo
echo "===== 3. LIVE OBSERVATION ====="
echo "sample,utc,cpu_mem,universe,sip,boats,news,rust_submitted,rust_candidates,rust_dropped,rust_restarts,rust_queue,rust_error,findings,ntfy_queue"

SAMPLES=$((DURATION_SECONDS / SAMPLE_SECONDS))

for i in $(seq 1 "$SAMPLES"); do
    curl -fsS "$BASE/api/status" >/tmp/scout-functional-sample.json
    STATS="$(docker stats --no-stream --format '{{.CPUPerc}}|{{.MemUsage}}' "$CONTAINER")"

    python3 - "$i" "$STATS" <<'PY'
import json,sys,datetime
i=sys.argv[1]
stats=sys.argv[2].replace(",",";")
s=json.load(open("/tmp/scout-functional-sample.json"))
feeds=s.get("feeds") or {}
rb=((s.get("hybrid") or {}).get("rust_bridge") or {})
q=((s.get("notifications") or {}).get("queues") or {})
print(",".join(map(str,[
    i,
    datetime.datetime.now(datetime.timezone.utc).isoformat(),
    stats,
    s.get("universe"),
    feeds.get("sip"),
    feeds.get("boats"),
    feeds.get("news"),
    rb.get("submitted"),
    rb.get("candidates"),
    rb.get("dropped"),
    rb.get("restarts"),
    rb.get("queue_depth"),
    repr(rb.get("last_error")),
    len(s.get("latest_findings") or []),
    q.get("ntfy"),
])))
PY

    sleep "$SAMPLE_SECONDS"
done

echo
echo "===== 4. FINAL STATUS ====="
curl -fsS "$BASE/api/status" >/tmp/scout-functional-final.json

python3 - <<'PY'
import json, collections

s=json.load(open("/tmp/scout-functional-final.json"))
feeds=s.get("feeds") or {}
hy=s.get("hybrid") or {}
rb=hy.get("rust_bridge") or {}
prec=hy.get("precision") or {}
findings=s.get("latest_findings") or []

print("version:", s.get("version"))
print("architecture:", hy.get("architecture"))
print("universe:", s.get("universe"))
print("sip_subscribed:", s.get("sip_subscribed"))
print("overnight_subscribed:", s.get("overnight_subscribed"))
print("rust_submitted:", rb.get("submitted"))
print("rust_candidates:", rb.get("candidates"))
print("rust_dropped:", rb.get("dropped"))
print("rust_restarts:", rb.get("restarts"))
print("rust_queue_depth:", rb.get("queue_depth"))
print("rust_last_error:", rb.get("last_error"))
print("precision_completed_episodes:", prec.get("completed_episodes"))
print("precision_successful_episodes:", prec.get("successful_episodes"))
print("precision:", prec.get("precision"))
print("source_mix:", prec.get("source_mix"))

stage=collections.Counter()
source=collections.Counter()
lifecycle=collections.Counter()
rank=collections.Counter()
timeliness=collections.Counter()
ticker=collections.Counter()

for f in findings:
    stage[str(f.get("stage"))] += 1
    source[str(f.get("engine_source"))] += 1
    lifecycle[str(f.get("lifecycle_phase"))] += 1
    rank[str(f.get("actionable_rank"))] += 1
    timeliness[str(f.get("timeliness_label"))] += 1
    ticker[str(f.get("ticker"))] += 1

print("latest_findings_count:", len(findings))
print("stage_mix:", dict(stage))
print("engine_source_mix_latest:", dict(source))
print("lifecycle_mix:", dict(lifecycle))
print("actionable_rank_mix:", dict(rank))
print("timeliness_mix:", dict(timeliness))
print("duplicate_tickers_latest:", {k:v for k,v in ticker.items() if v>1})

checks={
    "version_6_5_1": str(s.get("version"))=="6.5.1",
    "sip_connected": bool(feeds.get("sip")),
    "boats_connected": bool(feeds.get("boats")),
    "news_connected": bool(feeds.get("news")),
    "universe_positive": int(s.get("universe") or 0)>0,
    "sip_full_subscription": int(s.get("sip_subscribed") or 0)==int(s.get("universe") or 0),
    "boats_full_subscription": int(s.get("overnight_subscribed") or 0)==int(s.get("universe") or 0),
    "rust_running": rb.get("running") is True,
    "rust_zero_drops": int(rb.get("dropped") or 0)==0,
    "rust_zero_restarts": int(rb.get("restarts") or 0)==0,
    "rust_no_error": rb.get("last_error") is None,
    "rust_receiving_events": int(rb.get("submitted") or 0)>0,
}

print()
print("FINAL ASSERTIONS")
for k,v in checks.items():
    print(f"{k}: {'PASS' if v else 'FAIL'}")

if not all(checks.values()):
    raise SystemExit("Functional validation infrastructure assertion failed")
PY

echo
echo "===== 5. RECENT FINDINGS SUMMARY ====="
python3 - <<'PY'
import json
s=json.load(open("/tmp/scout-functional-final.json"))
findings=s.get("latest_findings") or []

for f in findings[:25]:
    print(
        f"{f.get('ticker')} | "
        f"{f.get('stage')} | "
        f"src={f.get('engine_source')} | "
        f"hybrid={f.get('hybrid_sources')} | "
        f"rank={f.get('actionable_rank')} | "
        f"life={f.get('lifecycle_phase')} | "
        f"time={f.get('timeliness_label')} | "
        f"score={f.get('score')} | "
        f"hybrid_score={f.get('hybrid_score')} | "
        f"reason={f.get('notification_reason')}"
    )
PY

echo
echo "===== 6. NTFY DELIVERY STATE ====="
python3 - <<'PY'
import json
s=json.load(open("/tmp/scout-functional-final.json"))
n=s.get("notifications") or {}
delivery=n.get("delivery") or {}
lat=(s.get("hybrid") or {}).get("notification_latency") or {}
print("queues:", n.get("queues"))
print("ntfy_delivery:", delivery.get("ntfy"))
print("notification_latency:", lat)
PY

echo
echo "===== 7. IMPORTANT PRODUCTION LOGS ====="
docker logs --since 20m "$CONTAINER" 2>&1 | \
grep -Ei 'PRE_IGNITION|AWAKEN|REACCEL|RETEST|RUST|HYBRID|duplicate|dedup|lifecycle|ntfy|alpaca|sip|boats|news|disconnect|drop|restart|error|exception|traceback|warning' || true

echo
echo "===== 8. FINAL HEALTH ====="
curl -fsS "$BASE/healthz"
echo

echo
echo "============================================================"
echo "PRODUCTION FUNCTIONAL VALIDATION COMPLETE"
echo "NO CONFIGURATION OR CONTAINER CHANGES WERE MADE"
echo "============================================================"
'@

$RemoteScript |
    ssh "${VpsUser}@${VpsHost}" "bash -s 2>&1" |
    Tee-Object -FilePath $OutFile

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "Functional validation reported a mandatory infrastructure failure." -ForegroundColor Red
    Write-Host "No production changes were intentionally made." -ForegroundColor Yellow
    Write-Host "Upload this file:" -ForegroundColor Yellow
    Write-Host $OutFile
    exit 1
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "PRODUCTION FUNCTIONAL VALIDATION COMPLETE" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Upload this file:"
Write-Host $OutFile
