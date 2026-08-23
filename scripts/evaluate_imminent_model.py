#!/usr/bin/env python3
"""Evaluate a locked imminent model on a completely untouched dataset."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import joblib
import numpy as np

from scripts.imminent_move_scorer import load_jsonl
from scripts.train_imminent_model import MODEL_FEATURES, feature_vector, metrics


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate a locked imminent model without threshold retuning")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()

    rows = load_jsonl(Path(args.dataset))
    artifact = joblib.load(args.model)
    features = tuple(artifact.get("features") or ())
    if features != MODEL_FEATURES:
        raise SystemExit(f"Model feature contract mismatch: expected {len(MODEL_FEATURES)}, got {len(features)}")
    x = np.asarray([feature_vector(row) for row in rows], dtype=float)
    y = np.asarray([int(row["label"]) for row in rows], dtype=int)
    probability = artifact["model"].predict_proba(x)[:, 1]
    threshold = float(artifact["threshold"])
    result = {
        "locked_model": str(Path(args.model)),
        "dates": sorted({str(row["date"]) for row in rows}),
        "threshold": threshold,
        "features": list(features),
        "metrics": metrics(
            rows, y, probability, threshold,
            cooldown_seconds=float(artifact.get("notification_cooldown_seconds", 30.0)),
            min_consecutive_samples=int(artifact.get("min_consecutive_samples", 1)),
        ),
    }
    report = Path(args.report)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
