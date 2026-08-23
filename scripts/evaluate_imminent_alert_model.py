#!/usr/bin/env python3
"""Evaluate a locked Scout-finding precision gate without threshold retuning."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import joblib
import numpy as np

from scripts.imminent_move_scorer import load_jsonl
from scripts.train_imminent_alert_gate import FEATURES, feature_vector, labeled_alerts, split_metrics


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate a locked Scout imminent-alert gate")
    parser.add_argument(
        "--findings", required=True, action="append",
        help="Replay findings JSONL; repeat for deterministic replay shards",
    )
    parser.add_argument("--cache-dir", default="data/replay-datasets/backtest")
    parser.add_argument("--model", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()

    replay_rows = []
    for findings_path in args.findings:
        replay_rows.extend(load_jsonl(Path(findings_path)))
    rows = labeled_alerts(replay_rows, Path(args.cache_dir))
    artifact = joblib.load(args.model)
    if tuple(artifact.get("features") or ()) != FEATURES:
        raise SystemExit("Model feature contract mismatch")
    x = np.asarray([feature_vector(row) for row in rows], dtype=float)
    y = np.asarray([row["label"] for row in rows], dtype=int)
    probability = artifact["model"].predict_proba(x)[:, 1]
    threshold = float(artifact["threshold"])
    result = {
        "model": str(Path(args.model)), "dates": sorted({row["date"] for row in rows}),
        "threshold": threshold, "metrics": split_metrics(rows, y, probability, threshold),
    }
    report = Path(args.report)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
