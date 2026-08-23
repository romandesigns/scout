#!/usr/bin/env python3
"""Measure a locked imminent model as a confirmation gate on Scout A/B alerts."""
from __future__ import annotations

import argparse
import json
from bisect import bisect_right
from collections import defaultdict
from pathlib import Path

import joblib
import numpy as np

from scripts.imminent_move_scorer import load_jsonl, score
from scripts.train_imminent_model import MODEL_FEATURES, feature_vector


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate imminent-model confirmation of Scout alerts")
    parser.add_argument("--findings", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--cache-dir", default="data/replay-datasets/backtest")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    replay_rows = load_jsonl(Path(args.findings))
    feature_rows = load_jsonl(Path(args.dataset))
    artifact = joblib.load(args.model)
    if tuple(artifact.get("features") or ()) != MODEL_FEATURES:
        raise SystemExit("Model feature contract mismatch")
    probability = artifact["model"].predict_proba(
        np.asarray([feature_vector(row) for row in feature_rows], dtype=float)
    )[:, 1]
    grouped: dict[tuple[str, str], list[tuple[float, float]]] = defaultdict(list)
    for row, value in zip(feature_rows, probability, strict=True):
        grouped[(str(row["ticker"]).upper(), str(row["date"]))].append(
            (float(row["sample_at"]), float(value))
        )
    for values in grouped.values():
        values.sort()

    threshold = float(artifact["threshold"])
    required = int(artifact.get("min_consecutive_samples", 1))
    input_alerts = passed_alerts = uncovered_alerts = 0
    filtered_rows: list[dict] = []
    decisions: list[dict] = []
    for replay in replay_rows:
        key = (str(replay["ticker"]).upper(), str(replay["date"]))
        values = grouped.get(key) or []
        times = [item[0] for item in values]
        kept = []
        for finding in replay.get("findings") or []:
            if str(finding.get("actionable_rank") or "C").upper() not in {"A", "B"}:
                kept.append(finding)
                continue
            input_alerts += 1
            index = bisect_right(times, float(finding["detected_at"])) - 1
            covered = index >= required - 1 and float(finding["detected_at"]) - times[index] <= 6.0
            window = values[index - required + 1:index + 1] if covered else []
            passed = bool(covered and len(window) == required and all(value >= threshold for _, value in window))
            if not covered:
                uncovered_alerts += 1
            if passed:
                kept.append(finding)
                passed_alerts += 1
            decisions.append({
                "ticker": key[0], "date": key[1], "detected_at": finding["detected_at"],
                "rank": finding.get("actionable_rank"), "covered": covered,
                "probabilities": [value for _, value in window], "threshold": threshold,
                "passed": passed,
            })
        filtered_rows.append({**replay, "findings": kept})

    report = score(filtered_rows, Path(args.cache_dir))
    result = {
        "model": str(Path(args.model)), "threshold": threshold,
        "required_consecutive_samples": required,
        "input_actionable_alerts": input_alerts, "passed_actionable_alerts": passed_alerts,
        "uncovered_actionable_alerts": uncovered_alerts,
        "gated_report": report, "decisions": decisions,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key not in {"gated_report", "decisions"}}, indent=2))
    print(json.dumps({key: report[key] for key in ("objective_moves", "moves_hit", "recall", "actionable_findings", "actionable_findings_matched", "strict_window_precision")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
