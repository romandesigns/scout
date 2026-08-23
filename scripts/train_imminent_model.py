#!/usr/bin/env python3
"""Train and time-split-validate a shadow imminent-move classifier."""
from __future__ import annotations

import argparse
import json
import math
import random
from collections import defaultdict
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score, roc_auc_score

from scripts.build_imminent_training_data import FEATURES
from scripts.imminent_move_scorer import load_jsonl


DERIVED_FEATURES = (
    "log_price", "log_trades_5s", "log_trades_15s", "log_trades_30s", "log_trades_60s",
    "log_dollar_5s", "log_dollar_15s", "log_dollar_30s", "log_dollar_60s",
    "trade_rate_ratio_5v15", "trade_rate_ratio_15v60",
    "dollar_rate_ratio_5v15", "dollar_rate_ratio_15v60",
    "quote_rate_ratio_5v15", "return_15v30_pp", "return_30v60_pp",
    "utc_time_sin", "utc_time_cos",
)
MODEL_FEATURES = FEATURES + DERIVED_FEATURES


def feature_vector(row: dict) -> list[float]:
    value = {name: float(row.get(name) or 0.0) for name in FEATURES}
    seconds = float(row.get("sample_at") or 0.0) % 86400.0
    angle = 2.0 * math.pi * seconds / 86400.0

    def ratio(short: str, long: str, multiplier: float) -> float:
        return value[short] * multiplier / max(1.0, value[long])

    derived = {
        "log_price": math.log1p(max(0.0, float(row.get("price") or 0.0))),
        **{f"log_trades_{window}": math.log1p(max(0.0, value[f"trades_{window}"])) for window in ("5s", "15s", "30s", "60s")},
        **{f"log_dollar_{window}": math.log1p(max(0.0, value[f"dollar_{window}"])) for window in ("5s", "15s", "30s", "60s")},
        "trade_rate_ratio_5v15": ratio("trades_5s", "trades_15s", 3.0),
        "trade_rate_ratio_15v60": ratio("trades_15s", "trades_60s", 4.0),
        "dollar_rate_ratio_5v15": ratio("dollar_5s", "dollar_15s", 3.0),
        "dollar_rate_ratio_15v60": ratio("dollar_15s", "dollar_60s", 4.0),
        "quote_rate_ratio_5v15": ratio("quote_updates_5s", "quote_updates_15s", 3.0),
        "return_15v30_pp": value["return_15s_pct"] - value["return_30s_pct"],
        "return_30v60_pp": value["return_30s_pct"] - value["return_60s_pct"],
        "utc_time_sin": math.sin(angle),
        "utc_time_cos": math.cos(angle),
    }
    return [value[name] for name in FEATURES] + [derived[name] for name in DERIVED_FEATURES]


def notification_indices(
    rows: list[dict], predicted: np.ndarray, *, cooldown_seconds: float = 30.0,
    min_consecutive_samples: int = 1,
) -> list[int]:
    """Collapse dense 5-second predictions into notifications users would receive."""
    ordered = sorted(
        range(len(rows)),
        key=lambda index: (
            str(rows[index]["ticker"]), str(rows[index]["date"]),
            float(rows[index]["sample_at"]),
        ),
    )
    alerts: list[int] = []
    last_alert: dict[tuple[str, str], float] = defaultdict(lambda: float("-inf"))
    active: dict[tuple[str, str], bool] = defaultdict(bool)
    consecutive: dict[tuple[str, str], int] = defaultdict(int)
    for index in ordered:
        key = (str(rows[index]["ticker"]), str(rows[index]["date"]))
        current = bool(predicted[index])
        at = float(rows[index]["sample_at"])
        consecutive[key] = consecutive[key] + 1 if current else 0
        confirmed = consecutive[key] >= min_consecutive_samples
        if confirmed and not active[key] and at - last_alert[key] >= cooldown_seconds:
            alerts.append(index)
            last_alert[key] = at
        active[key] = confirmed
    return alerts


def notification_metrics(
    rows: list[dict], y: np.ndarray, probability: np.ndarray, threshold: float,
    *, cooldown_seconds: float = 30.0,
    min_consecutive_samples: int = 1,
) -> dict:
    predicted = probability >= threshold
    episode_alerts = notification_indices(
        rows, predicted, cooldown_seconds=cooldown_seconds,
        min_consecutive_samples=min_consecutive_samples,
    )
    episode_tp = sum(int(y[index]) for index in episode_alerts)
    all_moves = {
        (str(row["ticker"]), str(row["date"]), float(row["target_completion_at"]))
        for row in rows if row.get("target_completion_at") is not None
    }
    caught_moves = {
        (str(rows[index]["ticker"]), str(rows[index]["date"]), float(rows[index]["target_completion_at"]))
        for index in episode_alerts if rows[index].get("target_completion_at") is not None
    }
    return {
        "notification_episodes": len(episode_alerts),
        "notification_true_positives": episode_tp,
        "notification_precision": episode_tp / len(episode_alerts) if episode_alerts else None,
        "objective_moves": len(all_moves),
        "objective_moves_caught": len(caught_moves),
        "objective_move_recall": len(caught_moves) / len(all_moves) if all_moves else None,
    }


def select_threshold(
    rows: list[dict], y: np.ndarray, probability: np.ndarray, *,
    target_precision: float, min_notifications: int, cooldown_seconds: float,
    min_consecutive_samples: int = 1,
) -> tuple[float, dict, bool]:
    """Choose on user-visible notifications, with enough support to resist overfit."""
    if len(probability) == 0:
        raise ValueError("validation set is empty")
    quantiles = np.linspace(0.0, 1.0, min(501, len(probability)))
    candidates = sorted(set(float(value) for value in np.quantile(probability, quantiles)))
    evaluated: list[tuple[float, dict]] = []
    for threshold in candidates:
        result = notification_metrics(
            rows, y, probability, threshold, cooldown_seconds=cooldown_seconds,
            min_consecutive_samples=min_consecutive_samples,
        )
        if result["notification_episodes"] >= min_notifications:
            evaluated.append((threshold, result))
    if not evaluated:
        # Tiny validation sets still need a deterministic diagnostic threshold,
        # but the report will explicitly state that the support gate failed.
        threshold = float(np.quantile(probability, 0.99))
        result = notification_metrics(
            rows, y, probability, threshold, cooldown_seconds=cooldown_seconds,
            min_consecutive_samples=min_consecutive_samples,
        )
        return threshold, result, False
    eligible = [
        item for item in evaluated
        if float(item[1]["notification_precision"] or 0.0) >= target_precision
    ]
    pool = eligible or evaluated
    threshold, result = max(
        pool,
        key=lambda item: (
            float(item[1]["objective_move_recall"] or 0.0),
            float(item[1]["notification_precision"] or 0.0),
            item[1]["notification_true_positives"],
            item[0],
        ) if eligible else (
            float(item[1]["notification_precision"] or 0.0),
            float(item[1]["objective_move_recall"] or 0.0),
            item[1]["notification_true_positives"],
            item[0],
        ),
    )
    return threshold, result, bool(eligible)


def metrics(
    rows: list[dict], y: np.ndarray, probability: np.ndarray, threshold: float,
    *, cooldown_seconds: float = 30.0,
    min_consecutive_samples: int = 1,
) -> dict:
    predicted = probability >= threshold
    tp = int(np.sum(predicted & (y == 1)))
    fp = int(np.sum(predicted & (y == 0)))
    fn = int(np.sum(~predicted & (y == 1)))
    result = {
        "rows": int(len(y)), "positives": int(np.sum(y)), "alerts": int(np.sum(predicted)),
        "true_positives": tp, "false_positives": fp, "false_negatives": fn,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
        "average_precision": average_precision_score(y, probability) if len(set(y.tolist())) > 1 else None,
        "roc_auc": roc_auc_score(y, probability) if len(set(y.tolist())) > 1 else None,
    }
    result.update(notification_metrics(
        rows, y, probability, threshold, cooldown_seconds=cooldown_seconds,
        min_consecutive_samples=min_consecutive_samples,
    ))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Train a shadow imminent-move model with chronological validation")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--target-precision", type=float, default=0.25)
    parser.add_argument("--train-negative-ratio", type=int, default=10)
    parser.add_argument("--min-validation-notifications", type=int, default=25)
    parser.add_argument("--notification-cooldown-seconds", type=float, default=30.0)
    parser.add_argument("--min-consecutive-samples", type=int, default=2)
    args = parser.parse_args()
    rows = load_jsonl(Path(args.dataset))
    dates = sorted({str(row["date"]) for row in rows})
    if len(dates) < 3:
        raise SystemExit("At least three distinct dates are required for train/validation/test separation")
    train_dates, validation_date, test_date = dates[:-2], dates[-2], dates[-1]

    def matrix(chosen: set[str]):
        subset = [row for row in rows if str(row["date"]) in chosen]
        x = np.asarray([feature_vector(row) for row in subset], dtype=float)
        y = np.asarray([int(row["label"]) for row in subset], dtype=int)
        return subset, x, y

    train_rows = [row for row in rows if str(row["date"]) in set(train_dates)]
    train_positive = [row for row in train_rows if int(row["label"]) == 1]
    train_negative = [row for row in train_rows if int(row["label"]) == 0]
    random.Random(17).shuffle(train_negative)
    if args.train_negative_ratio > 0:
        train_negative = train_negative[:args.train_negative_ratio * len(train_positive)]
    fitted_rows = train_positive + train_negative
    train_x = np.asarray([feature_vector(row) for row in fitted_rows], dtype=float)
    train_y = np.asarray([int(row["label"]) for row in fitted_rows], dtype=int)
    validation_rows, validation_x, validation_y = matrix({validation_date})
    test_rows, test_x, test_y = matrix({test_date})
    model = HistGradientBoostingClassifier(
        learning_rate=0.06, max_iter=250, max_leaf_nodes=15, min_samples_leaf=40,
        l2_regularization=1.0, class_weight="balanced", random_state=17,
    )
    model.fit(train_x, train_y)
    validation_probability = model.predict_proba(validation_x)[:, 1]
    threshold, validation_notifications, target_met = select_threshold(
        validation_rows, validation_y, validation_probability,
        target_precision=args.target_precision,
        min_notifications=args.min_validation_notifications,
        cooldown_seconds=args.notification_cooldown_seconds,
        min_consecutive_samples=args.min_consecutive_samples,
    )
    test_probability = model.predict_proba(test_x)[:, 1]
    report = {
        "shadow_only": True, "features": list(MODEL_FEATURES), "train_dates": train_dates,
        "validation_date": validation_date, "test_date": test_date,
        "target_precision": args.target_precision, "selected_threshold": threshold,
        "threshold_target_met": target_met,
        "min_validation_notifications": args.min_validation_notifications,
        "notification_cooldown_seconds": args.notification_cooldown_seconds,
        "min_consecutive_samples": args.min_consecutive_samples,
        "threshold_validation_notifications": validation_notifications,
        "train_rows": len(train_y), "train_positives": int(np.sum(train_y)),
        "train_negative_ratio": args.train_negative_ratio,
        "validation": metrics(
            validation_rows, validation_y, validation_probability, threshold,
            cooldown_seconds=args.notification_cooldown_seconds,
            min_consecutive_samples=args.min_consecutive_samples,
        ),
        "test": metrics(
            test_rows, test_y, test_probability, threshold,
            cooldown_seconds=args.notification_cooldown_seconds,
            min_consecutive_samples=args.min_consecutive_samples,
        ),
    }
    model_path = Path(args.model)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({
        "model": model, "features": MODEL_FEATURES, "threshold": threshold,
        "notification_cooldown_seconds": args.notification_cooldown_seconds,
        "min_consecutive_samples": args.min_consecutive_samples,
    }, model_path)
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
