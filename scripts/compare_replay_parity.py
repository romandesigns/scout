from __future__ import annotations

import argparse
import json
import math
import random
from bisect import bisect_left, bisect_right
from collections import defaultdict, deque
from pathlib import Path
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]

RUST_BUCKET_SECONDS = 15.0
RUST_WARMUP_BUCKETS = 8
RUST_KEEP_BUCKETS = 160
RUST_SESSION_RESET_GAP_SECONDS = 6.0 * 60.0 * 60.0

SIGNIFICANCE_PROFILES = (
    {"name": "plus_2pct_15m", "threshold_pct": 2.0, "horizon_seconds": 15.0 * 60.0},
    {"name": "plus_5pct_15m", "threshold_pct": 5.0, "horizon_seconds": 15.0 * 60.0},
    {"name": "plus_10pct_30m", "threshold_pct": 10.0, "horizon_seconds": 30.0 * 60.0},
    {"name": "plus_20pct_60m", "threshold_pct": 20.0, "horizon_seconds": 60.0 * 60.0},
)


DECISION_EXPECTATIONS = {
    "plus_2pct_15m": {
        "min_rust_recall": 0.25,
        "require_rust_precision_gte_python": True,
    },
    "plus_5pct_15m": {
        "min_objective_moves": 50,
        "min_rust_recall": 0.25,
        "min_recall_multiple_vs_python": 2.0,
        "require_rust_precision_gte_python": True,
        "min_rust_only_successful_discoveries": 10,
        "min_exclusive_discovery_multiple_vs_python": 2.0,
    },
    "plus_10pct_30m": {
        "min_objective_moves": 20,
        "require_rust_recall_gte_python": True,
        "max_rust_lead_ratio_vs_python": 0.75,
    },
    "plus_20pct_60m": {
        "min_objective_moves": 30,
        "preferred_objective_moves": 50,
        "min_sessions": 20,
        "noninferiority_margin": 0.05,
        "alpha": 0.05,
    },
    "mirror_match_rate": 1.0,
}

RUST_RECIPE_CHECKS = (
    "compressed or orderly base",
    "price remains near the base",
    "pressing a nearby trigger",
    "EMA structure is improving",
    "relative volume is waking up",
    "participation is broadening",
    "price or volume is accelerating",
    "path avoids bearish failure",
)


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * quantile) - 1))
    return float(ordered[index])



def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> tuple[float | None, float | None]:
    if total <= 0:
        return None, None
    p = successes / total
    denom = 1.0 + z * z / total
    center = (p + z * z / (2.0 * total)) / denom
    half = (z / denom) * math.sqrt((p * (1.0 - p) / total) + (z * z / (4.0 * total * total)))
    return max(0.0, center - half), min(1.0, center + half)


def exact_mcnemar_two_sided(rust_only: int, python_only: int) -> float | None:
    """Exact two-sided McNemar p-value without large-integer float overflow.

    Under H0, the number of Rust-only wins among discordant pairs is
    Binomial(n=discordant, p=0.5).  Compute the smaller-tail probability in
    log space, then double it for the conventional exact two-sided test.
    """
    discordant = rust_only + python_only
    if discordant <= 0:
        return None

    smaller = min(rust_only, python_only)
    log_two = math.log(2.0)
    log_terms = [
        math.lgamma(discordant + 1)
        - math.lgamma(k + 1)
        - math.lgamma(discordant - k + 1)
        - discordant * log_two
        for k in range(smaller + 1)
    ]

    max_log = max(log_terms)
    log_tail = max_log + math.log(sum(math.exp(value - max_log) for value in log_terms))

    # exp() legitimately underflows to 0.0 only for astronomically small
    # p-values; that is preferable to overflowing 2.0 ** discordant.
    tail = math.exp(log_tail) if log_tail > math.log(float.fromhex("0x0.0000000000001p-1022")) else 0.0
    return min(1.0, 2.0 * tail)


def paired_recall_difference_bootstrap(
    outcomes: list[tuple[int, int]],
    iterations: int = 10000,
    seed: int = 64920,
) -> dict[str, Any]:
    if not outcomes:
        return {"difference": None, "ci95_low": None, "ci95_high": None, "iterations": 0}
    difference = sum(r - p for p, r in outcomes) / len(outcomes)
    rng = random.Random(seed)
    draws: list[float] = []
    n = len(outcomes)
    for _ in range(iterations):
        total = 0
        for _ in range(n):
            python_hit, rust_hit = outcomes[rng.randrange(n)]
            total += rust_hit - python_hit
        draws.append(total / n)
    draws.sort()
    low_idx = max(0, int(math.floor(0.025 * (iterations - 1))))
    high_idx = min(iterations - 1, int(math.ceil(0.975 * (iterations - 1))))
    return {
        "difference": round(difference, 6),
        "ci95_low": round(draws[low_idx], 6),
        "ci95_high": round(draws[high_idx], 6),
        "iterations": iterations,
    }


def paired_lead_bootstrap(
    lead_pairs: list[tuple[float, float]],
    iterations: int = 10000,
    seed: int = 64921,
) -> dict[str, Any]:
    """Bootstrap median Rust-minus-Python lead time among moves caught by both.

    Negative values mean Rust fired earlier and therefore had a larger lead to onset.
    """
    if not lead_pairs:
        return {"paired_moves": 0, "median_rust_minus_python_seconds": None, "ci95_low": None, "ci95_high": None, "iterations": 0}
    deltas = [rust_lead - python_lead for python_lead, rust_lead in lead_pairs]
    observed = median(deltas)
    rng = random.Random(seed)
    draws: list[float] = []
    n = len(deltas)
    for _ in range(iterations):
        sample = [deltas[rng.randrange(n)] for _ in range(n)]
        draws.append(float(median(sample)))
    draws.sort()
    low_idx = max(0, int(math.floor(0.025 * (iterations - 1))))
    high_idx = min(iterations - 1, int(math.ceil(0.975 * (iterations - 1))))
    return {
        "paired_moves": n,
        "median_rust_minus_python_seconds": round(float(observed), 6),
        "ci95_low": round(draws[low_idx], 6),
        "ci95_high": round(draws[high_idx], 6),
        "iterations": iterations,
    }


def market_session_date(ts: float) -> str:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    if ZoneInfo is not None:
        try:
            dt = dt.astimezone(ZoneInfo("America/New_York"))
        except Exception:
            pass
    # Scout trading sessions begin at 20:00 ET. Shifting local Eastern time
    # forward four hours maps 20:00..23:59 to the following session date.
    # If zoneinfo data is unavailable, fall back to UTC calendar dates.
    shifted = dt
    if ZoneInfo is not None:
        try:
            shifted = dt + timedelta(hours=4)
        except Exception:
            pass
    return shifted.date().isoformat()

def group_episodes(items: list[dict[str, Any]], gap_seconds: float) -> list[dict[str, Any]]:
    by_ticker: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        ticker = str(item.get("ticker") or "").upper()
        if ticker and item.get("detected_at") is not None:
            by_ticker[ticker].append(item)

    episodes: list[dict[str, Any]] = []
    for ticker, rows in by_ticker.items():
        rows.sort(key=lambda item: float(item["detected_at"]))
        current: list[dict[str, Any]] = []
        for row in rows:
            detected_at = float(row["detected_at"])
            if current and detected_at - float(current[-1]["detected_at"]) > gap_seconds:
                episodes.append(summarize_episode(ticker, current))
                current = []
            current.append(row)
        if current:
            episodes.append(summarize_episode(ticker, current))
    episodes.sort(key=lambda item: (item["first_detected_at"], item["ticker"]))
    return episodes


def summarize_episode(ticker: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    first_at = float(rows[0]["detected_at"])
    last_at = float(rows[-1]["detected_at"])
    scores = [int(row["recipe_score"]) for row in rows if row.get("recipe_score") is not None]
    present_sets = [set(map(str, row.get("recipe_present") or [])) for row in rows]
    missing_sets = [set(map(str, row.get("recipe_missing") or [])) for row in rows]
    present_any = sorted(set().union(*present_sets)) if present_sets else []
    present_all = sorted(set.intersection(*present_sets)) if present_sets else []
    missing_any = sorted(set().union(*missing_sets)) if missing_sets else []
    trigger_distances = [float(row["trigger_distance_pct"]) for row in rows if row.get("trigger_distance_pct") is not None]
    base_extensions = [float(row["base_extension_pct"]) for row in rows if row.get("base_extension_pct") is not None]
    first = rows[0]
    return {
        "ticker": ticker,
        "first_price": first.get("price") or first.get("detection_price"),
        "first_detected_at": first_at,
        "last_detected_at": last_at,
        "duration_seconds": round(last_at - first_at, 6),
        "detection_count": len(rows),
        "first_recipe_score": first.get("recipe_score"),
        "first_recipe_present": list(first.get("recipe_present") or []),
        "first_recipe_missing": list(first.get("recipe_missing") or []),
        "first_trigger_distance_pct": first.get("trigger_distance_pct"),
        "first_base_extension_pct": first.get("base_extension_pct"),
        "max_recipe_score": max(scores) if scores else None,
        "min_recipe_score": min(scores) if scores else None,
        "recipe_present_any": present_any,
        "recipe_present_all": present_all,
        "recipe_missing_any": missing_any,
        "min_trigger_distance_pct": min(trigger_distances) if trigger_distances else None,
        "max_trigger_distance_pct": max(trigger_distances) if trigger_distances else None,
        "min_base_extension_pct": min(base_extensions) if base_extensions else None,
        "max_base_extension_pct": max(base_extensions) if base_extensions else None,
    }


def objective_expansions(dataset: Path, expansion_pct: float, base_window_seconds: float, dedupe_seconds: float) -> list[dict[str, Any]]:
    rolling: dict[str, deque[tuple[float, float]]] = defaultdict(deque)
    last_episode: dict[str, float] = defaultdict(lambda: float("-inf"))
    episodes: list[dict[str, Any]] = []
    with dataset.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
                if str(value.get("event_type", "")).lower() != "trade":
                    continue
                ticker = str(value.get("symbol") or "").upper()
                ts = float(value.get("source_ts", 0))
                price = float((value.get("payload") or {}).get("price", 0))
            except Exception as exc:
                raise ValueError(f"unable to parse calibration dataset line {line_number}: {exc}") from exc
            if not ticker or ts <= 0 or price <= 0:
                continue
            window = rolling[ticker]
            window.append((ts, price))
            cutoff = ts - base_window_seconds
            while window and window[0][0] < cutoff:
                window.popleft()
            base = min((value for _, value in window), default=price)
            if base and price >= base * (1 + expansion_pct / 100.0) and ts - last_episode[ticker] >= dedupe_seconds:
                episodes.append({"ticker": ticker, "onset_at": ts, "base_price": base, "onset_price": price})
                last_episode[ticker] = ts
    return episodes


def load_trade_tape(dataset: Path) -> dict[str, list[tuple[float, float]]]:
    """Load the canonical replay trade tape once for outcome/significance analysis."""
    by_ticker: dict[str, list[tuple[float, float]]] = defaultdict(list)
    with dataset.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
                if str(value.get("event_type", "")).lower() != "trade":
                    continue
                ticker = str(value.get("symbol") or "").upper()
                ts = float(value.get("source_ts", 0))
                price = float((value.get("payload") or {}).get("price", 0))
            except Exception as exc:
                raise ValueError(f"unable to parse significance dataset line {line_number}: {exc}") from exc
            if ticker and ts > 0 and price > 0:
                by_ticker[ticker].append((ts, price))
    for rows in by_ticker.values():
        rows.sort(key=lambda item: item[0])
    return dict(by_ticker)


def _profile_key(profile: dict[str, Any]) -> str:
    return str(profile["name"])


def attach_significance_outcomes(
    episodes: list[dict[str, Any]],
    trade_tape: dict[str, list[tuple[float, float]]],
    profiles: tuple[dict[str, Any], ...] = SIGNIFICANCE_PROFILES,
) -> None:
    """Attach forward excursion and threshold timing to each detector episode."""
    max_horizon = max(float(profile["horizon_seconds"]) for profile in profiles)
    for episode in episodes:
        ticker = str(episode["ticker"]).upper()
        rows = trade_tape.get(ticker, [])
        detected_at = float(episode["first_detected_at"])
        timestamps = [row[0] for row in rows]
        start = bisect_left(timestamps, detected_at)
        end = bisect_right(timestamps, detected_at + max_horizon)
        detection_price = episode.get("first_price")
        if detection_price is None and start < len(rows):
            detection_price = rows[start][1]
        if detection_price is None or float(detection_price) <= 0:
            episode["significance"] = {
                "available": False,
                "reason": "no_detection_price",
            }
            continue

        detection_price = float(detection_price)
        profile_state = {
            _profile_key(profile): {
                "threshold_pct": float(profile["threshold_pct"]),
                "horizon_seconds": float(profile["horizon_seconds"]),
                "reached": False,
                "reached_at": None,
                "lead_seconds": None,
                "max_favorable_pct": 0.0,
            }
            for profile in profiles
        }
        max_price = detection_price
        max_price_at = detected_at
        for ts, price in rows[start:end]:
            if price > max_price:
                max_price = price
                max_price_at = ts
            favorable_pct = rust_pct(detection_price, price)
            elapsed = ts - detected_at
            for profile in profiles:
                key = _profile_key(profile)
                state = profile_state[key]
                if elapsed <= float(profile["horizon_seconds"]):
                    if favorable_pct > float(state["max_favorable_pct"]):
                        state["max_favorable_pct"] = round(favorable_pct, 6)
                    if not state["reached"] and favorable_pct >= float(profile["threshold_pct"]):
                        state["reached"] = True
                        state["reached_at"] = ts
                        state["lead_seconds"] = round(elapsed, 6)

        episode["significance"] = {
            "available": True,
            "detection_price": detection_price,
            "max_horizon_seconds": max_horizon,
            "max_favorable_pct": round(rust_pct(detection_price, max_price), 6),
            "max_favorable_at": max_price_at,
            "profiles": profile_state,
        }


def build_objective_moves(
    trade_tape: dict[str, list[tuple[float, float]]],
    threshold_pct: float,
    base_window_seconds: float,
    dedupe_seconds: float,
) -> list[dict[str, Any]]:
    """Find objective expansion onsets from a rolling local low, independent of either engine."""
    moves: list[dict[str, Any]] = []
    for ticker, rows in trade_tape.items():
        rolling: deque[tuple[float, float]] = deque()
        last_onset = float("-inf")
        for ts, price in rows:
            rolling.append((ts, price))
            cutoff = ts - base_window_seconds
            while rolling and rolling[0][0] < cutoff:
                rolling.popleft()
            base_price = min((item[1] for item in rolling), default=price)
            if (
                base_price > 0
                and price >= base_price * (1.0 + threshold_pct / 100.0)
                and ts - last_onset >= dedupe_seconds
            ):
                moves.append(
                    {
                        "ticker": ticker,
                        "onset_at": ts,
                        "base_price": base_price,
                        "onset_price": price,
                        "threshold_pct": threshold_pct,
                        "session_date": market_session_date(ts),
                    }
                )
                last_onset = ts
    moves.sort(key=lambda item: (float(item["onset_at"]), item["ticker"]))
    return moves


def _episode_times_by_ticker(episodes: list[dict[str, Any]]) -> dict[str, list[float]]:
    result: dict[str, list[float]] = defaultdict(list)
    for episode in episodes:
        result[str(episode["ticker"]).upper()].append(float(episode["first_detected_at"]))
    for rows in result.values():
        rows.sort()
    return dict(result)


def _earliest_precursor(
    times: list[float],
    onset_at: float,
    lookback_seconds: float,
) -> float | None:
    left = bisect_left(times, onset_at - lookback_seconds)
    right = bisect_right(times, onset_at)
    if left >= right:
        return None
    return times[left]


def evaluate_objective_move_coverage(
    moves: list[dict[str, Any]],
    python_episodes: list[dict[str, Any]],
    rust_episodes: list[dict[str, Any]],
    lookback_seconds: float,
    simultaneous_seconds: float,
) -> dict[str, Any]:
    python_times = _episode_times_by_ticker(python_episodes)
    rust_times = _episode_times_by_ticker(rust_episodes)
    classifications = {"both": 0, "rust_only": 0, "python_only": 0, "neither": 0}
    timing = {"rust_earlier": 0, "python_earlier": 0, "near_simultaneous": 0}
    python_leads: list[float] = []
    rust_leads: list[float] = []
    paired_leads: list[tuple[float, float]] = []
    paired_outcomes: list[tuple[int, int]] = []
    session_rows: dict[str, dict[str, int]] = defaultdict(lambda: {"objective_moves": 0, "both": 0, "rust_only": 0, "python_only": 0, "neither": 0})
    examples: list[dict[str, Any]] = []

    for move in moves:
        ticker = str(move["ticker"]).upper()
        onset_at = float(move["onset_at"])
        session_date = str(move.get("session_date") or market_session_date(onset_at))
        python_at = _earliest_precursor(python_times.get(ticker, []), onset_at, lookback_seconds)
        rust_at = _earliest_precursor(rust_times.get(ticker, []), onset_at, lookback_seconds)
        python_hit = int(python_at is not None)
        rust_hit = int(rust_at is not None)
        paired_outcomes.append((python_hit, rust_hit))
        python_lead = None if python_at is None else onset_at - python_at
        rust_lead = None if rust_at is None else onset_at - rust_at
        if python_lead is not None:
            python_leads.append(python_lead)
        if rust_lead is not None:
            rust_leads.append(rust_lead)

        if python_at is not None and rust_at is not None:
            classification = "both"
            paired_leads.append((python_lead, rust_lead))
            delta = rust_at - python_at
            if abs(delta) <= simultaneous_seconds:
                timing["near_simultaneous"] += 1
            elif delta < 0:
                timing["rust_earlier"] += 1
            else:
                timing["python_earlier"] += 1
        elif rust_at is not None:
            classification = "rust_only"
        elif python_at is not None:
            classification = "python_only"
        else:
            classification = "neither"
        classifications[classification] += 1
        session_rows[session_date]["objective_moves"] += 1
        session_rows[session_date][classification] += 1

        if classification != "neither":
            examples.append(
                {
                    "ticker": ticker,
                    "onset_at": onset_at,
                    "session_date": session_date,
                    "base_price": move.get("base_price"),
                    "onset_price": move.get("onset_price"),
                    "classification": classification,
                    "python_detected_at": python_at,
                    "rust_detected_at": rust_at,
                    "python_lead_seconds": None if python_lead is None else round(python_lead, 6),
                    "rust_lead_seconds": None if rust_lead is None else round(rust_lead, 6),
                }
            )

    total = len(moves)
    python_caught = classifications["both"] + classifications["python_only"]
    rust_caught = classifications["both"] + classifications["rust_only"]
    python_ci = wilson_interval(python_caught, total)
    rust_ci = wilson_interval(rust_caught, total)
    recall_bootstrap = paired_recall_difference_bootstrap(paired_outcomes)
    mcnemar_p = exact_mcnemar_two_sided(classifications["rust_only"], classifications["python_only"])
    lead_bootstrap = paired_lead_bootstrap(paired_leads)
    examples.sort(
        key=lambda item: (
            item["classification"] == "rust_only",
            float(item.get("rust_lead_seconds") or 0.0),
            float(item.get("python_lead_seconds") or 0.0),
        ),
        reverse=True,
    )
    return {
        "objective_moves": total,
        "sessions_with_objective_moves": len(session_rows),
        "python": {
            "caught": python_caught,
            "recall": round(python_caught / total, 6) if total else None,
            "recall_ci95": [None if python_ci[0] is None else round(python_ci[0], 6), None if python_ci[1] is None else round(python_ci[1], 6)],
            "lead_seconds_median": median(python_leads) if python_leads else None,
            "lead_seconds_p95": percentile(python_leads, 0.95),
        },
        "rust": {
            "caught": rust_caught,
            "recall": round(rust_caught / total, 6) if total else None,
            "recall_ci95": [None if rust_ci[0] is None else round(rust_ci[0], 6), None if rust_ci[1] is None else round(rust_ci[1], 6)],
            "lead_seconds_median": median(rust_leads) if rust_leads else None,
            "lead_seconds_p95": percentile(rust_leads, 0.95),
        },
        "classification": classifications,
        "timing_when_both": timing,
        "paired_statistics": {
            "mcnemar_exact_two_sided_p": None if mcnemar_p is None else round(mcnemar_p, 8),
            "rust_minus_python_recall": recall_bootstrap,
            "rust_minus_python_lead_seconds": lead_bootstrap,
            "interpretation": "For recall difference, positive favors Rust. For Rust-minus-Python lead seconds among moves caught by both, negative means Rust detected earlier.",
        },
        "session_breakdown": [
            {"session_date": session_date, **values}
            for session_date, values in sorted(session_rows.items())
        ],
        "examples": examples[:25],
    }


def detector_significance_stats(
    episodes: list[dict[str, Any]],
    profile_name: str,
) -> dict[str, Any]:
    usable = [
        episode
        for episode in episodes
        if (episode.get("significance") or {}).get("available")
        and profile_name in ((episode.get("significance") or {}).get("profiles") or {})
    ]
    successful = [
        episode
        for episode in usable
        if episode["significance"]["profiles"][profile_name]["reached"]
    ]
    leads = [
        float(episode["significance"]["profiles"][profile_name]["lead_seconds"])
        for episode in successful
        if episode["significance"]["profiles"][profile_name]["lead_seconds"] is not None
    ]
    max_moves = [
        float(episode["significance"]["profiles"][profile_name]["max_favorable_pct"])
        for episode in usable
    ]
    return {
        "episodes": len(usable),
        "successful": len(successful),
        "false_arms": len(usable) - len(successful),
        "success_rate": round(len(successful) / len(usable), 6) if usable else None,
        "lead_seconds_median": median(leads) if leads else None,
        "lead_seconds_p95": percentile(leads, 0.95),
        "max_favorable_pct_median": median(max_moves) if max_moves else None,
    }


def _successful_discoveries(
    episodes: list[dict[str, Any]],
    profile_name: str,
    limit: int = 25,
) -> list[dict[str, Any]]:
    rows = []
    for episode in episodes:
        significance = episode.get("significance") or {}
        profile = (significance.get("profiles") or {}).get(profile_name) or {}
        if not profile.get("reached"):
            continue
        rows.append(
            {
                "ticker": episode["ticker"],
                "first_detected_at": episode["first_detected_at"],
                "detection_price": significance.get("detection_price"),
                "recipe_score": episode.get("first_recipe_score"),
                "lead_seconds": profile.get("lead_seconds"),
                "max_favorable_pct": profile.get("max_favorable_pct"),
                "max_favorable_pct_60m": significance.get("max_favorable_pct"),
            }
        )
    rows.sort(
        key=lambda item: (
            float(item.get("max_favorable_pct_60m") or 0.0),
            float(item.get("lead_seconds") or 0.0),
        ),
        reverse=True,
    )
    return rows[:limit]


def build_event_significance_report(
    trade_tape: dict[str, list[tuple[float, float]]],
    python_episodes: list[dict[str, Any]],
    rust_episodes: list[dict[str, Any]],
    python_only_episodes: list[dict[str, Any]],
    rust_only_episodes: list[dict[str, Any]],
    base_window_seconds: float,
    simultaneous_seconds: float,
    profiles: tuple[dict[str, Any], ...] = SIGNIFICANCE_PROFILES,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "available": True,
        "methodology": {
            "objective_move_definition": "Rolling local-low expansion independent of either detector.",
            "detector_success_definition": "Price reaches the profile threshold from that episode's first detection price within the profile horizon.",
            "coverage_definition": "An objective move is caught when an episode begins before onset and within that profile's horizon.",
            "base_window_seconds": base_window_seconds,
        },
        "profiles": {},
    }

    for profile in profiles:
        name = _profile_key(profile)
        threshold_pct = float(profile["threshold_pct"])
        horizon_seconds = float(profile["horizon_seconds"])
        objective_moves = build_objective_moves(
            trade_tape,
            threshold_pct=threshold_pct,
            base_window_seconds=base_window_seconds,
            dedupe_seconds=horizon_seconds,
        )
        coverage = evaluate_objective_move_coverage(
            objective_moves,
            python_episodes,
            rust_episodes,
            lookback_seconds=horizon_seconds,
            simultaneous_seconds=simultaneous_seconds,
        )
        report["profiles"][name] = {
            "definition": {
                "threshold_pct": threshold_pct,
                "horizon_seconds": horizon_seconds,
            },
            "detector_episode_precision": {
                "python": detector_significance_stats(python_episodes, name),
                "rust": detector_significance_stats(rust_episodes, name),
            },
            "objective_move_coverage": coverage,
            "rust_only_successful_discoveries": {
                "count": sum(
                    1
                    for episode in rust_only_episodes
                    if (((episode.get("significance") or {}).get("profiles") or {}).get(name) or {}).get("reached")
                ),
                "examples": _successful_discoveries(rust_only_episodes, name),
            },
            "python_only_successful_discoveries": {
                "count": sum(
                    1
                    for episode in python_only_episodes
                    if (((episode.get("significance") or {}).get("profiles") or {}).get(name) or {}).get("reached")
                ),
                "examples": _successful_discoveries(python_only_episodes, name),
            },
        }
    return report





# v6.4.10: outcome-driven bullish-event taxonomy and capture-efficiency analysis.
#
# This layer does NOT alter either detector. It treats bullish scenarios as
# evaluation strata rather than hard-coded signal templates so the engines can
# still discover structures that were not manually enumerated.
BULLISH_TAXONOMY_BASE_THRESHOLD_PCT = 5.0
BULLISH_TAXONOMY_HORIZON_SECONDS = 60.0 * 60.0
BULLISH_TAXONOMY_DEDUPE_SECONDS = 15.0 * 60.0
BULLISH_TAXONOMY_SAMPLE_SECONDS = 60.0


def _session_phase(ts: float) -> str:
    if ZoneInfo is None:
        return "unknown"
    local = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(ZoneInfo("America/New_York"))
    minute = local.hour * 60 + local.minute
    if minute < 4 * 60:
        return "overnight"
    if minute < 9 * 60 + 30:
        return "premarket"
    if minute < 10 * 60:
        return "opening_drive"
    if minute < 15 * 60 + 30:
        return "regular_session"
    if minute < 16 * 60:
        return "power_hour"
    if minute < 20 * 60:
        return "after_hours"
    return "overnight"


def _last_price_per_bucket(
    rows: list[tuple[float, float]],
    start_at: float,
    end_at: float,
    bucket_seconds: float = BULLISH_TAXONOMY_SAMPLE_SECONDS,
) -> list[tuple[float, float]]:
    if not rows or end_at < start_at:
        return []
    times = [item[0] for item in rows]
    left = bisect_left(times, start_at)
    right = bisect_right(times, end_at)
    buckets: dict[int, tuple[float, float]] = {}
    for ts, price in rows[left:right]:
        bucket = int((ts - start_at) // bucket_seconds)
        buckets[bucket] = (ts, price)
    return [buckets[key] for key in sorted(buckets)]


def _objective_move_morphology(
    move: dict[str, Any],
    trade_tape: dict[str, list[tuple[float, float]]],
    pre_window_seconds: float,
    horizon_seconds: float,
) -> dict[str, Any]:
    ticker = str(move["ticker"]).upper()
    onset_at = float(move["onset_at"])
    base_price = float(move["base_price"])
    rows = trade_tape.get(ticker, [])
    if not rows or base_price <= 0:
        return {"available": False, "reason": "no_trade_path"}

    times = [item[0] for item in rows]
    prior_left = bisect_left(times, onset_at - pre_window_seconds)
    onset_idx = bisect_left(times, onset_at)
    future_right = bisect_right(times, onset_at + horizon_seconds)
    prior_rows = rows[prior_left:onset_idx + 1]
    future_rows = rows[onset_idx:future_right]
    if not future_rows:
        return {"available": False, "reason": "no_forward_path"}

    peak_at, peak_price = max(future_rows, key=lambda item: item[1])
    peak_from_base_pct = rust_pct(base_price, peak_price)
    time_to_peak_seconds = max(0.0, peak_at - onset_at)

    if prior_rows:
        prior_prices = [item[1] for item in prior_rows]
        pre_range_pct = rust_pct(min(prior_prices), max(prior_prices)) if min(prior_prices) > 0 else None
    else:
        pre_range_pct = None

    sampled = _last_price_per_bucket(rows, onset_at, peak_at)
    if not sampled:
        sampled = [(onset_at, float(move["onset_price"])), (peak_at, peak_price)]
    elif sampled[0][0] > onset_at:
        sampled.insert(0, (onset_at, float(move["onset_price"])))

    prices = [item[1] for item in sampled]
    gross_path = sum(abs(b - a) for a, b in zip(prices, prices[1:]))
    net_path = max(0.0, peak_price - prices[0])
    path_efficiency = (net_path / gross_path) if gross_path > 0 else 1.0

    running_high = prices[0]
    max_drawdown_before_peak_pct = 0.0
    drawdown_seen = False
    for price in prices[1:]:
        running_high = max(running_high, price)
        if running_high > 0:
            drawdown = max(0.0, (running_high - price) / running_high * 100.0)
            if drawdown > max_drawdown_before_peak_pct:
                max_drawdown_before_peak_pct = drawdown
            if drawdown >= 3.0:
                drawdown_seen = True

    five_min_end = bisect_right(times, onset_at + 300.0)
    five_min_rows = rows[onset_idx:five_min_end]
    five_min_peak = max((price for _, price in five_min_rows), default=float(move["onset_price"]))
    first_5m_gain_pct = rust_pct(float(move["onset_price"]), five_min_peak)

    magnitude_bin = (
        "100pct_plus"
        if peak_from_base_pct >= 100.0
        else "50_to_100pct"
        if peak_from_base_pct >= 50.0
        else "20_to_50pct"
        if peak_from_base_pct >= 20.0
        else "10_to_20pct"
        if peak_from_base_pct >= 10.0
        else "5_to_10pct"
    )

    morphology_flags: list[str] = []
    if pre_range_pct is not None and pre_range_pct <= 3.0:
        morphology_flags.append("compressed_breakout")
    if time_to_peak_seconds <= 15.0 * 60.0 and peak_from_base_pct >= 10.0 and path_efficiency >= 0.55:
        morphology_flags.append("explosive_acceleration")
    if (
        time_to_peak_seconds > 15.0 * 60.0
        and peak_from_base_pct >= 10.0
        and path_efficiency >= 0.45
        and max_drawdown_before_peak_pct < 5.0
    ):
        morphology_flags.append("orderly_trend")
    if peak_from_base_pct >= 10.0 and drawdown_seen and peak_at > onset_at:
        morphology_flags.append("pullback_continuation")
    if peak_from_base_pct >= 20.0 and time_to_peak_seconds > 15.0 * 60.0 and max_drawdown_before_peak_pct >= 3.0:
        morphology_flags.append("multi_leg_runner")
    if first_5m_gain_pct >= 5.0:
        morphology_flags.append("fast_repricing")
    if peak_from_base_pct >= 50.0:
        morphology_flags.append("extreme_runner")
    if peak_from_base_pct >= 100.0:
        morphology_flags.append("triple_digit_runner")
    if not morphology_flags:
        morphology_flags.append("other_bullish_expansion")

    return {
        "available": True,
        "peak_price": round(peak_price, 8),
        "peak_at": peak_at,
        "peak_from_base_pct": round(peak_from_base_pct, 6),
        "time_to_peak_seconds": round(time_to_peak_seconds, 6),
        "pre_range_pct": None if pre_range_pct is None else round(pre_range_pct, 6),
        "path_efficiency": round(path_efficiency, 6),
        "max_drawdown_before_peak_pct": round(max_drawdown_before_peak_pct, 6),
        "first_5m_gain_pct": round(first_5m_gain_pct, 6),
        "magnitude_bin": magnitude_bin,
        "session_phase": _session_phase(onset_at),
        "morphology_flags": morphology_flags,
    }


def _episode_records_by_ticker(episodes: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for episode in episodes:
        result[str(episode["ticker"]).upper()].append(episode)
    for ticker in result:
        result[ticker].sort(key=lambda item: float(item["first_detected_at"]))
    return dict(result)


def _earliest_episode_record(
    episodes: list[dict[str, Any]],
    onset_at: float,
    lookback_seconds: float,
) -> dict[str, Any] | None:
    candidates = [
        episode
        for episode in episodes
        if onset_at - lookback_seconds <= float(episode["first_detected_at"]) <= onset_at
    ]
    return candidates[0] if candidates else None


def _capture_metrics(
    move: dict[str, Any],
    morphology: dict[str, Any],
    episode: dict[str, Any] | None,
) -> dict[str, Any]:
    if not episode or not morphology.get("available"):
        return {
            "caught": False,
            "detected_at": None,
            "detection_price": None,
            "lead_seconds": None,
            "remaining_move_pct": None,
            "capture_efficiency": None,
            "capture_efficiency_clipped": None,
        }

    detected_at = float(episode["first_detected_at"])
    detection_price = episode.get("first_price")
    if detection_price is None:
        significance = episode.get("significance") or {}
        detection_price = significance.get("detection_price")
    if detection_price is None or float(detection_price) <= 0:
        return {
            "caught": True,
            "detected_at": detected_at,
            "detection_price": None,
            "lead_seconds": float(move["onset_at"]) - detected_at,
            "remaining_move_pct": None,
            "capture_efficiency": None,
            "capture_efficiency_clipped": None,
        }

    detection_price = float(detection_price)
    base_price = float(move["base_price"])
    peak_price = float(morphology["peak_price"])
    denominator = peak_price - base_price
    capture = ((peak_price - detection_price) / denominator) if denominator > 0 else None
    return {
        "caught": True,
        "detected_at": detected_at,
        "detection_price": round(detection_price, 8),
        "lead_seconds": round(float(move["onset_at"]) - detected_at, 6),
        "remaining_move_pct": round(rust_pct(detection_price, peak_price), 6),
        "capture_efficiency": None if capture is None else round(capture, 6),
        "capture_efficiency_clipped": None if capture is None else round(max(0.0, min(1.0, capture)), 6),
    }


def _aggregate_taxonomy_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)

    def engine_stats(engine: str) -> dict[str, Any]:
        captures = [row[engine] for row in rows if row[engine]["caught"]]
        clipped = [
            float(item["capture_efficiency_clipped"])
            for item in captures
            if item.get("capture_efficiency_clipped") is not None
        ]
        remaining = [
            float(item["remaining_move_pct"])
            for item in captures
            if item.get("remaining_move_pct") is not None
        ]
        leads = [
            float(item["lead_seconds"])
            for item in captures
            if item.get("lead_seconds") is not None
        ]
        return {
            "caught": len(captures),
            "recall": round(len(captures) / total, 6) if total else None,
            "lead_seconds_median": median(leads) if leads else None,
            "capture_efficiency_median": median(clipped) if clipped else None,
            "remaining_move_pct_median": median(remaining) if remaining else None,
        }

    python_caught = sum(1 for row in rows if row["python"]["caught"])
    rust_caught = sum(1 for row in rows if row["rust"]["caught"])
    both = sum(1 for row in rows if row["python"]["caught"] and row["rust"]["caught"])
    return {
        "events": total,
        "python": engine_stats("python"),
        "rust": engine_stats("rust"),
        "classification": {
            "both": both,
            "rust_only": rust_caught - both,
            "python_only": python_caught - both,
            "neither": total - (rust_caught + python_caught - both),
        },
    }


def build_bullish_event_evaluation(
    trade_tape: dict[str, list[tuple[float, float]]],
    python_episodes: list[dict[str, Any]],
    rust_episodes: list[dict[str, Any]],
    base_window_seconds: float,
) -> dict[str, Any]:
    """Evaluate broad bullish outcomes without teaching either detector fixed patterns."""
    objective_moves = build_objective_moves(
        trade_tape,
        threshold_pct=BULLISH_TAXONOMY_BASE_THRESHOLD_PCT,
        base_window_seconds=base_window_seconds,
        dedupe_seconds=BULLISH_TAXONOMY_DEDUPE_SECONDS,
    )
    python_by_ticker = _episode_records_by_ticker(python_episodes)
    rust_by_ticker = _episode_records_by_ticker(rust_episodes)
    rows: list[dict[str, Any]] = []

    for move in objective_moves:
        morphology = _objective_move_morphology(
            move,
            trade_tape,
            pre_window_seconds=base_window_seconds,
            horizon_seconds=BULLISH_TAXONOMY_HORIZON_SECONDS,
        )
        if not morphology.get("available"):
            continue
        ticker = str(move["ticker"]).upper()
        onset_at = float(move["onset_at"])
        python_episode = _earliest_episode_record(
            python_by_ticker.get(ticker, []),
            onset_at,
            BULLISH_TAXONOMY_HORIZON_SECONDS,
        )
        rust_episode = _earliest_episode_record(
            rust_by_ticker.get(ticker, []),
            onset_at,
            BULLISH_TAXONOMY_HORIZON_SECONDS,
        )
        rows.append(
            {
                "ticker": ticker,
                "session_date": move.get("session_date"),
                "onset_at": onset_at,
                "base_price": move.get("base_price"),
                "onset_price": move.get("onset_price"),
                **morphology,
                "python": _capture_metrics(move, morphology, python_episode),
                "rust": _capture_metrics(move, morphology, rust_episode),
            }
        )

    magnitude: dict[str, list[dict[str, Any]]] = defaultdict(list)
    morphology_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    phase: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        magnitude[str(row["magnitude_bin"])].append(row)
        phase[str(row["session_phase"])].append(row)
        for flag in row.get("morphology_flags") or []:
            morphology_groups[str(flag)].append(row)

    # Keep the biggest misses/wins visible without flooding terminal output.
    examples = sorted(
        rows,
        key=lambda row: (
            float(row.get("peak_from_base_pct") or 0.0),
            1 if row["rust"]["caught"] and not row["python"]["caught"] else 0,
        ),
        reverse=True,
    )[:40]

    return {
        "available": True,
        "methodology": {
            "base_event": f"+{BULLISH_TAXONOMY_BASE_THRESHOLD_PCT:g}% objective expansion from rolling local base",
            "forward_horizon_seconds": BULLISH_TAXONOMY_HORIZON_SECONDS,
            "dedupe_seconds": BULLISH_TAXONOMY_DEDUPE_SECONDS,
            "taxonomy_is_evaluation_only": True,
            "detectors_modified": False,
            "capture_efficiency_definition": "(forward peak - detection price) / (forward peak - pre-expansion base), clipped to [0,1] for aggregate medians",
            "principle": "Reward early recognition across diverse bullish structures without forcing either engine into a fixed pattern catalog.",
        },
        "overall": _aggregate_taxonomy_rows(rows),
        "by_magnitude": {
            key: _aggregate_taxonomy_rows(value)
            for key, value in sorted(magnitude.items())
        },
        "by_morphology": {
            key: _aggregate_taxonomy_rows(value)
            for key, value in sorted(morphology_groups.items())
        },
        "by_session_phase": {
            key: _aggregate_taxonomy_rows(value)
            for key, value in sorted(phase.items())
        },
        "examples": examples,
    }



# v6.4.11: deterministic early-detection / capture-quality gate.
#
# This is an evaluation gate only. It does not alter Python or Rust detector
# thresholds, state transitions, scoring, or emissions.
EARLY_QUALITY_EXPECTATIONS = {
    "overall": {
        "min_events": 500,
        "min_rust_recall": 0.45,
        "min_capture_efficiency": 0.75,
        "min_rust_vs_python_recall_multiple": 1.75,
    },
    "5_to_10pct": {
        "min_events": 100,
        "min_rust_recall": 0.45,
        "min_capture_efficiency": 0.65,
    },
    "10_to_20pct": {
        "min_events": 100,
        "min_rust_recall": 0.40,
        "min_capture_efficiency": 0.75,
    },
    "20_to_50pct": {
        "min_events": 50,
        "min_rust_recall": 0.50,
        "min_capture_efficiency": 0.85,
    },
    "50_to_100pct": {
        "min_events": 20,
        "min_rust_recall": 0.60,
        "min_capture_efficiency": 0.85,
    },
    "morphology": {
        "explosive_acceleration": {"min_events": 50, "min_rust_recall": 0.45},
        "multi_leg_runner": {"min_events": 30, "min_rust_recall": 0.55},
        "pullback_continuation": {"min_events": 50, "min_rust_recall": 0.45},
        "orderly_trend": {"min_events": 20, "min_rust_recall": 0.45},
    },
    "session_phase": {
        "opening_drive": {"min_events": 50, "min_rust_recall": 0.30},
        "regular_session": {"min_events": 100, "min_rust_recall": 0.50},
        "power_hour": {"min_events": 30, "min_rust_recall": 0.50},
        "premarket": {"min_events": 50, "min_rust_recall": 0.20},
        "after_hours": {"min_events": 100, "min_rust_recall": 0.40},
    },
}


def _quality_gate_result(
    *,
    actual: float | int | None,
    required: float | int,
    comparator: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if actual is None:
        passed = False
    elif comparator == ">=":
        passed = float(actual) >= float(required)
    elif comparator == "<=":
        passed = float(actual) <= float(required)
    else:
        raise ValueError(f"Unsupported comparator {comparator!r}")
    return {
        "pass": passed,
        "actual": actual,
        "required": required,
        "comparator": comparator,
        **(context or {}),
    }


def build_early_detection_quality_gate(
    event_significance: dict[str, Any],
    bullish_event_evaluation: dict[str, Any],
) -> dict[str, Any]:
    """Judge whether Rust is early/actionable enough for hybrid integration.

    PASS means the frozen Rust detector demonstrates broad objective-move
    coverage and retains enough of the move at first detection to justify
    advancing to the hybrid-intelligence integration phase.  This gate is not
    a trading-profit guarantee and does not attempt to optimize exits.
    """
    if not bullish_event_evaluation.get("available"):
        return {
            "status": "INCONCLUSIVE",
            "recommendation": "BULLISH_EVENT_EVALUATION_REQUIRED",
            "detectors_modified": False,
            "gates": {},
        }

    overall = bullish_event_evaluation.get("overall") or {}
    by_magnitude = bullish_event_evaluation.get("by_magnitude") or {}
    by_morphology = bullish_event_evaluation.get("by_morphology") or {}
    by_phase = bullish_event_evaluation.get("by_session_phase") or {}

    gates: dict[str, dict[str, Any]] = {}

    overall_rust = overall.get("rust") or {}
    overall_python = overall.get("python") or {}
    overall_cfg = EARLY_QUALITY_EXPECTATIONS["overall"]
    rust_recall = overall_rust.get("recall")
    python_recall = overall_python.get("recall")
    recall_multiple = _safe_ratio(rust_recall, python_recall)

    gates["overall_sample"] = _quality_gate_result(
        actual=overall.get("events"),
        required=overall_cfg["min_events"],
        comparator=">=",
    )
    gates["overall_rust_recall"] = _quality_gate_result(
        actual=rust_recall,
        required=overall_cfg["min_rust_recall"],
        comparator=">=",
        context={"python": python_recall},
    )
    gates["overall_capture_efficiency"] = _quality_gate_result(
        actual=overall_rust.get("capture_efficiency_median"),
        required=overall_cfg["min_capture_efficiency"],
        comparator=">=",
        context={"python": overall_python.get("capture_efficiency_median")},
    )
    gates["overall_recall_advantage"] = _quality_gate_result(
        actual=recall_multiple,
        required=overall_cfg["min_rust_vs_python_recall_multiple"],
        comparator=">=",
        context={"rust": rust_recall, "python": python_recall},
    )

    for band in ("5_to_10pct", "10_to_20pct", "20_to_50pct", "50_to_100pct"):
        cfg = EARLY_QUALITY_EXPECTATIONS[band]
        stats = by_magnitude.get(band) or {}
        rust = stats.get("rust") or {}
        python = stats.get("python") or {}
        gates[f"{band}_sample"] = _quality_gate_result(
            actual=stats.get("events"),
            required=cfg["min_events"],
            comparator=">=",
        )
        gates[f"{band}_rust_recall"] = _quality_gate_result(
            actual=rust.get("recall"),
            required=cfg["min_rust_recall"],
            comparator=">=",
            context={"python": python.get("recall")},
        )
        gates[f"{band}_capture_efficiency"] = _quality_gate_result(
            actual=rust.get("capture_efficiency_median"),
            required=cfg["min_capture_efficiency"],
            comparator=">=",
            context={"python": python.get("capture_efficiency_median")},
        )

    for name, cfg in EARLY_QUALITY_EXPECTATIONS["morphology"].items():
        stats = by_morphology.get(name) or {}
        rust = stats.get("rust") or {}
        events = int(stats.get("events") or 0)
        # A morphology gate is only hard once its sample floor is satisfied.
        sample_pass = events >= int(cfg["min_events"])
        gates[f"morphology_{name}_sample"] = {
            "pass": sample_pass,
            "actual": events,
            "required": int(cfg["min_events"]),
            "comparator": ">=",
            "hard_gate": False,
        }
        if sample_pass:
            gates[f"morphology_{name}_rust_recall"] = _quality_gate_result(
                actual=rust.get("recall"),
                required=cfg["min_rust_recall"],
                comparator=">=",
                context={"python": (stats.get("python") or {}).get("recall"), "hard_gate": True},
            )

    for name, cfg in EARLY_QUALITY_EXPECTATIONS["session_phase"].items():
        stats = by_phase.get(name) or {}
        rust = stats.get("rust") or {}
        events = int(stats.get("events") or 0)
        sample_pass = events >= int(cfg["min_events"])
        gates[f"phase_{name}_sample"] = {
            "pass": sample_pass,
            "actual": events,
            "required": int(cfg["min_events"]),
            "comparator": ">=",
            "hard_gate": False,
        }
        if sample_pass:
            gates[f"phase_{name}_rust_recall"] = _quality_gate_result(
                actual=rust.get("recall"),
                required=cfg["min_rust_recall"],
                comparator=">=",
                context={"python": (stats.get("python") or {}).get("recall"), "hard_gate": True},
            )

    # Extra-noise protection: Rust may expand coverage, but it must not do so
    # with worse objective episode precision than Python at the primary +2/+5
    # validation profiles.
    profiles = event_significance.get("profiles") or {}
    for profile_name in ("plus_2pct_15m", "plus_5pct_15m"):
        profile = profiles.get(profile_name) or {}
        precision = profile.get("detector_episode_precision") or {}
        r = (precision.get("rust") or {}).get("success_rate")
        p = (precision.get("python") or {}).get("success_rate")
        gates[f"{profile_name}_precision_noninferiority"] = {
            "pass": r is not None and p is not None and float(r) >= float(p),
            "rust": r,
            "python": p,
            "required": "rust >= python",
            "hard_gate": True,
        }

    hard_gate_names: list[str] = []
    for name, result in gates.items():
        # Overall/magnitude gates are always hard. Morphology/session sample
        # gates are informational; their recall gates become hard once enough
        # observations exist.
        if name.startswith("morphology_") and name.endswith("_sample"):
            continue
        if name.startswith("phase_") and name.endswith("_sample"):
            continue
        hard_gate_names.append(name)

    failed = [name for name in hard_gate_names if not gates[name].get("pass")]
    passed = [name for name in hard_gate_names if gates[name].get("pass")]

    extreme = by_magnitude.get("100pct_plus") or {}
    extreme_rust = extreme.get("rust") or {}
    extreme_python = extreme.get("python") or {}

    status = "PASS" if not failed else "FAIL"
    recommendation = (
        "ADVANCE_TO_HYBRID_INTELLIGENCE_INTEGRATION"
        if status == "PASS"
        else "HOLD_AND_DIAGNOSE_FAILED_EARLY_QUALITY_GATES"
    )

    return {
        "status": status,
        "recommendation": recommendation,
        "detectors_modified": False,
        "decision_principle": "A detector advances when it finds diverse bullish expansions early enough to preserve meaningful upside, while objective precision remains no worse than the comparison engine.",
        "passed_hard_gates": passed,
        "failed_hard_gates": failed,
        "gates": gates,
        "observational_not_hard_gates": {
            "100pct_plus": {
                "events": extreme.get("events"),
                "rust_recall": extreme_rust.get("recall"),
                "python_recall": extreme_python.get("recall"),
                "rust_capture_efficiency_median": extreme_rust.get("capture_efficiency_median"),
                "python_capture_efficiency_median": extreme_python.get("capture_efficiency_median"),
                "reason": "Rare-event sample retained for monitoring; not allowed to decide the release by itself.",
            },
            "compressed_breakout": {
                "stats": by_morphology.get("compressed_breakout"),
                "reason": "Small taxonomy sample is diagnostic only; do not tune detector logic against it.",
            },
            "overnight": {
                "stats": by_phase.get("overnight"),
                "reason": "Small session sample is diagnostic only; do not tune detector logic against it.",
            },
        },
    }



# v6.4.12: hybrid-intelligence evaluation.
# Evaluation only: detector behavior is unchanged.
HYBRID_EXPECTATIONS = {
    "overall_min_union_recall_gain_vs_rust": 0.03,
    "overall_max_union_precision_drop_vs_rust": 0.35,
    "major_min_union_recall_gain_vs_rust": 0.02,
    "major_min_python_only_wins": 3,
}


def _hybrid_union_from_classification(stats: dict[str, Any]) -> dict[str, Any]:
    cls = stats.get("classification") or {}
    total = int(stats.get("events") or 0)
    both = int(cls.get("both") or 0)
    rust_only = int(cls.get("rust_only") or 0)
    python_only = int(cls.get("python_only") or 0)
    caught = both + rust_only + python_only
    return {
        "events": total,
        "caught": caught,
        "recall": round(caught / total, 6) if total else None,
        "both": both,
        "rust_only": rust_only,
        "python_only": python_only,
        "neither": int(cls.get("neither") or 0),
    }


def _hybrid_precision_proxy(event_significance: dict[str, Any], profile_name: str) -> dict[str, Any]:
    profile = (event_significance.get("profiles") or {}).get(profile_name) or {}
    precision = profile.get("detector_episode_precision") or {}
    py = precision.get("python") or {}
    rs = precision.get("rust") or {}
    py_eps = int(py.get("episodes") or 0)
    rs_eps = int(rs.get("episodes") or 0)
    py_success = int(py.get("successful") or 0)
    rs_success = int(rs.get("successful") or 0)
    denom = py_eps + rs_eps
    return {
        "python_success_rate": py.get("success_rate"),
        "rust_success_rate": rs.get("success_rate"),
        "union_success_rate_proxy": round((py_success + rs_success) / denom, 6) if denom else None,
        "python_episodes": py_eps,
        "rust_episodes": rs_eps,
        "union_episode_denominator_proxy": denom,
        "note": "Conservative proxy only; exact merged-stream precision is re-measured after hybrid integration.",
    }


def build_hybrid_intelligence_evaluation(
    event_significance: dict[str, Any],
    bullish_event_evaluation: dict[str, Any],
    early_detection_quality_gate: dict[str, Any],
) -> dict[str, Any]:
    if not bullish_event_evaluation.get("available"):
        return {
            "status": "INCONCLUSIVE",
            "recommendation": "BULLISH_EVENT_EVALUATION_REQUIRED",
            "detectors_modified": False,
        }

    overall = bullish_event_evaluation.get("overall") or {}
    by_mag = bullish_event_evaluation.get("by_magnitude") or {}
    by_morph = bullish_event_evaluation.get("by_morphology") or {}
    by_phase = bullish_event_evaluation.get("by_session_phase") or {}

    rust_overall = overall.get("rust") or {}
    python_overall = overall.get("python") or {}
    union_overall = _hybrid_union_from_classification(overall)
    overall_gain = (
        union_overall["recall"] - float(rust_overall.get("recall"))
        if union_overall.get("recall") is not None and rust_overall.get("recall") is not None
        else None
    )

    magnitude_rows = {}
    major_python_only = 0
    major_rust_only = 0
    major_gain_passes = []
    for band, stats in by_mag.items():
        union = _hybrid_union_from_classification(stats)
        rust = stats.get("rust") or {}
        python = stats.get("python") or {}
        gain = (
            union["recall"] - float(rust.get("recall"))
            if union.get("recall") is not None and rust.get("recall") is not None
            else None
        )
        magnitude_rows[band] = {
            "events": stats.get("events"),
            "python_recall": python.get("recall"),
            "rust_recall": rust.get("recall"),
            "union_recall": union.get("recall"),
            "union_gain_vs_rust": None if gain is None else round(gain, 6),
            "python_only": union.get("python_only"),
            "rust_only": union.get("rust_only"),
            "both": union.get("both"),
            "neither": union.get("neither"),
        }
        if band in {"20_to_50pct", "50_to_100pct", "100pct_plus"}:
            major_python_only += int(union.get("python_only") or 0)
            major_rust_only += int(union.get("rust_only") or 0)
            if band != "100pct_plus":
                major_gain_passes.append(
                    gain is not None and gain >= HYBRID_EXPECTATIONS["major_min_union_recall_gain_vs_rust"]
                )

    morphology_rows = {}
    for name, stats in by_morph.items():
        union = _hybrid_union_from_classification(stats)
        morphology_rows[name] = {
            "events": stats.get("events"),
            "python_recall": (stats.get("python") or {}).get("recall"),
            "rust_recall": (stats.get("rust") or {}).get("recall"),
            "union_recall": union.get("recall"),
            "python_only": union.get("python_only"),
            "rust_only": union.get("rust_only"),
            "neither": union.get("neither"),
        }

    phase_rows = {}
    for name, stats in by_phase.items():
        union = _hybrid_union_from_classification(stats)
        phase_rows[name] = {
            "events": stats.get("events"),
            "python_recall": (stats.get("python") or {}).get("recall"),
            "rust_recall": (stats.get("rust") or {}).get("recall"),
            "union_recall": union.get("recall"),
            "python_only": union.get("python_only"),
            "rust_only": union.get("rust_only"),
            "neither": union.get("neither"),
        }

    precision_plus2 = _hybrid_precision_proxy(event_significance, "plus_2pct_15m")
    precision_plus5 = _hybrid_precision_proxy(event_significance, "plus_5pct_15m")

    def precision_drop_ok(proxy: dict[str, Any]) -> bool:
        r = proxy.get("rust_success_rate")
        u = proxy.get("union_success_rate_proxy")
        if r is None or u is None or float(r) <= 0:
            return False
        drop = max(0.0, (float(r) - float(u)) / float(r))
        proxy["drop_fraction_vs_rust"] = round(drop, 6)
        proxy["max_allowed_drop_fraction"] = HYBRID_EXPECTATIONS["overall_max_union_precision_drop_vs_rust"]
        return drop <= HYBRID_EXPECTATIONS["overall_max_union_precision_drop_vs_rust"]

    gates = {
        "rust_quality_gate_already_passed": {
            "pass": early_detection_quality_gate.get("status") == "PASS",
            "actual": early_detection_quality_gate.get("status"),
            "required": "PASS",
        },
        "overall_union_recall_gain": {
            "pass": overall_gain is not None and overall_gain >= HYBRID_EXPECTATIONS["overall_min_union_recall_gain_vs_rust"],
            "actual": None if overall_gain is None else round(overall_gain, 6),
            "required": HYBRID_EXPECTATIONS["overall_min_union_recall_gain_vs_rust"],
            "rust_recall": rust_overall.get("recall"),
            "union_recall": union_overall.get("recall"),
        },
        "major_move_union_gain": {
            "pass": all(major_gain_passes) if major_gain_passes else False,
            "required": f">={HYBRID_EXPECTATIONS['major_min_union_recall_gain_vs_rust']:.2f} in populated +20-50% and +50-100% bands",
        },
        "python_adds_unique_major_winners": {
            "pass": major_python_only >= HYBRID_EXPECTATIONS["major_min_python_only_wins"],
            "actual": major_python_only,
            "required": HYBRID_EXPECTATIONS["major_min_python_only_wins"],
            "rust_only_major_winners": major_rust_only,
        },
        "plus2_precision_proxy_acceptable": {"pass": precision_drop_ok(precision_plus2), **precision_plus2},
        "plus5_precision_proxy_acceptable": {"pass": precision_drop_ok(precision_plus5), **precision_plus5},
    }

    failed = [k for k, v in gates.items() if not v.get("pass")]
    passed = [k for k, v in gates.items() if v.get("pass")]

    if not failed:
        status = "PASS"
        recommendation = "ADOPT_RUST_PRIMARY_PLUS_PYTHON_SPECIALIST_HYBRID"
    elif early_detection_quality_gate.get("status") == "PASS":
        status = "RUST_PRIMARY"
        recommendation = "ADOPT_RUST_PRIMARY_KEEP_PYTHON_OUT_OF_PRIMARY_SIGNAL_PATH"
    else:
        status = "INCONCLUSIVE"
        recommendation = "DO_NOT_INTEGRATE_YET"

    return {
        "status": status,
        "recommendation": recommendation,
        "detectors_modified": False,
        "decision_principle": "Hybrid wins only if Python adds unique actionable coverage to validated Rust without an unacceptable precision penalty.",
        "passed_gates": passed,
        "failed_gates": failed,
        "gates": gates,
        "overall": {
            "events": overall.get("events"),
            "python_recall": python_overall.get("recall"),
            "rust_recall": rust_overall.get("recall"),
            "union_recall": union_overall.get("recall"),
            "union_gain_vs_rust": None if overall_gain is None else round(overall_gain, 6),
            "classification": {
                "both": union_overall.get("both"),
                "rust_only": union_overall.get("rust_only"),
                "python_only": union_overall.get("python_only"),
                "neither": union_overall.get("neither"),
            },
        },
        "by_magnitude": magnitude_rows,
        "by_morphology": morphology_rows,
        "by_session_phase": phase_rows,
        "precision_proxies": {
            "plus_2pct_15m": precision_plus2,
            "plus_5pct_15m": precision_plus5,
        },
        "production_note": "Candidate-stream architecture only. Exact merged notification precision must be re-measured after hybrid implementation.",
    }


ARCHITECTURE_FREEZE_EXPECTATIONS = {
    "boundary_mirror_min": 1.0,
    "overall_hybrid_recall_min": 0.60,
    "overall_hybrid_gain_vs_rust_min": 0.03,
    "major_20_50_hybrid_recall_min": 0.60,
    "major_50_100_hybrid_recall_min": 0.75,
}

def build_architecture_freeze_gate(
    boundary_diagnostics: dict[str, Any],
    early_detection_quality_gate: dict[str, Any],
    hybrid_intelligence_evaluation: dict[str, Any],
    bullish_event_evaluation: dict[str, Any],
) -> dict[str, Any]:
    mirror = ((boundary_diagnostics.get("mirror_verification") or {}).get("match_rate"))
    hybrid_overall = hybrid_intelligence_evaluation.get("overall") or {}
    by_mag = hybrid_intelligence_evaluation.get("by_magnitude") or {}
    bullish_overall = bullish_event_evaluation.get("overall") or {}

    gates = {
        "boundary_integrity": {
            "pass": mirror is not None and float(mirror) >= 1.0,
            "actual": mirror, "required": 1.0,
        },
        "early_quality_gate": {
            "pass": early_detection_quality_gate.get("status") == "PASS",
            "actual": early_detection_quality_gate.get("status"), "required": "PASS",
        },
        "hybrid_gate": {
            "pass": hybrid_intelligence_evaluation.get("status") == "PASS",
            "actual": hybrid_intelligence_evaluation.get("status"), "required": "PASS",
        },
        "hybrid_overall_recall": {
            "pass": hybrid_overall.get("union_recall") is not None and float(hybrid_overall["union_recall"]) >= 0.60,
            "actual": hybrid_overall.get("union_recall"), "required": 0.60,
        },
        "hybrid_incremental_value": {
            "pass": hybrid_overall.get("union_gain_vs_rust") is not None and float(hybrid_overall["union_gain_vs_rust"]) >= 0.03,
            "actual": hybrid_overall.get("union_gain_vs_rust"), "required": 0.03,
        },
        "hybrid_20_to_50_recall": {
            "pass": (by_mag.get("20_to_50pct") or {}).get("union_recall") is not None
                    and float(by_mag["20_to_50pct"]["union_recall"]) >= 0.60,
            "actual": (by_mag.get("20_to_50pct") or {}).get("union_recall"), "required": 0.60,
        },
        "hybrid_50_to_100_recall": {
            "pass": (by_mag.get("50_to_100pct") or {}).get("union_recall") is not None
                    and float(by_mag["50_to_100pct"]["union_recall"]) >= 0.75,
            "actual": (by_mag.get("50_to_100pct") or {}).get("union_recall"), "required": 0.75,
        },
        "detectors_remained_frozen": {
            "pass": early_detection_quality_gate.get("detectors_modified") is False
                    and hybrid_intelligence_evaluation.get("detectors_modified") is False,
            "actual": {
                "early_quality_modified": early_detection_quality_gate.get("detectors_modified"),
                "hybrid_modified": hybrid_intelligence_evaluation.get("detectors_modified"),
            },
            "required": False,
        },
    }

    failed = [k for k,v in gates.items() if not v.get("pass")]
    status = "PASS" if not failed else "FAIL"
    return {
        "status": status,
        "recommendation": "FREEZE_INTELLIGENCE_AND_IMPLEMENT_HYBRID" if status == "PASS" else "DO_NOT_FREEZE_ARCHITECTURE",
        "research_phase_complete": status == "PASS",
        "selected_architecture": ({
            "primary_perception": "rust",
            "specialist_context_and_unique_candidate_source": "python",
            "candidate_policy": "union_with_rust_primary_priority",
            "notification_policy": "ranked_and_deduplicated_after_hybrid_wiring",
        } if status == "PASS" else None),
        "passed_gates": [k for k,v in gates.items() if v.get("pass")],
        "failed_gates": failed,
        "gates": gates,
        "evidence_snapshot": {
            "bullish_events": bullish_overall.get("events"),
            "python_recall": (bullish_overall.get("python") or {}).get("recall"),
            "rust_recall": (bullish_overall.get("rust") or {}).get("recall"),
            "hybrid_recall": hybrid_overall.get("union_recall"),
            "hybrid_gain_vs_rust": hybrid_overall.get("union_gain_vs_rust"),
            "rust_capture_efficiency_median": (bullish_overall.get("rust") or {}).get("capture_efficiency_median"),
            "python_capture_efficiency_median": (bullish_overall.get("python") or {}).get("capture_efficiency_median"),
        },
        "live_production_readiness": {
            "status": "PENDING_IMPLEMENTATION",
            "required_after_hybrid_wiring": [
                "exact_merged_candidate_stream_precision",
                "ntfy_notification_latency_and_deduplication",
                "dormant_to_awakening_to_expansion_end_to_end_alert_timing",
                "first_pullback_retest_and_reacceleration_lifecycle_notifications",
                "full_universe_sustained_load",
                "market_data_disconnect_and_recovery",
                "backend_restart_state_recovery",
                "VPS_health",
                "PWA_health",
                "Windows_desktop_health",
                "coordinated_release_smoke_test",
            ],
            "principle": "Replay evidence freezes the intelligence architecture; live system tests decide production readiness.",
        },
    }

def _safe_ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return float(numerator) / float(denominator)


def build_plus20_validation(profile: dict[str, Any] | None) -> dict[str, Any]:
    expectations = DECISION_EXPECTATIONS["plus_20pct_60m"]
    if not profile:
        return {
            "status": "INCONCLUSIVE",
            "reason": "plus_20pct_60m profile unavailable",
            "gates": {},
        }
    coverage = profile.get("objective_move_coverage") or {}
    total = int(coverage.get("objective_moves") or 0)
    sessions = int(coverage.get("sessions_with_objective_moves") or 0)
    rust_recall = (coverage.get("rust") or {}).get("recall")
    python_recall = (coverage.get("python") or {}).get("recall")
    classification = coverage.get("classification") or {}
    rust_only = int(classification.get("rust_only") or 0)
    python_only = int(classification.get("python_only") or 0)
    paired = coverage.get("paired_statistics") or {}
    recall_diff = paired.get("rust_minus_python_recall") or {}
    recall_ci_low = recall_diff.get("ci95_low")
    mcnemar_p = paired.get("mcnemar_exact_two_sided_p")
    margin = float(expectations["noninferiority_margin"])
    alpha = float(expectations["alpha"])

    gates = {
        "minimum_objective_moves": {
            "pass": total >= int(expectations["min_objective_moves"]),
            "actual": total,
            "required": int(expectations["min_objective_moves"]),
            "preferred": int(expectations["preferred_objective_moves"]),
        },
        "minimum_sessions": {
            "pass": sessions >= int(expectations["min_sessions"]),
            "actual": sessions,
            "required": int(expectations["min_sessions"]),
        },
    }
    sample_ready = all(gate["pass"] for gate in gates.values())
    superiority = bool(
        sample_ready
        and rust_recall is not None
        and python_recall is not None
        and float(rust_recall) > float(python_recall)
        and rust_only > python_only
        and mcnemar_p is not None
        and float(mcnemar_p) < alpha
        and recall_ci_low is not None
        and float(recall_ci_low) > 0.0
    )
    noninferior = bool(
        sample_ready
        and rust_recall is not None
        and python_recall is not None
        and recall_ci_low is not None
        and float(recall_ci_low) > -margin
    )

    if not sample_ready:
        status = "INCONCLUSIVE"
        conclusion = "COLLECT_MORE_PLUS20_EVENTS"
    elif superiority:
        status = "ESTABLISHED_SUPERIOR"
        conclusion = "RUST_PLUS20_ADVANTAGE_ESTABLISHED"
    elif noninferior:
        status = "ESTABLISHED_NONINFERIOR"
        conclusion = "RUST_PLUS20_NONINFERIOR_OR_COMPLEMENTARY"
    else:
        status = "ESTABLISHED_INFERIOR_OR_UNCERTAIN"
        conclusion = "DO_NOT_CLAIM_RUST_PLUS20_ADVANTAGE"

    return {
        "status": status,
        "conclusion": conclusion,
        "sample_ready": sample_ready,
        "gates": gates,
        "objective_moves": total,
        "sessions_with_objective_moves": sessions,
        "rust_recall": rust_recall,
        "python_recall": python_recall,
        "rust_recall_ci95": (coverage.get("rust") or {}).get("recall_ci95"),
        "python_recall_ci95": (coverage.get("python") or {}).get("recall_ci95"),
        "rust_only": rust_only,
        "python_only": python_only,
        "mcnemar_exact_two_sided_p": mcnemar_p,
        "rust_minus_python_recall_bootstrap": recall_diff,
        "paired_lead_bootstrap": paired.get("rust_minus_python_lead_seconds"),
        "noninferiority_margin": margin,
        "alpha": alpha,
        "superiority_established": superiority,
        "noninferiority_established": noninferior,
        "session_breakdown": coverage.get("session_breakdown") or [],
    }


def build_architecture_decision(
    event_significance: dict[str, Any],
    boundary_diagnostics: dict[str, Any],
) -> dict[str, Any]:
    """Apply the v6.4.9 evidence gate without changing either detector.

    Parity is intentionally excluded from the decision gate. The decision is based
    on objective-move recall, useful precision, lead time, exclusive discoveries,
    sample sufficiency, and mirror integrity.
    """
    profiles = event_significance.get("profiles") or {}
    required = ("plus_2pct_15m", "plus_5pct_15m", "plus_10pct_30m", "plus_20pct_60m")
    if not event_significance.get("available") or any(name not in profiles for name in required):
        return {
            "status": "INCONCLUSIVE",
            "architecture_recommendation": "COLLECT_MORE_HISTORICAL_EVIDENCE",
            "confidence": "LOW",
            "reason": "required event-significance profiles are unavailable",
            "parity_is_decision_gate": False,
            "gates": {},
        }

    p2 = profiles["plus_2pct_15m"]
    p5 = profiles["plus_5pct_15m"]
    p10 = profiles["plus_10pct_30m"]
    p20 = profiles["plus_20pct_60m"]
    plus20_validation = build_plus20_validation(p20)

    p2_cov = p2["objective_move_coverage"]
    p5_cov = p5["objective_move_coverage"]
    p10_cov = p10["objective_move_coverage"]
    p2_prec = p2["detector_episode_precision"]
    p5_prec = p5["detector_episode_precision"]

    p2_rust_recall = p2_cov["rust"].get("recall")
    p2_python_recall = p2_cov["python"].get("recall")
    p5_rust_recall = p5_cov["rust"].get("recall")
    p5_python_recall = p5_cov["python"].get("recall")
    p10_rust_recall = p10_cov["rust"].get("recall")
    p10_python_recall = p10_cov["python"].get("recall")

    p5_recall_multiple = _safe_ratio(p5_rust_recall, p5_python_recall)
    p5_rust_only = int((p5.get("rust_only_successful_discoveries") or {}).get("count") or 0)
    p5_python_only = int((p5.get("python_only_successful_discoveries") or {}).get("count") or 0)
    p5_exclusive_multiple = (
        float("inf") if p5_python_only == 0 and p5_rust_only > 0
        else _safe_ratio(float(p5_rust_only), float(p5_python_only))
    )

    p10_rust_lead = p10_cov["rust"].get("lead_seconds_median")
    p10_python_lead = p10_cov["python"].get("lead_seconds_median")
    p10_lead_ratio = _safe_ratio(p10_rust_lead, p10_python_lead)

    mirror_rate = ((boundary_diagnostics.get("mirror_verification") or {}).get("match_rate"))

    gates = {
        "sample_sufficiency_plus_5": {
            "pass": int(p5_cov.get("objective_moves") or 0) >= DECISION_EXPECTATIONS["plus_5pct_15m"]["min_objective_moves"],
            "actual": int(p5_cov.get("objective_moves") or 0),
            "required": DECISION_EXPECTATIONS["plus_5pct_15m"]["min_objective_moves"],
        },
        "sample_sufficiency_plus_10": {
            "pass": int(p10_cov.get("objective_moves") or 0) >= DECISION_EXPECTATIONS["plus_10pct_30m"]["min_objective_moves"],
            "actual": int(p10_cov.get("objective_moves") or 0),
            "required": DECISION_EXPECTATIONS["plus_10pct_30m"]["min_objective_moves"],
        },
        "plus_2_recall": {
            "pass": p2_rust_recall is not None and p2_rust_recall >= DECISION_EXPECTATIONS["plus_2pct_15m"]["min_rust_recall"],
            "rust": p2_rust_recall,
            "python": p2_python_recall,
            "required_rust_min": DECISION_EXPECTATIONS["plus_2pct_15m"]["min_rust_recall"],
        },
        "plus_2_precision_efficiency": {
            "pass": (p2_prec["rust"].get("success_rate") or 0.0) >= (p2_prec["python"].get("success_rate") or 0.0),
            "rust": p2_prec["rust"].get("success_rate"),
            "python": p2_prec["python"].get("success_rate"),
        },
        "plus_5_recall": {
            "pass": p5_rust_recall is not None and p5_rust_recall >= DECISION_EXPECTATIONS["plus_5pct_15m"]["min_rust_recall"],
            "rust": p5_rust_recall,
            "python": p5_python_recall,
            "required_rust_min": DECISION_EXPECTATIONS["plus_5pct_15m"]["min_rust_recall"],
        },
        "plus_5_recall_advantage": {
            "pass": p5_recall_multiple is not None and p5_recall_multiple >= DECISION_EXPECTATIONS["plus_5pct_15m"]["min_recall_multiple_vs_python"],
            "rust_vs_python_multiple": None if p5_recall_multiple is None else round(p5_recall_multiple, 4),
            "required_multiple": DECISION_EXPECTATIONS["plus_5pct_15m"]["min_recall_multiple_vs_python"],
        },
        "plus_5_precision_efficiency": {
            "pass": (p5_prec["rust"].get("success_rate") or 0.0) >= (p5_prec["python"].get("success_rate") or 0.0),
            "rust": p5_prec["rust"].get("success_rate"),
            "python": p5_prec["python"].get("success_rate"),
        },
        "plus_5_exclusive_discovery": {
            "pass": (
                p5_rust_only >= DECISION_EXPECTATIONS["plus_5pct_15m"]["min_rust_only_successful_discoveries"]
                and p5_exclusive_multiple is not None
                and p5_exclusive_multiple >= DECISION_EXPECTATIONS["plus_5pct_15m"]["min_exclusive_discovery_multiple_vs_python"]
            ),
            "rust_only_successful": p5_rust_only,
            "python_only_successful": p5_python_only,
            "rust_vs_python_multiple": "infinity" if p5_exclusive_multiple == float("inf") else (None if p5_exclusive_multiple is None else round(p5_exclusive_multiple, 4)),
            "required_rust_only_min": DECISION_EXPECTATIONS["plus_5pct_15m"]["min_rust_only_successful_discoveries"],
            "required_multiple": DECISION_EXPECTATIONS["plus_5pct_15m"]["min_exclusive_discovery_multiple_vs_python"],
        },
        "plus_10_recall_noninferiority": {
            "pass": p10_rust_recall is not None and p10_python_recall is not None and p10_rust_recall >= p10_python_recall,
            "rust": p10_rust_recall,
            "python": p10_python_recall,
        },
        "plus_10_lead_time_advantage": {
            "pass": p10_lead_ratio is not None and p10_lead_ratio <= DECISION_EXPECTATIONS["plus_10pct_30m"]["max_rust_lead_ratio_vs_python"],
            "rust_median_seconds": p10_rust_lead,
            "python_median_seconds": p10_python_lead,
            "rust_vs_python_ratio": None if p10_lead_ratio is None else round(p10_lead_ratio, 4),
            "max_allowed_ratio": DECISION_EXPECTATIONS["plus_10pct_30m"]["max_rust_lead_ratio_vs_python"],
        },
        "mirror_integrity": {
            "pass": mirror_rate == DECISION_EXPECTATIONS["mirror_match_rate"],
            "actual": mirror_rate,
            "required": DECISION_EXPECTATIONS["mirror_match_rate"],
        },
    }

    sample_gate_names = {"sample_sufficiency_plus_5", "sample_sufficiency_plus_10"}
    hard_gate_names = [name for name in gates if name not in sample_gate_names]
    sample_ready = all(bool(gates[name]["pass"]) for name in sample_gate_names)
    plus20_ready = bool(plus20_validation.get("sample_ready"))
    plus20_acceptable = plus20_validation.get("status") in {"ESTABLISHED_SUPERIOR", "ESTABLISHED_NONINFERIOR"}
    hard_pass = all(bool(gates[name]["pass"]) for name in hard_gate_names) and plus20_acceptable

    if not sample_ready or not plus20_ready:
        status = "INCONCLUSIVE"
        recommendation = "COLLECT_MORE_HISTORICAL_EVIDENCE"
        confidence = "LOW"
    elif hard_pass:
        status = "PASS"
        recommendation = "ADOPT_RUST_QUANT_PERCEPTION_PLUS_PYTHON_CONTEXTUAL_INTELLIGENCE"
        confidence = "HIGH"
    else:
        status = "FAIL"
        recommendation = "KEEP_PYTHON_PRIMARY_AND_REFINE_RUST"
        confidence = "HIGH"

    failed = [name for name, gate in gates.items() if not gate["pass"]]
    passed = [name for name, gate in gates.items() if gate["pass"]]
    return {
        "status": status,
        "architecture_recommendation": recommendation,
        "confidence": confidence,
        "parity_is_decision_gate": False,
        "decision_principle": "Choose the architecture by objective-move recall, useful precision, lead time, exclusive discovery value, and implementation integrity; parity is diagnostic only.",
        "passed_gates": passed,
        "failed_gates": failed,
        "gates": gates,
        "plus_20_validation": plus20_validation,
    }

def rust_pct(from_price: float, to_price: float) -> float:
    return 0.0 if from_price == 0.0 else (to_price - from_price) / from_price * 100.0


def rust_round_score(present_count: int) -> int:
    return int(math.floor((present_count / len(RUST_RECIPE_CHECKS)) * 10.0 + 0.5))


def rust_mirror_evaluate(trades: deque[tuple[float, float, float]], closed_bucket_count: int) -> dict[str, Any]:
    latest_ts, latest_price, _ = trades[-1]
    oldest_ts = trades[0][0]
    previous_trade_gap = latest_ts - trades[-2][0] if len(trades) >= 2 else None
    result: dict[str, Any] = {
        "evaluated_at": latest_ts,
        "price": latest_price,
        "trade_count_300s": len(trades),
        "closed_bucket_count": int(closed_bucket_count),
        "warmup_bucket_target": RUST_WARMUP_BUCKETS,
        "oldest_trade_ts_300s": oldest_ts,
        "newest_trade_ts_300s": latest_ts,
        "trade_span_seconds_300s": latest_ts - oldest_ts,
        "previous_trade_gap_seconds": previous_trade_gap,
        "qualified": False,
        "recipe_score": None,
        "recipe_present": [],
        "recipe_missing": list(RUST_RECIPE_CHECKS),
        "metrics": {},
    }
    if closed_bucket_count < RUST_WARMUP_BUCKETS:
        result["blocked_by"] = "fewer_than_8_closed_buckets"
        return result

    base_cutoff = latest_ts - 300.0
    trigger_cutoff = latest_ts - 5.0
    base = [trade for trade in trades if trade[0] >= base_cutoff]
    prior = [trade for trade in base if trade[0] < trigger_cutoff]
    result["prior_trade_count"] = len(prior)
    result["prior_oldest_trade_ts"] = prior[0][0] if prior else None
    result["prior_newest_trade_ts"] = prior[-1][0] if prior else None
    result["prior_trade_span_seconds"] = (prior[-1][0] - prior[0][0]) if len(prior) >= 2 else 0.0 if prior else None
    if not prior:
        result["blocked_by"] = "no_prior_reference_trade"
        return result

    base_low = min(trade[1] for trade in base)
    base_high = max(trade[1] for trade in base)
    trigger = max(trade[1] for trade in prior)
    range_pct = rust_pct(base_low, base_high)
    extension = rust_pct(base_low, latest_price)
    trigger_distance = rust_pct(latest_price, trigger)
    trades5 = [trade for trade in base if trade[0] >= latest_ts - 5.0]
    trades15 = [trade for trade in base if trade[0] >= latest_ts - 15.0]
    trades30 = [trade for trade in base if trade[0] >= latest_ts - 30.0]
    trades60 = [trade for trade in base if trade[0] >= latest_ts - 60.0]
    trades120 = [trade for trade in base if trade[0] >= latest_ts - 120.0]
    volume15 = sum(trade[2] for trade in trades15)
    volume30 = sum(trade[2] for trade in trades30)
    first5 = next((trade for trade in base if trade[0] >= latest_ts - 5.0), None)
    change5 = rust_pct(first5[1], latest_price) if first5 else 0.0
    change15 = rust_pct(trades15[0][1], latest_price) if trades15 else 0.0

    checks = {
        "compressed or orderly base": range_pct <= 3.5,
        "price remains near the base": extension <= 0.75,
        "pressing a nearby trigger": -0.35 <= trigger_distance <= 0.75,
        "EMA structure is improving": change15 >= 0.0,
        "relative volume is waking up": volume15 * 2.0 >= max(volume30, 1.0),
        "participation is broadening": len(trades15) >= 3 and volume15 > 0.0,
        "price or volume is accelerating": change5 > 0.0 or volume15 > volume30 * 0.55,
        "path avoids bearish failure": change15 > -0.2,
    }
    present = [name for name in RUST_RECIPE_CHECKS if checks[name]]
    missing = [name for name in RUST_RECIPE_CHECKS if not checks[name]]
    score = rust_round_score(len(present))
    qualified = score >= 7 and extension <= 0.75 and -0.35 <= trigger_distance <= 0.75
    continuity_holds = bool(
        checks["compressed or orderly base"]
        and checks["price remains near the base"]
        and checks["pressing a nearby trigger"]
        and checks["EMA structure is improving"]
        and checks["relative volume is waking up"]
        and checks["path avoids bearish failure"]
    )
    result.update({
        "qualified": qualified,
        "continuity_holds": continuity_holds,
        "recipe_score": score,
        "recipe_present": present,
        "recipe_missing": missing,
        "checks": checks,
        "metrics": {
            "base_low": base_low,
            "base_high": base_high,
            "trigger": trigger,
            "range_pct": range_pct,
            "base_extension_pct": extension,
            "trigger_distance_pct": trigger_distance,
            "change_5s_pct": change5,
            "change_15s_pct": change15,
            "trades_5s": len(trades5),
            "trades_15s": len(trades15),
            "trades_30s": len(trades30),
            "trades_60s": len(trades60),
            "trades_120s": len(trades120),
            "trades_300s": len(base),
            "volume_15s": volume15,
            "volume_30s": volume30,
        },
        "blocked_by": None if qualified else "qualification_gate",
    })
    return result


def select_episode_samples(episodes: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    return sorted(
        episodes,
        key=lambda item: (
            bool(item.get("expanded")),
            int(item.get("max_recipe_score") or 0),
            int(item.get("detection_count") or 0),
            float(item.get("duration_seconds") or 0.0),
        ),
        reverse=True,
    )[:limit]


def nearest_detection(items: list[dict[str, Any]], ticker: str, target_at: float) -> dict[str, Any] | None:
    candidates = [item for item in items if str(item.get("ticker") or "").upper() == ticker]
    if not candidates:
        return None
    item = min(candidates, key=lambda row: abs(float(row["detected_at"]) - target_at))
    return {
        "detected_at": item.get("detected_at"),
        "delta_seconds": round(float(item["detected_at"]) - target_at, 6),
        "recipe_score": item.get("recipe_score"),
        "recipe_present": list(item.get("recipe_present") or []),
        "recipe_missing": list(item.get("recipe_missing") or []),
        "trigger_distance_pct": item.get("trigger_distance_pct"),
        "base_extension_pct": item.get("base_extension_pct"),
    }


def build_boundary_targets(python_only, rust_only, matches, python_episodes, rust_episodes, limit):
    python_samples = select_episode_samples(python_only, limit)
    rust_samples = select_episode_samples(rust_only, limit)
    match_samples = sorted(matches, key=lambda item: abs(float(item["delta_seconds"])), reverse=True)[:limit]
    python_by_key = {(item["ticker"], float(item["first_detected_at"])): item for item in python_episodes}
    rust_by_key = {(item["ticker"], float(item["first_detected_at"])): item for item in rust_episodes}
    targets = []
    diagnostics = {"python_only": [], "rust_only": [], "matched_large_delta": []}
    for index, episode in enumerate(python_samples):
        target_id = f"python-only-{index}"
        targets.append({"id": target_id, "ticker": episode["ticker"], "target_at": float(episode["first_detected_at"])})
        diagnostics["python_only"].append({"target_id": target_id, "python_episode": episode})
    for index, episode in enumerate(rust_samples):
        target_id = f"rust-only-{index}"
        targets.append({"id": target_id, "ticker": episode["ticker"], "target_at": float(episode["first_detected_at"])})
        diagnostics["rust_only"].append({"target_id": target_id, "rust_episode": episode})
    for index, match in enumerate(match_samples):
        python_episode = python_by_key.get((match["ticker"], float(match["python_first_at"])))
        rust_episode = rust_by_key.get((match["ticker"], float(match["rust_first_at"])))
        python_target_id = f"matched-{index}-python"
        rust_target_id = f"matched-{index}-rust"
        targets.extend([
            {"id": python_target_id, "ticker": match["ticker"], "target_at": float(match["python_first_at"])},
            {"id": rust_target_id, "ticker": match["ticker"], "target_at": float(match["rust_first_at"])},
        ])
        diagnostics["matched_large_delta"].append({
            "python_target_id": python_target_id,
            "rust_target_id": rust_target_id,
            "match": match,
            "python_episode": python_episode,
            "rust_episode": rust_episode,
        })
    return targets, diagnostics


def collect_rust_mirror_snapshots(dataset: Path, targets: list[dict[str, Any]], window_seconds: float) -> dict[str, list[dict[str, Any]]]:
    by_ticker = defaultdict(list)
    for target in targets:
        by_ticker[str(target["ticker"]).upper()].append(target)
    states = {}
    snapshots = defaultdict(list)
    target_symbols = set(by_ticker)
    with dataset.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
                if str(value.get("event_type", "")).lower() != "trade":
                    continue
                ticker = str(value.get("symbol") or "").upper()
                if ticker not in target_symbols:
                    continue
                ts = float(value.get("source_ts", 0))
                payload = value.get("payload") or {}
                price = float(payload.get("price", 0))
                size = float(payload.get("size", 0))
            except Exception as exc:
                raise ValueError(f"unable to parse diagnostic dataset line {line_number}: {exc}") from exc
            if ts <= 0 or price <= 0 or size < 0:
                continue
            feed = str(value.get("feed") or "").lower()
            state = states.setdefault(ticker, {
                "trades": deque(),
                "last_eval": 0.0,
                "armed": False,
                "first_seen_ts": ts,
                "total_trades_seen": 0,
                "last_trade_ts": None,
                "last_feed": None,
                "current_bucket_start": None,
                "closed_bucket_count": 0,
            })
            prior_stream_trade_ts = state["last_trade_ts"]
            entering_overnight = state["last_feed"] is not None and state["last_feed"] != "boats" and feed == "boats"
            large_gap = prior_stream_trade_ts is not None and ts - float(prior_stream_trade_ts) >= RUST_SESSION_RESET_GAP_SECONDS
            if entering_overnight or large_gap:
                state["trades"].clear()
                state["last_eval"] = 0.0
                state["armed"] = False
                state["current_bucket_start"] = None
                state["closed_bucket_count"] = 0
                state["last_trade_ts"] = None
                state["first_seen_ts"] = ts
                prior_stream_trade_ts = None

            bucket_start = ts - (ts % RUST_BUCKET_SECONDS)
            current_bucket_start = state["current_bucket_start"]
            if current_bucket_start is None:
                state["current_bucket_start"] = bucket_start
            elif bucket_start > float(current_bucket_start):
                crossed = round((bucket_start - float(current_bucket_start)) / RUST_BUCKET_SECONDS)
                state["closed_bucket_count"] = min(
                    RUST_KEEP_BUCKETS,
                    int(state["closed_bucket_count"]) + int(crossed),
                )
                state["current_bucket_start"] = bucket_start

            trades = state["trades"]
            state["total_trades_seen"] += 1
            state["last_trade_ts"] = ts
            state["last_feed"] = feed
            trades.append((ts, price, size))
            while trades and trades[0][0] < ts - 300.0:
                trades.popleft()
            if ts - float(state["last_eval"]) < 1.0:
                continue
            previous_eval_ts = float(state["last_eval"]) if float(state["last_eval"]) > 0 else None
            state["last_eval"] = ts
            evaluation = rust_mirror_evaluate(trades, int(state["closed_bucket_count"]))
            evaluation["first_seen_ts"] = state["first_seen_ts"]
            evaluation["seconds_since_first_seen"] = ts - float(state["first_seen_ts"])
            evaluation["total_trades_seen"] = int(state["total_trades_seen"])
            evaluation["stream_previous_trade_gap_seconds"] = None if prior_stream_trade_ts is None else ts - float(prior_stream_trade_ts)
            evaluation["evaluation_gap_seconds"] = None if previous_eval_ts is None else ts - previous_eval_ts
            armed_before = bool(state["armed"])
            qualifies = bool(evaluation["qualified"])
            continuity_holds = bool(evaluation.get("continuity_holds"))
            emitted_transition = qualifies and not armed_before
            armed_after = qualifies or (armed_before and continuity_holds)
            state["armed"] = armed_after
            evaluation["armed_before"] = armed_before
            evaluation["armed_after"] = armed_after
            evaluation["continuity_holds"] = continuity_holds
            evaluation["emitted_transition"] = emitted_transition
            for target in by_ticker[ticker]:
                if abs(ts - float(target["target_at"])) <= window_seconds:
                    snapshots[target["id"]].append(dict(evaluation))
    return snapshots


def compact_state(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
    if not snapshot:
        return None
    metrics = snapshot.get("metrics") or {}
    return {
        "evaluated_at": snapshot.get("evaluated_at"),
        "price": snapshot.get("price"),
        "qualified": snapshot.get("qualified"),
        "recipe_score": snapshot.get("recipe_score"),
        "armed_before": snapshot.get("armed_before"),
        "armed_after": snapshot.get("armed_after"),
        "emitted_transition": snapshot.get("emitted_transition"),
        "blocked_by": snapshot.get("blocked_by"),
        "base_low": metrics.get("base_low"),
        "base_high": metrics.get("base_high"),
        "trigger": metrics.get("trigger"),
        "base_extension_pct": metrics.get("base_extension_pct"),
        "trigger_distance_pct": metrics.get("trigger_distance_pct"),
        "range_pct": metrics.get("range_pct"),
        "change_5s_pct": metrics.get("change_5s_pct"),
        "change_15s_pct": metrics.get("change_15s_pct"),
        "trade_count_300s": snapshot.get("trade_count_300s"),
        "closed_bucket_count": snapshot.get("closed_bucket_count"),
        "warmup_bucket_target": snapshot.get("warmup_bucket_target"),
        "prior_trade_count": snapshot.get("prior_trade_count"),
        "oldest_trade_ts_300s": snapshot.get("oldest_trade_ts_300s"),
        "trade_span_seconds_300s": snapshot.get("trade_span_seconds_300s"),
        "prior_trade_span_seconds": snapshot.get("prior_trade_span_seconds"),
        "previous_trade_gap_seconds": snapshot.get("previous_trade_gap_seconds"),
        "stream_previous_trade_gap_seconds": snapshot.get("stream_previous_trade_gap_seconds"),
        "evaluation_gap_seconds": snapshot.get("evaluation_gap_seconds"),
        "first_seen_ts": snapshot.get("first_seen_ts"),
        "seconds_since_first_seen": snapshot.get("seconds_since_first_seen"),
        "total_trades_seen": snapshot.get("total_trades_seen"),
        "trades_5s": metrics.get("trades_5s"),
        "trades_15s": metrics.get("trades_15s"),
        "trades_30s": metrics.get("trades_30s"),
        "trades_60s": metrics.get("trades_60s"),
        "trades_120s": metrics.get("trades_120s"),
        "trades_300s": metrics.get("trades_300s"),
    }


def summarize_boundary_snapshots(target_at: float, rows: list[dict[str, Any]]) -> dict[str, Any]:
    rows = sorted(rows, key=lambda item: float(item["evaluated_at"]))
    before = [item for item in rows if float(item["evaluated_at"]) <= target_at]
    after = [item for item in rows if float(item["evaluated_at"]) >= target_at]
    qualified = [item for item in rows if item.get("qualified")]
    emitted = [item for item in rows if item.get("emitted_transition")]
    nearest = min(rows, key=lambda item: abs(float(item["evaluated_at"]) - target_at)) if rows else None
    nearest_index = rows.index(nearest) if nearest is not None else -1
    history_start = max(0, nearest_index - 5)
    history_end = min(len(rows), nearest_index + 6) if nearest_index >= 0 else 0
    return {
        "snapshot_count": len(rows),
        "nearest": nearest,
        "last_at_or_before": before[-1] if before else None,
        "first_at_or_after": after[0] if after else None,
        "first_qualified_in_window": qualified[0] if qualified else None,
        "first_emitted_transition_in_window": emitted[0] if emitted else None,
        "state_history_near_boundary": [compact_state(item) for item in rows[history_start:history_end]],
    }


def implied_reference_from_pct(current_price: float | None, pct_value: float | None) -> float | None:
    if current_price is None or pct_value is None:
        return None
    denominator = 1.0 + float(pct_value) / 100.0
    if denominator == 0.0:
        return None
    return float(current_price) / denominator


def semantic_geometry_comparison(python_episode: dict[str, Any] | None, boundary: dict[str, Any]) -> dict[str, Any] | None:
    if not python_episode:
        return None
    snapshot = boundary.get("nearest")
    metrics = (snapshot or {}).get("metrics") or {}
    python_price = python_episode.get("first_price")
    python_extension = python_episode.get("first_base_extension_pct")
    python_trigger_distance = python_episode.get("first_trigger_distance_pct")
    rust_extension = metrics.get("base_extension_pct")
    rust_trigger_distance = metrics.get("trigger_distance_pct")
    python_base = implied_reference_from_pct(python_price, python_extension)
    python_trigger = None
    if python_price is not None and python_trigger_distance is not None:
        python_trigger = float(python_price) * (1.0 + float(python_trigger_distance) / 100.0)
    return {
        "python_reported": {
            "price": python_price,
            "base_extension_pct": python_extension,
            "trigger_distance_pct": python_trigger_distance,
            "implied_base_price": python_base,
            "implied_trigger_price": python_trigger,
            "near_base": None if python_extension is None else float(python_extension) <= 0.75,
            "near_trigger": None if python_trigger_distance is None else -0.35 <= float(python_trigger_distance) <= 0.75,
        },
        "rust_mirror": {
            "price": (snapshot or {}).get("price"),
            "base_low": metrics.get("base_low"),
            "base_high": metrics.get("base_high"),
            "trigger": metrics.get("trigger"),
            "base_extension_pct": rust_extension,
            "trigger_distance_pct": rust_trigger_distance,
            "near_base": None if rust_extension is None else float(rust_extension) <= 0.75,
            "near_trigger": None if rust_trigger_distance is None else -0.35 <= float(rust_trigger_distance) <= 0.75,
        },
        "delta": {
            "base_extension_pct": None if python_extension is None or rust_extension is None else round(float(rust_extension) - float(python_extension), 6),
            "trigger_distance_pct": None if python_trigger_distance is None or rust_trigger_distance is None else round(float(rust_trigger_distance) - float(python_trigger_distance), 6),
            "base_price": None if python_base is None or metrics.get("base_low") is None else round(float(metrics["base_low"]) - float(python_base), 6),
            "trigger_price": None if python_trigger is None or metrics.get("trigger") is None else round(float(metrics["trigger"]) - float(python_trigger), 6),
        },
        "classification": {
            "near_base_disagrees": python_extension is not None and rust_extension is not None and (float(python_extension) <= 0.75) != (float(rust_extension) <= 0.75),
            "near_trigger_disagrees": python_trigger_distance is not None and rust_trigger_distance is not None and (-0.35 <= float(python_trigger_distance) <= 0.75) != (-0.35 <= float(rust_trigger_distance) <= 0.75),
        },
        "note": "Python reference prices are inferred from the finding's reported percentage fields; they are diagnostic reconstructions, not direct Python internal state.",
    }


def hard_gate_failures(snapshot: dict[str, Any] | None) -> list[str]:
    if not snapshot:
        return ["no_rust_evaluation_in_window"]
    blocked = snapshot.get("blocked_by")
    if blocked in {"fewer_than_8_closed_buckets", "no_prior_reference_trade"}:
        return [str(blocked)]
    failures: list[str] = []
    metrics = snapshot.get("metrics") or {}
    score = snapshot.get("recipe_score")
    extension = metrics.get("base_extension_pct")
    trigger_distance = metrics.get("trigger_distance_pct")
    if score is not None and int(score) < 7:
        failures.append("recipe_score_below_7")
    if extension is not None and float(extension) > 0.75:
        failures.append("base_extension_above_0.75pct")
    if trigger_distance is not None and not (-0.35 <= float(trigger_distance) <= 0.75):
        failures.append("trigger_distance_outside_gate")
    return failures or ([str(blocked)] if blocked else [])



def classify_warmup_state(snapshot: dict[str, Any] | None) -> list[str]:
    if not snapshot:
        return ["no_rust_evaluation_in_window"]
    labels: list[str] = []
    trade_count = int(snapshot.get("trade_count_300s") or 0)
    prior_count = int(snapshot.get("prior_trade_count") or 0)
    seconds_since_first_seen = snapshot.get("seconds_since_first_seen")
    trade_span = snapshot.get("trade_span_seconds_300s")
    stream_gap = snapshot.get("stream_previous_trade_gap_seconds")
    closed_bucket_count = int(snapshot.get("closed_bucket_count") or 0)
    if closed_bucket_count < RUST_WARMUP_BUCKETS:
        labels.append("insufficient_closed_bucket_warmup")
    if trade_count < 12:
        labels.append("sparse_300s_trade_count_nonblocking")
    if prior_count < 8:
        labels.append("sparse_prior_trade_count_nonblocking")
    if seconds_since_first_seen is not None and float(seconds_since_first_seen) < 300.0:
        labels.append("within_first_300s_of_symbol_history")
    if trade_span is not None and float(trade_span) < 60.0 and trade_count < 20:
        labels.append("short_or_sparse_rolling_span")
    if stream_gap is not None and float(stream_gap) >= 60.0:
        labels.append("long_gap_before_boundary")
    if stream_gap is not None and float(stream_gap) >= 300.0:
        labels.append("gap_exceeds_rust_rolling_window")
    return labels


def warmup_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
    if not snapshot:
        return None
    metrics = snapshot.get("metrics") or {}
    return {
        "evaluated_at": snapshot.get("evaluated_at"),
        "trade_count_300s": snapshot.get("trade_count_300s"),
        "closed_bucket_count": snapshot.get("closed_bucket_count"),
        "warmup_bucket_target": snapshot.get("warmup_bucket_target"),
        "prior_trade_count": snapshot.get("prior_trade_count"),
        "oldest_trade_ts_300s": snapshot.get("oldest_trade_ts_300s"),
        "newest_trade_ts_300s": snapshot.get("newest_trade_ts_300s"),
        "trade_span_seconds_300s": snapshot.get("trade_span_seconds_300s"),
        "prior_oldest_trade_ts": snapshot.get("prior_oldest_trade_ts"),
        "prior_newest_trade_ts": snapshot.get("prior_newest_trade_ts"),
        "prior_trade_span_seconds": snapshot.get("prior_trade_span_seconds"),
        "first_seen_ts": snapshot.get("first_seen_ts"),
        "seconds_since_first_seen": snapshot.get("seconds_since_first_seen"),
        "total_trades_seen": snapshot.get("total_trades_seen"),
        "stream_previous_trade_gap_seconds": snapshot.get("stream_previous_trade_gap_seconds"),
        "evaluation_gap_seconds": snapshot.get("evaluation_gap_seconds"),
        "trades_by_window": {
            "5s": metrics.get("trades_5s"),
            "15s": metrics.get("trades_15s"),
            "30s": metrics.get("trades_30s"),
            "60s": metrics.get("trades_60s"),
            "120s": metrics.get("trades_120s"),
            "300s": metrics.get("trades_300s"),
        },
        "classifications": classify_warmup_state(snapshot),
    }

def compare_python_recipe_to_rust_boundary(python_episode: dict[str, Any] | None, boundary: dict[str, Any]) -> dict[str, Any] | None:
    if not python_episode:
        return None
    snapshot = boundary.get("nearest")
    python_present = set(map(str, python_episode.get("first_recipe_present") or []))
    rust_present = set(map(str, (snapshot or {}).get("recipe_present") or []))
    return {
        "python_present_but_rust_missing": sorted(python_present - rust_present),
        "rust_present_but_python_missing": sorted(rust_present - python_present),
        "hard_gate_failures": hard_gate_failures(snapshot),
        "rust_qualified_at_nearest_evaluation": bool((snapshot or {}).get("qualified")),
        "rust_emitted_at_nearest_evaluation": bool((snapshot or {}).get("emitted_transition")),
        "geometry": semantic_geometry_comparison(python_episode, boundary),
        "rolling_state": warmup_snapshot(snapshot),
    }


def attach_boundary_diagnostics(diagnostics, targets, snapshots, oracle, rust):
    target_by_id = {item["id"]: item for item in targets}
    mirror_checks = 0
    mirror_emission_matches = 0
    for item in diagnostics["python_only"]:
        target = target_by_id[item["target_id"]]
        item["nearest_rust_detection"] = nearest_detection(rust, target["ticker"], float(target["target_at"]))
        item["rust_mirror_boundary"] = summarize_boundary_snapshots(float(target["target_at"]), snapshots.get(item["target_id"], []))
        item["recipe_boundary_diff"] = compare_python_recipe_to_rust_boundary(item.get("python_episode"), item["rust_mirror_boundary"])
    for item in diagnostics["rust_only"]:
        target = target_by_id[item["target_id"]]
        item["nearest_python_detection"] = nearest_detection(oracle, target["ticker"], float(target["target_at"]))
        boundary = summarize_boundary_snapshots(float(target["target_at"]), snapshots.get(item["target_id"], []))
        item["rust_mirror_boundary"] = boundary
        mirror_checks += 1
        nearest = boundary.get("nearest")
        if nearest and nearest.get("emitted_transition"):
            mirror_emission_matches += 1
    for item in diagnostics["matched_large_delta"]:
        python_target = target_by_id[item["python_target_id"]]
        rust_target = target_by_id[item["rust_target_id"]]
        item["rust_mirror_at_python_boundary"] = summarize_boundary_snapshots(float(python_target["target_at"]), snapshots.get(item["python_target_id"], []))
        item["recipe_boundary_diff_at_python_time"] = compare_python_recipe_to_rust_boundary(item.get("python_episode"), item["rust_mirror_at_python_boundary"])
        rust_boundary = summarize_boundary_snapshots(float(rust_target["target_at"]), snapshots.get(item["rust_target_id"], []))
        item["rust_mirror_at_rust_boundary"] = rust_boundary
        python_nearest = item["rust_mirror_at_python_boundary"].get("nearest")
        rust_nearest = rust_boundary.get("nearest")
        item["rolling_state_transition"] = {
            "at_python_boundary": warmup_snapshot(python_nearest),
            "at_rust_boundary": warmup_snapshot(rust_nearest),
            "rust_blocked_at_python_boundary": (python_nearest or {}).get("blocked_by"),
            "rust_blocked_at_rust_boundary": (rust_nearest or {}).get("blocked_by"),
            "became_history_ready_between_boundaries": bool(
                (python_nearest or {}).get("blocked_by") in {"fewer_than_8_closed_buckets", "no_prior_reference_trade"}
                and (rust_nearest or {}).get("blocked_by") not in {"fewer_than_8_closed_buckets", "no_prior_reference_trade"}
            ),
        }
        mirror_checks += 1
        nearest = rust_nearest
        if nearest and nearest.get("emitted_transition"):
            mirror_emission_matches += 1
    blocker_counts: dict[str, int] = defaultdict(int)
    ingredient_mismatch_counts: dict[str, int] = defaultdict(int)
    for item in diagnostics["python_only"]:
        diff = item.get("recipe_boundary_diff") or {}
        for gate in diff.get("hard_gate_failures") or []:
            blocker_counts[str(gate)] += 1
        for ingredient in diff.get("python_present_but_rust_missing") or []:
            ingredient_mismatch_counts[str(ingredient)] += 1
    geometry_counts = defaultdict(int)
    base_extension_deltas: list[float] = []
    trigger_distance_deltas: list[float] = []
    for item in diagnostics["python_only"]:
        geometry = ((item.get("recipe_boundary_diff") or {}).get("geometry") or {})
        classification = geometry.get("classification") or {}
        if classification.get("near_base_disagrees"):
            geometry_counts["near_base_disagrees"] += 1
        if classification.get("near_trigger_disagrees"):
            geometry_counts["near_trigger_disagrees"] += 1
        delta = geometry.get("delta") or {}
        if delta.get("base_extension_pct") is not None:
            base_extension_deltas.append(abs(float(delta["base_extension_pct"])))
        if delta.get("trigger_distance_pct") is not None:
            trigger_distance_deltas.append(abs(float(delta["trigger_distance_pct"])))
    warmup_classification_counts: dict[str, int] = defaultdict(int)
    warmup_trade_counts: list[float] = []
    warmup_closed_bucket_counts: list[float] = []
    warmup_prior_counts: list[float] = []
    warmup_spans: list[float] = []
    warmup_seconds_since_first_seen: list[float] = []
    warmup_stream_gaps: list[float] = []
    for item in diagnostics["python_only"]:
        rolling = ((item.get("recipe_boundary_diff") or {}).get("rolling_state") or {})
        for classification in rolling.get("classifications") or []:
            warmup_classification_counts[str(classification)] += 1
        if rolling.get("trade_count_300s") is not None:
            warmup_trade_counts.append(float(rolling["trade_count_300s"]))
        if rolling.get("closed_bucket_count") is not None:
            warmup_closed_bucket_counts.append(float(rolling["closed_bucket_count"]))
        if rolling.get("prior_trade_count") is not None:
            warmup_prior_counts.append(float(rolling["prior_trade_count"]))
        if rolling.get("trade_span_seconds_300s") is not None:
            warmup_spans.append(float(rolling["trade_span_seconds_300s"]))
        if rolling.get("seconds_since_first_seen") is not None:
            warmup_seconds_since_first_seen.append(float(rolling["seconds_since_first_seen"]))
        if rolling.get("stream_previous_trade_gap_seconds") is not None:
            warmup_stream_gaps.append(float(rolling["stream_previous_trade_gap_seconds"]))

    matched_history_ready_transitions = sum(
        1
        for item in diagnostics["matched_large_delta"]
        if (item.get("rolling_state_transition") or {}).get("became_history_ready_between_boundaries")
    )
    matched_python_boundary_history_blocked = sum(
        1
        for item in diagnostics["matched_large_delta"]
        if (item.get("rolling_state_transition") or {}).get("rust_blocked_at_python_boundary")
        in {"fewer_than_8_closed_buckets", "no_prior_reference_trade"}
    )
    diagnostics["matched_large_delta_state_summary"] = {
        "samples": len(diagnostics["matched_large_delta"]),
        "rust_history_blocked_at_python_boundary": matched_python_boundary_history_blocked,
        "became_history_ready_between_python_and_rust_boundaries": matched_history_ready_transitions,
    }

    diagnostics["python_only_sample_summary"] = {
        "hard_gate_failure_counts": dict(sorted(blocker_counts.items(), key=lambda pair: (-pair[1], pair[0]))),
        "python_present_but_rust_missing_counts": dict(sorted(ingredient_mismatch_counts.items(), key=lambda pair: (-pair[1], pair[0]))),
        "geometry_disagreement_counts": dict(sorted(geometry_counts.items())),
        "absolute_base_extension_delta_pct_median": median(base_extension_deltas) if base_extension_deltas else None,
        "absolute_trigger_distance_delta_pct_median": median(trigger_distance_deltas) if trigger_distance_deltas else None,
        "rolling_state": {
            "classification_counts": dict(sorted(warmup_classification_counts.items(), key=lambda pair: (-pair[1], pair[0]))),
            "trade_count_300s_median": median(warmup_trade_counts) if warmup_trade_counts else None,
            "closed_bucket_count_median": median(warmup_closed_bucket_counts) if warmup_closed_bucket_counts else None,
            "prior_trade_count_median": median(warmup_prior_counts) if warmup_prior_counts else None,
            "trade_span_seconds_300s_median": median(warmup_spans) if warmup_spans else None,
            "seconds_since_first_seen_median": median(warmup_seconds_since_first_seen) if warmup_seconds_since_first_seen else None,
            "stream_previous_trade_gap_seconds_median": median(warmup_stream_gaps) if warmup_stream_gaps else None,
            "stream_previous_trade_gap_seconds_p95": percentile(warmup_stream_gaps, 0.95),
        },
    }

    diagnostics["methodology"] = {
        "purpose": "Trace Rust-equivalent recipe predicates around selected first-detection boundaries without changing production logic.",
        "mirror_source": "Python mirror of rust/market-replay/src/lib.rs bucket-warmup evaluate(), active-state/structural episode continuity, and 1-second emission-edge semantics.",
        "interpretation": "Python-only samples show which Rust gate/predicate is false at Python's boundary; geometry diagnostics compare Python-reported base/trigger percentages against the Rust mirror. Rolling-state diagnostics expose Rust's Python-aligned closed-bucket warmup, 300-second trade buffer depth, prior-trigger history, symbol warm-up age, local trade density, and gaps around the same boundary. Rust-boundary emitted_transition verifies the mirror against actual Rust candidate emission.",
    }
    diagnostics["mirror_verification"] = {
        "sampled_rust_boundaries": mirror_checks,
        "emitted_transition_matches": mirror_emission_matches,
        "match_rate": round(mirror_emission_matches / mirror_checks, 6) if mirror_checks else None,
    }
    return diagnostics


def attach_outcomes(episodes: list[dict[str, Any]], expansions: list[dict[str, Any]], horizon_seconds: float) -> None:
    by_ticker: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for expansion in expansions:
        by_ticker[str(expansion["ticker"])].append(expansion)
    for rows in by_ticker.values():
        rows.sort(key=lambda item: float(item["onset_at"]))

    for episode in episodes:
        first_at = float(episode["first_detected_at"])
        onset = next(
            (
                float(item["onset_at"])
                for item in by_ticker.get(str(episode["ticker"]), [])
                if first_at <= float(item["onset_at"]) <= first_at + horizon_seconds
            ),
            None,
        )
        episode["expanded"] = onset is not None
        episode["expansion_onset_at"] = onset
        episode["lead_seconds"] = round(onset - first_at, 6) if onset is not None else None


def match_raw(oracle: list[dict[str, Any]], rust: list[dict[str, Any]], tolerance_seconds: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    remaining = set(range(len(rust)))
    matches: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for finding in oracle:
        choices = [index for index in remaining if rust[index].get("ticker") == finding.get("ticker")]
        if choices:
            index = min(choices, key=lambda item: abs(float(rust[item]["detected_at"]) - float(finding["detected_at"])))
            delta = float(rust[index]["detected_at"]) - float(finding["detected_at"])
            if abs(delta) <= tolerance_seconds:
                remaining.remove(index)
                matches.append({
                    "ticker": finding["ticker"],
                    "python_at": finding["detected_at"],
                    "rust_at": rust[index]["detected_at"],
                    "delta_seconds": delta,
                    "recipe_score_equal": finding.get("recipe_score") == rust[index].get("recipe_score"),
                })
                continue
        missing.append({"ticker": finding.get("ticker"), "detected_at": finding.get("detected_at")})
    extras = [rust[index] for index in sorted(remaining)]
    return matches, missing, extras


def match_episodes(python_episodes: list[dict[str, Any]], rust_episodes: list[dict[str, Any]], match_seconds: float, simultaneous_seconds: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    remaining = set(range(len(rust_episodes)))
    matches: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for python_episode in python_episodes:
        choices = [index for index in remaining if rust_episodes[index]["ticker"] == python_episode["ticker"]]
        if choices:
            index = min(choices, key=lambda item: abs(float(rust_episodes[item]["first_detected_at"]) - float(python_episode["first_detected_at"])))
            rust_episode = rust_episodes[index]
            delta = float(rust_episode["first_detected_at"]) - float(python_episode["first_detected_at"])
            if abs(delta) <= match_seconds:
                remaining.remove(index)
                if abs(delta) <= simultaneous_seconds:
                    timing = "near_simultaneous"
                elif delta < 0:
                    timing = "rust_earlier"
                else:
                    timing = "python_earlier"
                matches.append({
                    "ticker": python_episode["ticker"],
                    "python_first_at": python_episode["first_detected_at"],
                    "rust_first_at": rust_episode["first_detected_at"],
                    "delta_seconds": round(delta, 6),
                    "timing": timing,
                    "python_detection_count": python_episode["detection_count"],
                    "rust_detection_count": rust_episode["detection_count"],
                    "python_expanded": python_episode.get("expanded"),
                    "rust_expanded": rust_episode.get("expanded"),
                })
                continue
        missing.append(python_episode)
    extras = [rust_episodes[index] for index in sorted(remaining)]
    return matches, missing, extras


def episode_stats(episodes: list[dict[str, Any]]) -> dict[str, Any]:
    counts = [float(item["detection_count"]) for item in episodes]
    durations = [float(item["duration_seconds"]) for item in episodes]
    leads = [float(item["lead_seconds"]) for item in episodes if item.get("lead_seconds") is not None]
    expanded = [item for item in episodes if item.get("expanded")]
    return {
        "episodes": len(episodes),
        "detections": sum(int(item["detection_count"]) for item in episodes),
        "detections_per_episode_median": median(counts) if counts else None,
        "detections_per_episode_p95": percentile(counts, 0.95),
        "detections_per_episode_max": max(counts) if counts else None,
        "duration_seconds_median": median(durations) if durations else None,
        "duration_seconds_p95": percentile(durations, 0.95),
        "followed_by_expansion": len(expanded),
        "false_arm_episodes": len(episodes) - len(expanded),
        "episode_success_rate": round(len(expanded) / len(episodes), 6) if episodes else None,
        "lead_seconds_median": median(leads) if leads else None,
    }


def ticker_breakdown(oracle: list[dict[str, Any]], rust: list[dict[str, Any]], python_episodes: list[dict[str, Any]], rust_episodes: list[dict[str, Any]], episode_matches: list[dict[str, Any]], episode_missing: list[dict[str, Any]], episode_extras: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for item in oracle:
        counts[str(item.get("ticker"))]["python_detections"] += 1
    for item in rust:
        counts[str(item.get("ticker"))]["rust_detections"] += 1
    for item in python_episodes:
        counts[item["ticker"]]["python_episodes"] += 1
    for item in rust_episodes:
        counts[item["ticker"]]["rust_episodes"] += 1
    for item in episode_matches:
        counts[item["ticker"]]["matched_episodes"] += 1
    for item in episode_missing:
        counts[item["ticker"]]["python_only_episodes"] += 1
    for item in episode_extras:
        counts[item["ticker"]]["rust_only_episodes"] += 1
    rows = [{"ticker": ticker, **values} for ticker, values in counts.items() if ticker]
    rows.sort(key=lambda item: (item.get("rust_only_episodes", 0), item.get("rust_detections", 0)), reverse=True)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare Python oracle findings with Rust replay candidates and episode-level outcomes.")
    parser.add_argument("--python-report", type=Path, required=True)
    parser.add_argument("--rust-report", type=Path, required=True)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tolerance-seconds", type=float, default=5.0)
    parser.add_argument("--episode-gap-seconds", type=float, default=900.0)
    parser.add_argument("--episode-match-seconds", type=float, default=900.0)
    parser.add_argument("--expansion-pct", type=float, default=2.0)
    parser.add_argument("--expansion-horizon-seconds", type=float, default=900.0)
    parser.add_argument("--base-window-seconds", type=float, default=300.0)
    parser.add_argument("--diagnostic-samples-per-category", type=int, default=12)
    parser.add_argument("--diagnostic-window-seconds", type=float, default=45.0)
    args = parser.parse_args()

    python_report = json.loads(args.python_report.read_text(encoding="utf-8"))
    rust_report = json.loads(args.rust_report.read_text(encoding="utf-8"))
    oracle = [item for item in python_report.get("findings", []) if item.get("stage") == "PRE_IGNITION"]
    rust = list(rust_report.get("candidates", []))

    raw_matches, raw_missing, raw_extras = match_raw(oracle, rust, args.tolerance_seconds)
    raw_denominator = max(1, len(oracle) + len(raw_extras))
    raw_parity_rate = len(raw_matches) / raw_denominator

    python_episodes = group_episodes(oracle, args.episode_gap_seconds)
    rust_episodes = group_episodes(rust, args.episode_gap_seconds)
    expansions: list[dict[str, Any]] = []
    trade_tape: dict[str, list[tuple[float, float]]] = {}
    if args.dataset:
        expansions = objective_expansions(args.dataset, args.expansion_pct, args.base_window_seconds, args.expansion_horizon_seconds)
        attach_outcomes(python_episodes, expansions, args.expansion_horizon_seconds)
        attach_outcomes(rust_episodes, expansions, args.expansion_horizon_seconds)
        trade_tape = load_trade_tape(args.dataset)
        attach_significance_outcomes(python_episodes, trade_tape)
        attach_significance_outcomes(rust_episodes, trade_tape)

    episode_matches, episode_missing, episode_extras = match_episodes(
        python_episodes,
        rust_episodes,
        args.episode_match_seconds,
        args.tolerance_seconds,
    )
    episode_denominator = max(1, len(python_episodes) + len(episode_extras))
    episode_parity_rate = len(episode_matches) / episode_denominator
    timing_counts = {
        "rust_earlier": sum(1 for item in episode_matches if item["timing"] == "rust_earlier"),
        "python_earlier": sum(1 for item in episode_matches if item["timing"] == "python_earlier"),
        "near_simultaneous": sum(1 for item in episode_matches if item["timing"] == "near_simultaneous"),
    }
    abs_deltas = [abs(float(item["delta_seconds"])) for item in episode_matches]

    event_significance: dict[str, Any] = {
        "available": False,
        "reason": "dataset not supplied",
    }
    if args.dataset:
        event_significance = build_event_significance_report(
            trade_tape,
            python_episodes,
            rust_episodes,
            episode_missing,
            episode_extras,
            args.base_window_seconds,
            args.tolerance_seconds,
        )

    boundary_diagnostics: dict[str, Any] = {
        "available": False,
        "reason": "dataset not supplied",
    }
    if args.dataset and args.diagnostic_samples_per_category > 0:
        targets, diagnostic_groups = build_boundary_targets(
            episode_missing,
            episode_extras,
            episode_matches,
            python_episodes,
            rust_episodes,
            args.diagnostic_samples_per_category,
        )
        snapshots = collect_rust_mirror_snapshots(args.dataset, targets, args.diagnostic_window_seconds)
        boundary_diagnostics = {
            "available": True,
            "samples_per_category": args.diagnostic_samples_per_category,
            "window_seconds": args.diagnostic_window_seconds,
            **attach_boundary_diagnostics(diagnostic_groups, targets, snapshots, oracle, rust),
        }

    bullish_event_evaluation: dict[str, Any] = {
        "available": False,
        "reason": "dataset not supplied",
    }
    if args.dataset:
        bullish_event_evaluation = build_bullish_event_evaluation(
            trade_tape,
            python_episodes,
            rust_episodes,
            args.base_window_seconds,
        )

    early_detection_quality_gate = build_early_detection_quality_gate(
        event_significance,
        bullish_event_evaluation,
    )

    hybrid_intelligence_evaluation = build_hybrid_intelligence_evaluation(
        event_significance,
        bullish_event_evaluation,
        early_detection_quality_gate,
    )

    architecture_freeze_gate = build_architecture_freeze_gate(
        boundary_diagnostics,
        early_detection_quality_gate,
        hybrid_intelligence_evaluation,
        bullish_event_evaluation,
    )

    architecture_decision = build_architecture_decision(event_significance, boundary_diagnostics)

    report = {
        "mode": "SIMULATION",
        "python_engine": python_report.get("replay_engine_version"),
        "rust_engine": rust_report.get("engine"),
        "raw": {
            "tolerance_seconds": args.tolerance_seconds,
            "python_precursors": len(oracle),
            "rust_precursors": len(rust),
            "matched": len(raw_matches),
            "missing_in_rust": len(raw_missing),
            "extra_in_rust": len(raw_extras),
            "parity_rate": round(raw_parity_rate, 6),
        },
        "episodes": {
            "gap_seconds": args.episode_gap_seconds,
            "match_seconds": args.episode_match_seconds,
            "python": episode_stats(python_episodes),
            "rust": episode_stats(rust_episodes),
            "matched": len(episode_matches),
            "missing_in_rust": len(episode_missing),
            "extra_in_rust": len(episode_extras),
            "parity_rate": round(episode_parity_rate, 6),
            "timing": timing_counts,
            "absolute_first_detection_delta_seconds_median": median(abs_deltas) if abs_deltas else None,
            "absolute_first_detection_delta_seconds_p95": percentile(abs_deltas, 0.95),
        },
        "outcomes": {
            "available": bool(args.dataset),
            "definition": {
                "expansion_pct": args.expansion_pct,
                "horizon_seconds": args.expansion_horizon_seconds,
                "base_window_seconds": args.base_window_seconds,
            },
            "objective_expansion_episodes": len(expansions),
        },
        "boundary_diagnostics": boundary_diagnostics,
        "event_significance": event_significance,
        "bullish_event_evaluation": bullish_event_evaluation,
        "early_detection_quality_gate": early_detection_quality_gate,
        "hybrid_intelligence_evaluation": hybrid_intelligence_evaluation,
        "architecture_freeze_gate": architecture_freeze_gate,
        "architecture_decision": architecture_decision,
        "production_cutover_ready": bool(oracle)
        and raw_parity_rate == 1.0
        and not raw_missing
        and not raw_extras
        and all(item["recipe_score_equal"] for item in raw_matches),
        "ticker_breakdown": ticker_breakdown(oracle, rust, python_episodes, rust_episodes, episode_matches, episode_missing, episode_extras),
        "raw_matches": raw_matches,
        "raw_missing": raw_missing,
        "raw_extras": raw_extras,
        "episode_matches": episode_matches,
        "python_episodes": python_episodes,
        "rust_episodes": rust_episodes,
        "python_only_episodes": episode_missing,
        "rust_only_episodes": episode_extras,
        "objective_expansions": expansions,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    summary = {
        "mode": report["mode"],
        "python_engine": report["python_engine"],
        "rust_engine": report["rust_engine"],
        "raw": report["raw"],
        "episodes": report["episodes"],
        "outcomes": report["outcomes"],
        "event_significance": {
            "available": event_significance.get("available"),
            "profiles": {
                name: {
                    "definition": profile.get("definition"),
                    "detector_episode_precision": profile.get("detector_episode_precision"),
                    "objective_move_coverage": {
                        key: value
                        for key, value in (profile.get("objective_move_coverage") or {}).items()
                        if key != "examples"
                    },
                    "rust_only_successful_discoveries": {
                        "count": (profile.get("rust_only_successful_discoveries") or {}).get("count"),
                    },
                    "python_only_successful_discoveries": {
                        "count": (profile.get("python_only_successful_discoveries") or {}).get("count"),
                    },
                }
                for name, profile in (event_significance.get("profiles") or {}).items()
            },
        },
        "bullish_event_evaluation": {
            "available": bullish_event_evaluation.get("available"),
            "methodology": bullish_event_evaluation.get("methodology"),
            "overall": bullish_event_evaluation.get("overall"),
            "by_magnitude": bullish_event_evaluation.get("by_magnitude"),
            "by_morphology": bullish_event_evaluation.get("by_morphology"),
            "by_session_phase": bullish_event_evaluation.get("by_session_phase"),
        },
        "early_detection_quality_gate": early_detection_quality_gate,
        "hybrid_intelligence_evaluation": hybrid_intelligence_evaluation,
        "architecture_freeze_gate": architecture_freeze_gate,
        "boundary_diagnostics": {
            "available": boundary_diagnostics.get("available"),
            "samples_per_category": boundary_diagnostics.get("samples_per_category"),
            "window_seconds": boundary_diagnostics.get("window_seconds"),
            "mirror_verification": boundary_diagnostics.get("mirror_verification"),
            "python_only_sample_summary": boundary_diagnostics.get("python_only_sample_summary"),
            "matched_large_delta_state_summary": boundary_diagnostics.get("matched_large_delta_state_summary"),
            "python_only_samples": len(boundary_diagnostics.get("python_only", [])),
            "rust_only_samples": len(boundary_diagnostics.get("rust_only", [])),
            "matched_large_delta_samples": len(boundary_diagnostics.get("matched_large_delta", [])),
        },
        "architecture_decision": architecture_decision,
        "production_cutover_ready": report["production_cutover_ready"],
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()