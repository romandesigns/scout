from __future__ import annotations

import base64
import binascii
import json
import threading
import time
from bisect import bisect_left
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from .config import settings
from .live_replay import finding_from_row, run_live_detector, run_rust_detector
from .momentum_zones import find_momentum_zones, match_detections_to_zones
from .significance_tier import classify_tier, would_notify as preview_would_notify

ET = ZoneInfo(settings.timezone)
ALLOWED_TIMEFRAMES = {30, 60, 300}
RENDER_LOCK = threading.Lock()
MAX_ANNOTATION_BYTES = 12 * 1024 * 1024


def _rust_candidate_row(candidate: dict, index: int) -> dict:
    market_state = dict(candidate.get("market_state") or {})
    qualified = bool(market_state.get("qualified"))
    raw_stage = str(candidate.get("stage") or "REJECTED")
    stage = "IGNITION" if qualified else "EARLY_SIGNAL" if raw_stage in {"SHAPING_UP", "REARMED"} else raw_stage
    profile = {
        "rust_recipe_stage": raw_stage,
        "multi_timeframe": {
            "qualified": qualified,
            "blockers": list(market_state.get("blockers") or []),
            "five_minute_change_pct": market_state.get("five_minute_change_pct"),
            "one_minute_change_pct": market_state.get("one_minute_change_pct"),
            "change_30s_pct": market_state.get("thirty_second_change_pct"),
        },
    }
    return {
        "id": -(index + 1), "ticker": candidate["ticker"], "stage": stage,
        "detected_at": float(candidate["detected_at"]), "price": float(candidate["price"]),
        "score": int(candidate.get("confidence") or candidate.get("recipe_score") or 0),
        "vol_ratio_15s": float(candidate.get("trade_acceleration") or 0),
        "vol_ratio_30s": float(candidate.get("dollar_acceleration") or 0),
        "change_60s_pct": float(market_state.get("one_minute_change_pct") or 0),
        "change_30s_pct": float(market_state.get("thirty_second_change_pct") or 0),
        "change_15s_pct": None, "change_5s_pct": float(market_state.get("five_second_change_pct") or 0),
        "extension_pct": float(market_state.get("extension_pct") or candidate.get("base_extension_pct") or 0),
        "ema9": None, "ema21": None, "ema9_slope": None, "vwap": None, "above_vwap": False,
        "quiet_break": qualified, "evidence": list(candidate.get("recipe_present") or []),
        "quality_label": "CLEAN" if qualified else "DEVELOPING",
        "quality_score": int(candidate.get("recipe_score") or 0) * 10,
        "actionable_rank": "A" if qualified else "C", "shadow_mode": not qualified,
        "rejection_reasons": list(candidate.get("recipe_missing") or []) + list(market_state.get("blockers") or []),
        "candidate_profile": profile, "episode_id": int(candidate.get("episode_id") or 0),
        "trigger_level": candidate.get("trigger_level"), "invalidation_level": candidate.get("invalidation_level"),
        "lifecycle_phase": candidate.get("lifecycle_phase"), "recipe_score": int(candidate.get("recipe_score") or 0),
        "recipe_present": list(candidate.get("recipe_present") or []), "recipe_missing": list(candidate.get("recipe_missing") or []),
        "trigger_distance_pct": candidate.get("trigger_distance_pct"),
        "base_extension_at_detection_pct": candidate.get("base_extension_pct"),
        "engine_source": "rust", "hybrid_sources": ["rust"],
        "hybrid_score": int(candidate.get("confidence") or 0),
        "hybrid_key": f"{candidate['ticker']}:rust-replay:{int(candidate.get('episode_id') or 0)}",
        "trace_timestamps": dict(candidate.get("trace") or {}),
    }


def save_annotation_artifact(evaluation_id: int, ticker: str, image_data_url: str,
                             notes: str = "", out_dir: Path | None = None,
                             evaluation: dict | None = None) -> dict:
    """Persist a client-annotated audit without replacing the source chart."""
    if not image_data_url.startswith("data:image/png;base64,"):
        raise ValueError("annotation must be a base64 PNG")
    try:
        payload = base64.b64decode(image_data_url.split(",", 1)[1], validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("annotation PNG is not valid base64") from exc
    if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("annotation payload is not a PNG")
    if len(payload) > MAX_ANNOTATION_BYTES:
        raise ValueError("annotation PNG exceeds the 12 MB limit")
    safe_ticker = "".join(char for char in ticker.upper() if char.isalnum() or char in ".-")[:16] or "CHART"
    target_dir = (out_dir or settings.chart_dir) / "annotations"
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(ET).strftime("%Y%m%d-%H%M%S-%f")
    stem = f"dev-annotation-{int(evaluation_id)}-{safe_ticker}-{stamp}"
    image_path = target_dir / f"{stem}.png"
    metadata_path = target_dir / f"{stem}.json"
    image_path.write_bytes(payload)
    metadata_path.write_text(json.dumps({
        "evaluation_id": int(evaluation_id), "ticker": safe_ticker,
        "notes": str(notes)[:4000], "created_at": time.time(),
        "image": image_path.name,
        "evaluation": evaluation,
    }, indent=2), encoding="utf-8")
    share_prompt = (
        f"Analyze the Scout development chart at {image_path}. "
        f"Use its evaluation context at {metadata_path}."
    )
    return {
        "name": image_path.name,
        "chart_url": f"/charts/annotations/{image_path.name}",
        "workspace_path": str(image_path),
        "notes_path": str(metadata_path),
        "review_path": str(metadata_path),
        "share_prompt": share_prompt,
    }


def _chart_x(visible: list[dict], timestamp: float, timeframe_seconds: int) -> float:
    starts = [float(row["start_ts"]) for row in visible]
    index = bisect_left(starts, timestamp)
    if index <= 0:
        return (timestamp - starts[0]) / timeframe_seconds
    if index >= len(starts):
        return len(starts) - 1 + (timestamp - starts[-1]) / timeframe_seconds
    left, right = starts[index - 1], starts[index]
    return index - 1 + ((timestamp - left) / max(right - left, 1.0))


def _pct(origin: float, value: float) -> float:
    return ((value / origin) - 1.0) * 100.0 if origin > 0 else 0.0


def _window(rows: list[dict], detected_at: float, seconds: int) -> list[dict]:
    return [row for row in rows if detected_at <= float(row["start_ts"]) <= detected_at + seconds]


def _forward_metrics(rows: list[dict], finding: dict | None, detected_at: float) -> dict:
    before = [row for row in rows if float(row["start_ts"]) <= detected_at]
    detection_price = float((finding or {}).get("price") or (before[-1]["close"] if before else rows[0]["open"]))
    metrics: dict[str, object] = {"detection_price": detection_price}
    last_market_ts = max((float(row["start_ts"]) for row in rows), default=detected_at)
    complete_through_seconds = max(0.0, last_market_ts - detected_at)
    for label, seconds in (("30s", 30), ("1m", 60), ("5m", 300), ("15m", 900)):
        window = _window(rows, detected_at, seconds)
        metrics[f"max_{label}_pct"] = round(_pct(detection_price, max((float(row["high"]) for row in window), default=detection_price)), 3) if complete_through_seconds >= seconds else None
        metrics[f"min_{label}_pct"] = round(_pct(detection_price, min((float(row["low"]) for row in window), default=detection_price)), 3) if complete_through_seconds >= seconds else None
    trigger = (finding or {}).get("trigger_level")
    invalidation = (finding or {}).get("invalidation_level")
    trigger = float(trigger) if trigger else detection_price
    invalidation = float(invalidation) if invalidation and float(invalidation) < trigger else detection_price * .98
    risk = max(trigger - invalidation, trigger * .0025)
    future = _window(rows, detected_at, 900)
    best = max((float(row["high"]) for row in future), default=detection_price)
    worst = min((float(row["low"]) for row in future), default=detection_price)
    prior = [row for row in rows if detected_at - 900 <= float(row["start_ts"]) <= detected_at]
    origin_low = min((float(row["low"]) for row in prior), default=detection_price)
    available_move = max(best - origin_low, 0.0)
    captured_move = max(best - detection_price, 0.0)
    first_touch = "NONE"
    for row in future:
        hit_stop = float(row["low"]) <= invalidation
        hit_target = float(row["high"]) >= trigger + 3 * risk
        if hit_stop and hit_target:
            first_touch = "AMBIGUOUS_BAR"
            break
        if hit_stop:
            first_touch = "STOP_FIRST"
            break
        if hit_target:
            first_touch = "3R_FIRST"
            break
    metrics.update({
        "trigger": round(trigger, 4), "invalidation": round(invalidation, 4),
        "risk_per_share": round(risk, 4), "max_favorable_r": round((best - trigger) / risk, 3),
        "max_adverse_r": round((trigger - worst) / risk, 3),
        "hit_1r": best >= trigger + risk, "hit_2r": best >= trigger + 2 * risk,
        "hit_3r": best >= trigger + 3 * risk, "invalidated": worst <= invalidation,
        "first_touch": first_touch, "available_move_pct": round(_pct(origin_low, best), 3),
        "captured_move_pct": round(_pct(detection_price, best), 3),
        "capture_efficiency_pct": round(captured_move / available_move * 100.0, 2) if available_move else 0.0,
    })
    metrics["complete_through_seconds"] = round(complete_through_seconds, 1)
    if complete_through_seconds < 300:
        verdict = "PENDING"
    elif first_touch == "3R_FIRST" or (bool(metrics["hit_3r"]) and not bool(metrics["invalidated"])):
        verdict = "WINNER"
    elif first_touch == "STOP_FIRST" or (bool(metrics["invalidated"]) and not bool(metrics["hit_1r"])):
        verdict = "FAILED"
    elif float(metrics["max_favorable_r"]) > float(metrics["max_adverse_r"]):
        verdict = "PARTIAL"
    else:
        verdict = "NO_EDGE"
    metrics["verdict"] = verdict
    metrics["formation"] = {
        "stage": (finding or {}).get("stage"), "rank": (finding or {}).get("actionable_rank"),
        "quality": (finding or {}).get("quality_label"), "score": (finding or {}).get("score"),
        "timeframe_seconds": (finding or {}).get("detection_timeframe_seconds"),
        "catalyst": (finding or {}).get("catalyst_headline"),
    }
    return metrics


def _render(ticker: str, rows: list[dict], finding: dict | None, detected_at: float,
            timeframe_seconds: int, metrics: dict, out_dir: Path,
            inspection_start: float | None = None, inspection_end: float | None = None,
            notifications: list[dict] | None = None,
            detections: list[dict] | None = None,
            momentum_zones: list[dict] | None = None) -> str:
    visible = [row for row in rows if
               (inspection_start if inspection_start is not None else detected_at - 15 * 60)
               <= float(row["start_ts"])
               <= (inspection_end if inspection_end is not None else detected_at + 15 * 60)]
    if not visible:
        raise LookupError("no Alpaca candles exist inside the selected inspection range")
    fig, (ax, volume_ax) = plt.subplots(2, 1, figsize=(13, 7), dpi=140, sharex=True,
                                        gridspec_kw={"height_ratios": [4, 1], "hspace": .04})
    for index, row in enumerate(visible):
        up = float(row["close"]) >= float(row["open"])
        color = "#2ed6a1" if up else "#ff657a"
        ax.vlines(index, float(row["low"]), float(row["high"]), color=color, linewidth=.8)
        bottom = min(float(row["open"]), float(row["close"]))
        height = max(abs(float(row["close"]) - float(row["open"])), float(row["close"]) * .0002)
        ax.add_patch(Rectangle((index - .34, bottom), .68, height, color=color, alpha=.9))
        volume_ax.bar(index, float(row.get("volume") or 0), width=.7, color=color, alpha=.45)
    detection_index = min(range(len(visible)), key=lambda i: abs(float(visible[i]["start_ts"]) - detected_at))
    reference_label = "Selected detection" if finding else "Inspection reference"
    ax.axvline(detection_index, color="#4aa8ff", linewidth=1.5, linestyle="--", label=reference_label)
    visible_start = max(float(visible[0]["start_ts"]), inspection_start) if inspection_start is not None else float(visible[0]["start_ts"])
    visible_end = min(float(visible[-1]["start_ts"]) + timeframe_seconds, inspection_end) if inspection_end is not None else float(visible[-1]["start_ts"]) + timeframe_seconds
    zone_markers = []
    zones_caught = 0
    for zone_index, zone in enumerate(momentum_zones or []):
        onset_at, peak_at = float(zone["onset_at"]), float(zone["peak_at"])
        if not (visible_start <= peak_at and onset_at <= visible_end):
            continue
        caught = bool(zone.get("caught"))
        zones_caught += int(caught)
        zone_color = "#2ed6a1" if caught else "#ffb020"
        x0 = _chart_x(visible, max(onset_at, visible_start), timeframe_seconds)
        x1 = _chart_x(visible, min(peak_at, visible_end), timeframe_seconds)
        ax.axvspan(x0, x1, color=zone_color, alpha=.08, zorder=1,
                   label=("Real momentum -- caught" if caught else "Real momentum -- MISSED") if not zone_markers else None)
        label_x = (x0 + x1) / 2
        lead = zone.get("lead_seconds")
        lead_text = f" ({lead:+.0f}s lead)" if lead is not None else ""
        ax.annotate(f"+{zone['expansion_pct']:.1f}% {'CAUGHT' if caught else 'MISSED'}{lead_text}",
                    (label_x, zone["peak_price"]), xytext=(0, 6), textcoords="offset points",
                    ha="center", va="bottom", fontsize=7, color=zone_color, weight="bold", zorder=6)
        zone_markers.append({**zone, "visible": True})
    metrics["momentum_zones"] = zone_markers
    metrics["momentum_zones_marked"] = len(zone_markers)
    metrics["momentum_zones_caught"] = zones_caught
    metrics["momentum_catch_rate_pct"] = round(zones_caught / len(zone_markers) * 100.0, 1) if zone_markers else None
    detection_markers = []
    would_notify_markers = []
    gate_passes = 0
    tier_counts = {1: 0, 2: 0, 3: 0}
    would_notify_seen = 0
    TIER_COLORS = {1: "#ff5d73", 2: "#4aa8ff", 3: "#6b7686"}
    TIER_LABELS = {1: "T1 breakout", 2: "T2 continuation", 3: "T3 bounce"}
    for detected in detections or []:
        marked_at = float(detected.get("detected_at") or 0)
        if not visible_start <= marked_at <= visible_end:
            continue
        index = _chart_x(visible, marked_at, timeframe_seconds)
        nearest = min(range(len(visible)), key=lambda i: abs(float(visible[i]["start_ts"]) - marked_at))
        price = float(detected.get("price") or visible[nearest]["close"])
        profile = detected.get("candidate_profile") or {}
        gate = profile.get("imminent_move_gate") or {}
        # Recomputed fresh from the stored detection's own fields (not read
        # back from candidate_profile) so this works retroactively for every
        # historical detection, including ones stored before this tiering
        # existed -- not only detections saved after this code shipped.
        tier_info = classify_tier(detected)
        notify_preview = preview_would_notify(detected)
        would_pass = gate.get("would_pass")
        gate_status = "PASS" if would_pass is True else "REJECT" if would_pass is False else "UNSCORED"
        gate_passes += int(would_pass is True)
        tier = tier_info.get("tier")
        tier_label = TIER_LABELS.get(tier, "unscored")
        if tier in tier_counts:
            tier_counts[tier] += 1
        # Color marks Scout's significance tier (JUNS/WEN chart-review
        # framework: structural breakout vs continuation vs reaction bounce --
        # see IMPLEMENTATION_DECISIONS.md 2026-08-22). This is what "Scout
        # identified as what we're after"; the shadow ML gate (gate_status) is
        # shown as secondary annotation text since it is not deployed by default.
        color = TIER_COLORS.get(tier, "#f5b84b")
        ax.axvline(index, color=color, linewidth=1.0, alpha=.8,
                   label=TIER_LABELS[tier] if tier in tier_counts and tier_counts[tier] == 1 else None)
        ax.axvspan(
            _chart_x(visible, marked_at + 15, timeframe_seconds),
            _chart_x(visible, marked_at + 30, timeframe_seconds),
            color=color, alpha=.10,
            label="15–30s detection area" if not detection_markers else None,
        )
        stage = str(detected.get("stage") or "DETECTION")
        is_rejected = stage in {"REJECTED", "STIRRING"}
        actionable = str(detected.get("actionable_rank") or "").upper() == "A" and not bool(detected.get("shadow_mode"))
        marker_kind = "rejected" if is_rejected else "actionable" if actionable else "detected"
        if is_rejected:
            ax.scatter(index, price, marker="x", s=38, color="#7b8798", linewidths=.8, zorder=6)
        elif actionable:
            ax.scatter(index, price, marker="o", s=72, color=color, edgecolors="#101820", linewidths=.7, zorder=7)
        else:
            ax.scatter(index, price, marker="o", s=64, facecolors="none", edgecolors=color, linewidths=1.2, zorder=7)
        ax.annotate(f"{stage} · {tier_label} · gate {gate_status}", (index, price), xytext=(0, -15), textcoords="offset points",
                    ha="center", va="top", fontsize=6.5, color=color, weight="bold")
        would_notify = bool(notify_preview.get("would_notify"))
        if would_notify:
            would_notify_seen += 1
            ax.scatter(index, price, marker="s", s=150, facecolors="#39d2c0", edgecolors="#101820", linewidths=1.0,
                       zorder=9, label="Would notify (preview)" if would_notify_seen == 1 else None)
        elif actionable:
            ax.scatter(index, price, marker="s", s=130, facecolors="none", edgecolors="#ffb020", linewidths=1.4,
                       zorder=8, label="Notification blocked" if not any(row.get("notification_blocked") for row in detection_markers) else None)
        invalidation = detected.get("invalidation_level")
        invalidated_at = None
        if invalidation and not is_rejected:
            for bar in visible[nearest:]:
                if float(bar["low"]) <= float(invalidation):
                    invalidated_at = float(bar["start_ts"])
                    invalidation_x = _chart_x(visible, invalidated_at, timeframe_seconds)
                    ax.scatter(invalidation_x, float(invalidation), marker="x", s=70, color="#ff657a", linewidths=1.6, zorder=9)
                    break
        detection_markers.append({
            "finding_id": detected.get("id"), "stage": stage, "detected_at": marked_at,
            "price": price, "gate_status": gate_status, "gate_probability": gate.get("probability"),
            "tier": tier, "tier_label": tier_label, "tier_reasons": tier_info.get("reasons"),
            "reaction_bounce": tier_info.get("reaction_bounce"),
            "would_notify": would_notify, "would_notify_reason": notify_preview.get("reason"),
            "notification_blocked": actionable and not would_notify,
            "marker_kind": marker_kind, "invalidated_at": invalidated_at,
            "recipe_present": detected.get("recipe_present") or [], "recipe_missing": detected.get("recipe_missing") or [],
            "blockers": ((detected.get("candidate_profile") or {}).get("multi_timeframe") or {}).get("blockers") or [],
            "selected": detected.get("id") == (finding or {}).get("id"),
        })
        if would_notify:
            would_notify_markers.append({"finding_id": detected.get("id"), "detected_at": marked_at, "price": price, "stage": stage})
    metrics["detections_marked"] = len(detection_markers)
    metrics["gate_passes_marked"] = gate_passes
    metrics["detection_markers"] = detection_markers
    metrics["tier_counts"] = {f"tier_{key}": value for key, value in tier_counts.items()}
    metrics["would_notify_preview_marked"] = len(would_notify_markers)
    metrics["would_notify_preview_markers"] = would_notify_markers
    notification_markers = []
    for notified in notifications or []:
        notified_at = float(notified.get("notification_delivered_at") or 0)
        if not visible_start <= notified_at <= visible_end:
            continue
        index = min(range(len(visible)), key=lambda i: abs(float(visible[i]["start_ts"]) - notified_at))
        price = float(visible[index]["close"])
        stage = str(notified.get("stage") or "ALERT")
        ax.scatter(index, price, s=210, facecolors="none", edgecolors="#ffd166", linewidths=2.2,
                   zorder=8, label="Scout notification" if not notification_markers else None)
        ax.annotate(stage, (index, price), xytext=(0, 13), textcoords="offset points",
                    ha="center", va="bottom", fontsize=7, color="#ffd166", weight="bold")
        notification_markers.append({
            "finding_id": notified.get("id"), "stage": stage, "notified_at": notified_at,
            "price": price,
        })
    metrics["notifications_marked"] = len(notification_markers)
    metrics["notification_markers"] = notification_markers
    for key, color, label in (("trigger", "#f5b84b", "Trigger"), ("invalidation", "#ff657a", "Invalidation")):
        value = metrics.get(key)
        if value:
            ax.axhline(float(value), color=color, linewidth=1.0, linestyle=":", label=label)
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(alpha=.12); volume_ax.grid(alpha=.08)
    step = max(1, len(visible) // 8)
    ticks = list(range(0, len(visible), step))
    volume_ax.set_xticks(ticks)
    volume_ax.set_xticklabels([datetime.fromtimestamp(float(visible[i]["start_ts"]), ET).strftime("%H:%M:%S") for i in ticks], fontsize=8)
    when = datetime.fromtimestamp(detected_at, ET).strftime("%Y-%m-%d %H:%M:%S ET")
    fig.suptitle(f"{ticker} · Scout formation audit · {timeframe_seconds}s · detected {when}\n"
                 f"{metrics['verdict']} · MFE {metrics['max_favorable_r']:+.2f}R · MAE {metrics['max_adverse_r']:+.2f}R · 3R {'yes' if metrics['hit_3r'] else 'no'}")
    fig.text(.01, .01, "Source: Alpaca market-data API. The blue line is the original Scout detection timestamp. "
                       "Red/blue/gray = Tier 1/2/3 significance. Cyan star = would notify (preview, gated to opportunity+quality; user preferences not applied). "
                       "Green/orange shading = real price-only momentum zone, independent of Scout -- green if a Tier 1/2 or would-notify detection caught it, orange if not.",
             fontsize=7)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromtimestamp(detected_at, ET).strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"dev-{stamp}-{ticker}-{timeframe_seconds}s.png"
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return str(path)


def evaluate_ticker(store, market, ticker: str, detection_at: float | None = None,
                    timeframe_seconds: int = 60, use_latest_finding: bool = True,
                    inspection_start: float | None = None, inspection_end: float | None = None,
                    use_live_detector: bool = False, detector_engine: str = "python") -> dict:
    ticker = ticker.strip().upper()
    if not ticker or not ticker.replace(".", "").replace("-", "").isalnum():
        raise ValueError("invalid ticker")
    timeframe_seconds = int(timeframe_seconds)
    if timeframe_seconds not in ALLOWED_TIMEFRAMES:
        raise ValueError("timeframe_seconds must be 30, 60, or 300")
    if use_live_detector and (inspection_start is None or inspection_end is None):
        raise ValueError("live detector replay requires an inspection start and end")
    if detector_engine not in {"python", "rust", "both"}:
        raise ValueError("detector_engine must be python, rust, or both")
    finding = None
    candidates = store.list_findings(500, ticker=ticker)
    if use_latest_finding:
        detection_candidates = candidates
        if inspection_start is not None and inspection_end is not None:
            detection_candidates = [item for item in detection_candidates if inspection_start <= float(item["detected_at"]) <= inspection_end]
        finding = detection_candidates[0] if detection_candidates else None
        if finding is None and not (inspection_start is not None and inspection_end is not None):
            suffix = " inside the selected range" if inspection_start is not None else ""
            raise LookupError(f"no Scout detection is stored for {ticker}{suffix}")
    center = float(detection_at or (finding or {}).get("detected_at") or
                   ((inspection_start + inspection_end) / 2 if inspection_start is not None and inspection_end is not None else time.time()))
    snapshot = market.historical_snapshot_sync(ticker, center, timeframe_seconds, inspection_start, inspection_end)
    rows = snapshot["buckets"]
    metrics = _forward_metrics(rows, finding, center)
    metrics["detection_match"] = finding is not None
    metrics["detection_note"] = (
        "Matched stored Scout detection"
        if finding else "No stored Scout detection in range; chart centered on the requested section"
    )
    detections_for_chart = candidates
    rust_evaluation_trace: list[dict] = []
    if use_live_detector:
        # Run Scout's actual production detector against real Alpaca trades
        # for this exact window -- answers "what would Scout have flagged
        # here", not "what's already stored". See app/live_replay.py.
        python_replay = run_live_detector(ticker, inspection_start, inspection_end) if detector_engine in {"python", "both"} else None
        rust_replay = run_rust_detector(ticker, inspection_start, inspection_end) if detector_engine in {"rust", "both"} else None
        live_findings = list((python_replay or {}).get("findings") or [])
        rust_transitions = [_rust_candidate_row(row, index) for index, row in enumerate((rust_replay or {}).get("findings") or [])]
        rust_evaluations = [_rust_candidate_row(row, index + len(rust_transitions)) for index, row in enumerate((rust_replay or {}).get("evaluations") or [])]
        rust_evaluation_trace = rust_evaluations
        live_findings.extend(rust_transitions)
        for row in live_findings:
            row["candidate_profile"] = dict(row.get("candidate_profile") or {})
            try:
                row["candidate_profile"]["edge_validation"] = store.paper_edge_validation(finding_from_row(row))
            except Exception as exc:
                row["candidate_profile"]["edge_validation"] = {"validated": False, "error": str(exc)[:200]}
        # Preserve the strongest rejected/watching Rust evaluation per 15-second
        # chart bucket. The complete one-per-second trace remains in metrics,
        # while the sampled markers keep the chart legible.
        sampled_rejections: dict[int, dict] = {}
        for item in rust_evaluations:
            if item["stage"] not in {"REJECTED", "STIRRING", "EARLY_SIGNAL"}:
                continue
            bucket = int(float(item["detected_at"]) // 15)
            previous = sampled_rejections.get(bucket)
            if previous is None or int(item.get("recipe_score") or 0) > int(previous.get("recipe_score") or 0):
                sampled_rejections[bucket] = item
        detections_for_chart = live_findings + list(sampled_rejections.values())
        metrics["rust_evaluation_count"] = len(rust_evaluations)
        metrics["rust_rejected_count"] = sum(item["stage"] == "REJECTED" for item in rust_evaluations)
        metrics["rust_evaluation_trace_path"] = (rust_replay or {}).get("evaluation_trace_path")
        metrics["live_replay"] = {
            "status": (rust_replay or python_replay or {})["status"],
            "processed_events": sum(int(item.get("processed_events") or 0) for item in (python_replay, rust_replay) if item),
            "findings_count": len(live_findings),
            "engine": detector_engine,
        }
        metrics["detection_note"] = (
            f"{detector_engine.title()} detector replay: {len(live_findings)} transition(s) from "
            f"{metrics['live_replay']['processed_events']} canonical market events in this window"
            if metrics["live_replay"]["status"] == "OK" else
            f"Live detector replay found no events in this window ({metrics['live_replay']['status']})"
        )
    # Ground-truth bullish momentum zones, computed straight from price/volume
    # -- independent of anything Scout's detector found -- then checked
    # against whichever of Scout's own detections would actually count as
    # "caught this" (tier 1/2, or the notify-preview firing). This is what
    # lets the chart show detector accuracy, not just detector output.
    qualifying_detections = []
    for item in detections_for_chart:
        tier_info = classify_tier(item)
        notify_info = preview_would_notify(item)
        if tier_info.get("tier") in (1, 2) or notify_info.get("would_notify"):
            qualifying_detections.append(item)
    momentum_zones = match_detections_to_zones(find_momentum_zones(rows), qualifying_detections)
    matched_detection_ids = {zone.get("matched_finding_id") for zone in momentum_zones if zone.get("caught")}
    matched_detections = [item for item in qualifying_detections if item.get("id") in matched_detection_ids]
    leads = sorted(float(zone["lead_seconds"]) for zone in momentum_zones if zone.get("lead_seconds") is not None)
    missed_reasons: list[dict] = []
    rust_trace = rust_evaluation_trace
    for zone in momentum_zones:
        if zone.get("caught"):
            continue
        prior = [item for item in rust_trace if float(zone["onset_at"]) - 120 <= float(item["detected_at"]) <= float(zone["peak_at"])]
        nearest = max(prior, key=lambda item: (int(item.get("recipe_score") or 0), float(item["detected_at"])), default=None)
        missed_reasons.append({
            "onset_at": zone["onset_at"], "expansion_pct": zone["expansion_pct"],
            "best_recipe_score": (nearest or {}).get("recipe_score"),
            "blockers": ((nearest or {}).get("candidate_profile") or {}).get("multi_timeframe", {}).get("blockers", []),
            "missing_recipe": (nearest or {}).get("recipe_missing", []),
        })
    metrics["objective_zone_metrics"] = {
        "zones": len(momentum_zones), "caught": sum(bool(zone.get("caught")) for zone in momentum_zones),
        "recall_pct": round(sum(bool(zone.get("caught")) for zone in momentum_zones) / len(momentum_zones) * 100, 1) if momentum_zones else None,
        "qualifying_detections": len(qualifying_detections), "matched_detections": len(matched_detections),
        "precision_pct": round(len(matched_detections) / len(qualifying_detections) * 100, 1) if qualifying_detections else None,
        "median_lead_seconds": leads[len(leads) // 2] if leads else None,
        "missed_reasons": missed_reasons,
    }
    profile_source = finding or max(
        detections_for_chart, key=lambda item: float(item.get("detected_at") or 0), default=None,
    )
    unified = (profile_source or {}).get("candidate_profile") or {}
    metrics["unified_evidence"] = {
        "supply": unified.get("supply"), "lifecycle": unified.get("lifecycle"),
        "compression_quality": unified.get("compression_quality"),
        "phase": unified.get("phase"), "box": unified.get("box"),
        "pullback": unified.get("pullback"),
        "advisory_only": True,
    }
    if finding:
        trace = store.finding_pipeline_trace(int(finding["id"]))
        stages = {str(item["stage"]): float(item["event_at"]) for item in trace}
        start = stages.get("source_received", center)
        displayed = stages.get("client_displayed")
        submitted = stages.get("paper_order_submitted")
        metrics["notification_latency_ms"] = round((displayed - start) * 1000.0, 2) if displayed else None
        metrics["order_latency_ms"] = round((submitted - start) * 1000.0, 2) if submitted else None
    with RENDER_LOCK:
        chart_path = _render(ticker, rows, finding, center, timeframe_seconds, metrics, settings.chart_dir,
                             inspection_start, inspection_end,
                             [item for item in candidates if item.get("notification_delivered_at")],
                             detections_for_chart, momentum_zones)
    return store.save_development_evaluation({
        "ticker": ticker, "finding_id": (finding or {}).get("id"), "detection_at": center,
        "timeframe_seconds": timeframe_seconds, "status": "complete", "chart_path": chart_path,
        "metrics": {**metrics, "source": snapshot.get("source"), "bars": len(rows),
                    "inspection_start": inspection_start, "inspection_end": inspection_end,
                    "use_live_detector": use_live_detector, "detector_engine": detector_engine}, "error": None,
    })
