from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class Check:
    name: str
    status: str
    detail: str
    data: dict[str, Any] | None = None


PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"
INFO = "INFO"
MANUAL = "MANUAL"


def fetch_json(url: str, *, method: str = "GET", payload: dict[str, Any] | None = None, timeout: float = 20.0) -> Any:
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read()
        if not raw:
            return None
        return json.loads(raw.decode("utf-8"))


def safe_float(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def pct(new: float, old: float) -> float | None:
    if not old:
        return None
    return (new / old - 1.0) * 100.0


def nearest_bucket(rows: list[dict[str, Any]], ts: float) -> int | None:
    if not rows:
        return None
    return min(range(len(rows)), key=lambda i: abs(float(rows[i].get("start_ts", 0)) - ts))


def independent_bucket_metrics(rows: list[dict[str, Any]], detected_at: float, bucket_seconds: int = 15) -> dict[str, float | None]:
    """Recompute coarse price/volume metrics from API candles only.

    These are deliberately independent of Scout's internal feature code. They are
    candle-level cross-checks, not a second SIP feed and not expected to match
    trade-level metrics to the last basis point.
    """
    ordered = sorted((r for r in rows if safe_float(r.get("start_ts")) is not None), key=lambda r: float(r["start_ts"]))
    if not ordered:
        return {}
    index = nearest_bucket(ordered, detected_at)
    if index is None:
        return {}
    current = safe_float(ordered[index].get("close"))
    if current is None:
        return {}

    def change(seconds: int) -> float | None:
        steps = max(1, round(seconds / bucket_seconds))
        prior_index = index - steps
        if prior_index < 0:
            return None
        prior = safe_float(ordered[prior_index].get("close"))
        return pct(current, prior) if prior is not None else None

    def sum_field(seconds: int, key: str) -> float | None:
        steps = max(1, round(seconds / bucket_seconds))
        start = max(0, index - steps + 1)
        values = [safe_float(r.get(key)) for r in ordered[start:index + 1]]
        valid = [v for v in values if v is not None]
        return sum(valid) if valid else None

    return {
        "close": current,
        "change_15s_pct": change(15),
        "change_30s_pct": change(30),
        "change_60s_pct": change(60),
        "volume_15s": sum_field(15, "volume"),
        "volume_30s": sum_field(30, "volume"),
        "trades_15s": sum_field(15, "trades"),
        "trades_30s": sum_field(30, "trades"),
    }


def compare_metric(label: str, scout: Any, independent: Any, *, abs_tolerance: float, relative_tolerance: float = 0.0) -> dict[str, Any]:
    s = safe_float(scout)
    i = safe_float(independent)
    if s is None or i is None:
        return {"metric": label, "status": "NA", "scout": s, "independent": i}
    delta = abs(s - i)
    allowed = max(abs_tolerance, abs(s) * relative_tolerance)
    return {
        "metric": label,
        "status": PASS if delta <= allowed else WARN,
        "scout": round(s, 6),
        "independent": round(i, 6),
        "delta": round(delta, 6),
        "tolerance": round(allowed, 6),
    }


def run_ssh(host: str, user: str, command: str, *, timeout: float = 30.0) -> tuple[int, str]:
    target = f"{user}@{host}" if user else host
    proc = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", target, command],
        capture_output=True, text=True, timeout=timeout,
    )
    output = (proc.stdout + ("\n" + proc.stderr if proc.stderr else "")).strip()
    return proc.returncode, output


def add(checks: list[Check], name: str, status: str, detail: str, data: dict[str, Any] | None = None) -> None:
    checks.append(Check(name, status, detail, data))
    print(f"[{status:6}] {name}: {detail}")


def endpoint(base: str, path: str) -> str:
    return base.rstrip("/") + path


def main() -> int:
    parser = argparse.ArgumentParser(description="StockHunter Scout production end-to-end validator")
    parser.add_argument("--api-base", default=os.getenv("SCOUT_E2E_API_BASE", "https://srv1170872.tail86523.ts.net:8444"))
    parser.add_argument("--expected-version", default="")
    parser.add_argument("--sample-size", type=int, default=8)
    parser.add_argument("--progress-seconds", type=int, default=20)
    parser.add_argument("--ssh-host", default=os.getenv("SCOUT_E2E_SSH_HOST", "srv1170872.tail86523.ts.net"))
    parser.add_argument("--ssh-user", default=os.getenv("SCOUT_E2E_SSH_USER", "wavystack"))
    parser.add_argument("--skip-ssh", action="store_true")
    parser.add_argument("--test-android-notification", action="store_true")
    parser.add_argument("--output", default="e2e-validation-latest.json")
    args = parser.parse_args()

    checks: list[Check] = []
    samples: list[dict[str, Any]] = []
    started = time.time()
    print("=" * 72)
    print("SCOUT END-TO-END VALIDATION")
    print(datetime.now(timezone.utc).isoformat())
    print("=" * 72)

    try:
        h1 = fetch_json(endpoint(args.api_base, "/healthz"), timeout=20)
        version = str(h1.get("version", ""))
        ok = bool(h1.get("ok")) and bool(h1.get("hybrid_ready"))
        expected_ok = not args.expected_version or version == args.expected_version
        add(checks, "Backend health", PASS if ok and expected_ok else FAIL,
            f"version={version} hybrid_ready={h1.get('hybrid_ready')} universe={h1.get('universe')}")
    except Exception as exc:
        add(checks, "Backend health", FAIL, str(exc))
        h1 = {}

    if h1:
        hybrid1 = h1.get("hybrid", {})
        submitted1 = int(hybrid1.get("submitted") or 0)
        dropped1 = int(hybrid1.get("dropped") or 0)
        time.sleep(max(1, args.progress_seconds))
        try:
            h2 = fetch_json(endpoint(args.api_base, "/healthz"), timeout=20)
            submitted2 = int(h2.get("hybrid", {}).get("submitted") or 0)
            progressing = submitted2 > submitted1
            hybrid2 = h2.get("hybrid", {})
            dropped = int(hybrid2.get("dropped") or 0)
            dropped_delta = max(0, dropped - dropped1)
            queue = int(hybrid2.get("queue_depth") or 0)
            capacity = max(1, int(hybrid2.get("queue_capacity") or 1))
            utilization = float(hybrid2.get("queue_utilization") or (queue / capacity))
            backpressure = str(hybrid2.get("backpressure") or "unknown")
            sip = bool(h2.get("feed_health", {}).get("sip", {}).get("connected"))
            boats = bool(h2.get("feed_health", {}).get("boats", {}).get("connected"))
            watchdog = h2.get("watchdog", {})
            ingest_ok = progressing and dropped_delta == 0 and utilization < 0.90 and sip
            ingest_status = PASS if ingest_ok else FAIL
            add(checks, "Market ingest progress", ingest_status,
                f"submitted {submitted1}->{submitted2}; dropped {dropped1}->{dropped} (+{dropped_delta}); "
                f"queue={queue}/{capacity} ({utilization:.1%}) backpressure={backpressure}; SIP={sip}; BOATS={boats}",
                {"submitted_before": submitted1, "submitted_after": submitted2, "dropped_before": dropped1,
                 "dropped_after": dropped, "dropped_delta": dropped_delta, "queue": queue,
                 "queue_capacity": capacity, "queue_utilization": utilization, "backpressure": backpressure})
            add(checks, "Runtime watchdog", PASS if int(watchdog.get("recoveries") or 0) == 0 else WARN,
                f"recoveries={watchdog.get('recoveries')} max_lag={watchdog.get('max_lag_seconds')}s")
        except Exception as exc:
            add(checks, "Market ingest progress", FAIL, str(exc))

    try:
        status = fetch_json(endpoint(args.api_base, "/api/status"), timeout=30)
        notifications = status.get("notifications", {})
        add(checks, "Application status API", PASS, f"tracked_states={status.get('tracked_states')} latest_findings={len(status.get('latest_findings', []))}")
        delivery = notifications.get("delivery", {})
        ntfy = delivery.get("ntfy", {})
        ntfy_ok = bool(ntfy.get("last_success_at")) and not ntfy.get("last_error")
        add(checks, "Scout -> ntfy publisher", PASS if ntfy_ok else WARN,
            f"configured={notifications.get('android_delivery_configured')} last_success={ntfy.get('last_success_at')} last_error={ntfy.get('last_error')}")
        if notifications.get("windows_enabled"):
            add(checks, "Windows notification preference", INFO, "Windows preference is enabled; native delivery must be confirmed in the installed Tauri client.")
        else:
            add(checks, "Windows notification preference", WARN, "Windows notification preference is disabled or unavailable in server preferences.")
    except Exception as exc:
        status = {}
        add(checks, "Application status API", FAIL, str(exc))

    for label, path in (
        ("Findings API", f"/api/findings?limit={max(10, args.sample_size)}&episodes=1"),
        ("Attention API", "/api/attention?limit=20"),
        ("Catalysts API", "/api/catalysts?limit=20"),
        ("Notification preferences API", "/api/notifications/preferences"),
    ):
        try:
            payload = fetch_json(endpoint(args.api_base, path), timeout=30)
            count = len(payload.get("items", [])) if isinstance(payload, dict) and "items" in payload else len(payload) if isinstance(payload, list) else 1
            add(checks, label, PASS, f"HTTP/JSON OK; items={count}")
        except Exception as exc:
            add(checks, label, FAIL, str(exc))

    try:
        findings_payload = fetch_json(endpoint(args.api_base, f"/api/findings?limit={max(20, args.sample_size * 2)}&episodes=1"), timeout=30)
        findings = findings_payload.get("items", []) if isinstance(findings_payload, dict) else []
        selected = [f for f in findings if f.get("ticker") and f.get("detected_at")][: max(1, args.sample_size)]
        metric_checks = 0
        metric_warns = 0
        for finding in selected:
            ticker = str(finding["ticker"]).upper()
            detected_at = float(finding["detected_at"])
            finding_id = finding.get("id")
            query = urllib.parse.urlencode({"detected_at": detected_at, "bucket_seconds": int(finding.get("detection_timeframe_seconds") or 15), "finding_id": finding_id or ""})
            try:
                snap = fetch_json(endpoint(args.api_base, f"/api/market/snapshot/{urllib.parse.quote(ticker)}?{query}"), timeout=30)
                independent = independent_bucket_metrics(snap.get("buckets", []), detected_at, int(finding.get("detection_timeframe_seconds") or 15))
                comparisons = [
                    compare_metric("price", finding.get("price"), independent.get("close"), abs_tolerance=0.03, relative_tolerance=0.01),
                    compare_metric("change_15s_pct", finding.get("change_15s_pct"), independent.get("change_15s_pct"), abs_tolerance=1.25),
                    compare_metric("change_30s_pct", finding.get("change_30s_pct"), independent.get("change_30s_pct"), abs_tolerance=1.5),
                    compare_metric("change_60s_pct", finding.get("change_60s_pct"), independent.get("change_60s_pct"), abs_tolerance=2.0),
                ]
                usable = [c for c in comparisons if c["status"] != "NA"]
                metric_checks += len(usable)
                metric_warns += sum(1 for c in usable if c["status"] == WARN)
                samples.append({"finding_id": finding_id, "ticker": ticker, "stage": finding.get("stage"), "snapshot_source": snap.get("source"), "comparisons": comparisons})
            except Exception as exc:
                samples.append({"finding_id": finding_id, "ticker": ticker, "stage": finding.get("stage"), "error": str(exc)})
        if metric_checks:
            agreement = 100.0 * (metric_checks - metric_warns) / metric_checks
            add(checks, "Independent candle recomputation", PASS if agreement >= 80 else WARN,
                f"{metric_checks - metric_warns}/{metric_checks} coarse metrics within tolerance ({agreement:.1f}%); same Scout market-data source, independently recomputed")
        else:
            add(checks, "Independent candle recomputation", WARN, "No comparable detection-window candles were available.")
    except Exception as exc:
        add(checks, "Independent candle recomputation", WARN, str(exc))

    if not args.skip_ssh:
        try:
            remote = (
                "cd /opt/apps/scout && "
                "printf 'SCOUT '; docker stats stockhunter-scout --no-stream --format 'CPU={{.CPUPerc}} MEM={{.MemUsage}} PIDS={{.PIDs}}'; "
                "printf 'NTFY '; docker inspect stockhunter-ntfy --format 'status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}'; "
                "printf 'SCOUT_CONTAINER '; docker inspect stockhunter-scout --format 'status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} restart={{.RestartCount}}'"
            )
            code, output = run_ssh(args.ssh_host, args.ssh_user, remote)
            add(checks, "VPS containers/resources", PASS if code == 0 else WARN, output.replace("\n", " | "))
        except Exception as exc:
            add(checks, "VPS containers/resources", WARN, f"SSH check skipped/failed: {exc}")

        try:
            code, output = run_ssh(args.ssh_host, args.ssh_user,
                "docker exec stockhunter-scout python -c \"import sqlite3;d=sqlite3.connect('/data/state.db');print([r[1] for r in d.execute('PRAGMA index_list(findings)')])\"")
            index_ok = "ix_findings_hybrid_key_time" in output
            add(checks, "Production DB index", PASS if code == 0 and index_ok else WARN, output)
        except Exception as exc:
            add(checks, "Production DB index", WARN, str(exc))

        try:
            code, output = run_ssh(args.ssh_host, args.ssh_user,
                "docker logs stockhunter-ntfy --since 10m 2>&1 | grep 'Server stats' | tail -1")
            subscriber_known = "subscribers=" in output
            add(checks, "ntfy subscriber telemetry", INFO if subscriber_known else WARN, output or "No recent ntfy manager stats")
        except Exception as exc:
            add(checks, "ntfy subscriber telemetry", WARN, str(exc))

    if args.test_android_notification:
        try:
            result = fetch_json(endpoint(args.api_base, "/api/notifications/test"), method="POST", payload={"platform": "android"}, timeout=30)
            add(checks, "Android notification provider test", PASS if result.get("ok") else FAIL,
                f"platform={result.get('platform')} message={result.get('message')}")
            add(checks, "Phone receipt", MANUAL, "Confirm the Scout test notification appeared on the phone. Provider acceptance cannot prove OS presentation.")
        except Exception as exc:
            add(checks, "Android notification provider test", FAIL, str(exc))

    add(checks, "Windows native toast/sound", MANUAL,
        "Run the installed Scout client's Notifications > Platforms test. Server-side E2E cannot prove a Windows OS toast or sound was presented.")
    add(checks, "Independent external-feed truth", INFO,
        "Not claimed: this harness independently recomputes from Scout-exposed candles. A true second-feed comparison requires a separately credentialed market-data provider.")

    fail_count = sum(c.status == FAIL for c in checks)
    warn_count = sum(c.status == WARN for c in checks)
    report = {
        "schema": 1,
        "started_at": started,
        "finished_at": time.time(),
        "api_base": args.api_base,
        "checks": [asdict(c) for c in checks],
        "samples": samples,
        "summary": {"failures": fail_count, "warnings": warn_count, "result": FAIL if fail_count else PASS},
    }
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("=" * 72)
    print(f"OVERALL: {report['summary']['result']} | failures={fail_count} warnings={warn_count}")
    print(f"Report: {output_path}")
    print("=" * 72)
    return 1 if fail_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
