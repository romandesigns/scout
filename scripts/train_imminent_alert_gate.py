#!/usr/bin/env python3
"""Train a chronological precision gate over Scout's own actionable findings."""
from __future__ import annotations

import argparse
import json
from bisect import bisect_left, bisect_right
from collections import defaultdict
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score, roc_auc_score

from app.learning_features import FEATURES, feature_vector
from scripts.imminent_move_scorer import is_actionable, load_jsonl, load_trades, objective_moves


def labeled_alerts(replay_rows: list[dict], cache_dir: Path, *, feed: str = "sip") -> list[dict]:
    alerts: list[dict] = []
    for replay in replay_rows:
        ticker, session_date = str(replay["ticker"]).upper(), str(replay["date"])
        dataset = cache_dir / f"{ticker}-{session_date}-{feed}.ndjson"
        if not dataset.exists():
            continue
        trades = load_trades(dataset)
        moves = [
            move for move in objective_moves(ticker, trades)
            if float(move["duration_seconds"]) >= 15.0
        ]
        completion_times = [float(move["completed_at"]) for move in moves]
        for finding in replay.get("findings") or []:
            if not is_actionable(finding):
                continue
            detected_at = float(finding["detected_at"])
            first = bisect_left(completion_times, detected_at + 15.0)
            last = bisect_right(completion_times, detected_at + 30.0)
            matched = next(
                (move for move in moves[first:last] if detected_at >= float(move["base_at"])),
                None,
            )
            entry = float(finding.get("price") or 0.0)
            horizon = [(ts, price) for ts, price in trades if detected_at <= ts <= detected_at + 300.0]
            target_price = entry * 1.02
            invalidation_price = float(finding.get("invalidation_level") or (entry * 0.99))
            target_at = next((ts for ts, price in horizon if price >= target_price), None)
            invalidated_at = next((ts for ts, price in horizon if price <= invalidation_price), None)
            target_before_invalidation = bool(target_at is not None and (invalidated_at is None or target_at < invalidated_at))
            peak = max((price for _, price in horizon), default=entry)
            trough = min((price for _, price in horizon), default=entry)
            alerts.append({
                **finding, "ticker": ticker, "date": session_date,
                "label": int(matched is not None),
                "target_completion_at": float(matched["completed_at"]) if matched else None,
                "target_base_at": float(matched["base_at"]) if matched else None,
                "target_base_price": float(matched["base_price"]) if matched else None,
                "target_completion_price": float(matched["completed_price"]) if matched else None,
                # Path-aware research labels.  They are reported alongside the
                # strict 15-30s target and never fed back into detection time.
                "target_before_invalidation": int(target_before_invalidation),
                "target_at": target_at, "invalidated_at": invalidated_at,
                "max_favorable_5m_pct": ((peak - entry) / entry * 100.0) if entry else 0.0,
                "max_adverse_5m_pct": ((trough - entry) / entry * 100.0) if entry else 0.0,
            })
    return alerts


def split_metrics(rows: list[dict], labels: np.ndarray, probability: np.ndarray, threshold: float) -> dict:
    predicted = probability >= threshold
    tp = int(np.sum(predicted & (labels == 1)))
    fp = int(np.sum(predicted & (labels == 0)))
    fn = int(np.sum(~predicted & (labels == 1)))
    moves = {(row["ticker"], row["date"], row["target_completion_at"]) for row in rows if row["target_completion_at"] is not None}
    caught = {
        (rows[index]["ticker"], rows[index]["date"], rows[index]["target_completion_at"])
        for index in np.flatnonzero(predicted) if rows[index]["target_completion_at"] is not None
    }
    return {
        "alerts": len(rows), "positive_alerts": int(np.sum(labels)), "alerts_passed": int(np.sum(predicted)),
        "true_positives": tp, "false_positives": fp, "false_negatives": fn,
        "precision": tp / (tp + fp) if tp + fp else None,
        "alert_recall": tp / (tp + fn) if tp + fn else None,
        "objective_moves_represented": len(moves), "objective_moves_caught": len(caught),
        "represented_move_recall": len(caught) / len(moves) if moves else None,
        "average_precision": average_precision_score(labels, probability) if len(set(labels.tolist())) > 1 else None,
        "roc_auc": roc_auc_score(labels, probability) if len(set(labels.tolist())) > 1 else None,
    }


def choose_threshold(labels: np.ndarray, probability: np.ndarray, target_precision: float, min_alerts: int) -> tuple[float, bool]:
    choices = []
    for threshold in sorted(set(float(value) for value in probability)):
        predicted = probability >= threshold
        count = int(np.sum(predicted))
        if count < min_alerts:
            continue
        tp = int(np.sum(predicted & (labels == 1)))
        precision = tp / count
        recall = tp / max(1, int(np.sum(labels)))
        choices.append((threshold, precision, recall, tp, count))
    eligible = [item for item in choices if item[1] >= target_precision]
    pool = eligible or choices
    if not pool:
        return 1.0, False
    selected = max(pool, key=lambda item: (item[2], item[1], item[3], item[0]) if eligible else (item[1], item[2], item[3], item[0]))
    return selected[0], bool(eligible)


def main() -> int:
    parser = argparse.ArgumentParser(description="Train a time-split Scout imminent-alert precision gate")
    parser.add_argument(
        "--findings", required=True, action="append",
        help="Replay findings JSONL; repeat to train across multiple historical batches",
    )
    parser.add_argument("--cache-dir", default="data/replay-datasets/backtest")
    parser.add_argument("--model", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--target-precision", type=float, default=0.15)
    parser.add_argument("--min-validation-alerts", type=int, default=5)
    parser.add_argument("--min-training-alerts", type=int, default=30)
    args = parser.parse_args()

    replay_rows = []
    for findings_path in args.findings:
        replay_rows.extend(load_jsonl(Path(findings_path)))
    rows = labeled_alerts(replay_rows, Path(args.cache_dir))
    dates = sorted({str(row["date"]) for row in rows})
    if len(dates) < 3:
        raise SystemExit("At least three dates are required")
    train_dates, validation_date, test_date = dates[:-2], dates[-2], dates[-1]

    def matrix(chosen: set[str]):
        subset = [row for row in rows if row["date"] in chosen]
        return subset, np.asarray([feature_vector(row) for row in subset]), np.asarray([row["label"] for row in subset])

    train_rows, train_x, train_y = matrix(set(train_dates))
    validation_rows, validation_x, validation_y = matrix({validation_date})
    test_rows, test_x, test_y = matrix({test_date})
    if len(train_rows) < args.min_training_alerts:
        raise SystemExit(f"At least {args.min_training_alerts} matured training alerts are required")
    if len(set(train_y.tolist())) < 2:
        raise SystemExit("Training data must contain both successful and unsuccessful alerts")
    model = HistGradientBoostingClassifier(
        learning_rate=0.04, max_iter=200, max_leaf_nodes=7, min_samples_leaf=12,
        l2_regularization=3.0, class_weight="balanced", random_state=23,
    )
    model.fit(train_x, train_y)
    validation_probability = model.predict_proba(validation_x)[:, 1]
    threshold, target_met = choose_threshold(
        validation_y, validation_probability, args.target_precision, args.min_validation_alerts,
    )
    test_probability = model.predict_proba(test_x)[:, 1]
    path_rows = [row for row in test_rows if "target_before_invalidation" in row]
    report = {
        "shadow_only": True, "features": list(FEATURES), "findings": args.findings,
        "train_dates": train_dates,
        "validation_date": validation_date, "test_date": test_date,
        "target_precision": args.target_precision, "min_validation_alerts": args.min_validation_alerts,
        "selected_threshold": threshold, "threshold_target_met": target_met,
        "train": {"alerts": len(train_rows), "positive_alerts": int(np.sum(train_y))},
        "validation": split_metrics(validation_rows, validation_y, validation_probability, threshold),
        "test": split_metrics(test_rows, test_y, test_probability, threshold),
        "path_outcomes": {
            "test_alerts": len(path_rows),
            "target_before_invalidation": sum(row["target_before_invalidation"] for row in path_rows),
            "rate": (sum(row["target_before_invalidation"] for row in path_rows) / len(path_rows)) if path_rows else None,
            "mean_max_favorable_5m_pct": (sum(row["max_favorable_5m_pct"] for row in path_rows) / len(path_rows)) if path_rows else None,
            "mean_max_adverse_5m_pct": (sum(row["max_adverse_5m_pct"] for row in path_rows) / len(path_rows)) if path_rows else None,
        },
    }
    model_path = Path(args.model)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    artifact = {
        "model": model, "features": FEATURES, "threshold": threshold,
        "shadow_only": True, "train_dates": train_dates,
        "validation_date": validation_date, "test_date": test_date,
        "training_alerts": len(train_rows),
    }
    temporary_model = model_path.with_suffix(model_path.suffix + ".tmp")
    joblib.dump(artifact, temporary_model)
    temporary_model.replace(model_path)
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
