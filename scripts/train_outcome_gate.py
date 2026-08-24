#!/usr/bin/env python3
"""Train a shadow precision gate from Scout's own captured forward-price outcomes.

Unlike scripts/train_imminent_alert_gate.py (which needs replayed tick data and
a narrow 15-30s completion window), this trains directly against the
`outcomes` table that Scout already populates for essentially every finding
(max_1m_pct / max_5m_pct / max_15m_pct / max_session_pct). The label is
whether the finding's forward price actually reached a meaningful expansion,
which is closer to "was this worth acting on" than a fixed short-horizon proxy.

The output artifact reuses the shared feature contract (app.learning_features)
so it is a drop-in replacement for the existing shadow gate consumed by
app/imminent_gate.py::score_finding. Nothing here touches alert delivery.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score, roc_auc_score

from app.learning_features import FEATURES, feature_vector

QUERY = """
SELECT
    f.id, f.ticker, f.stage, f.detected_at, f.price, f.score,
    f.vol_ratio_15s, f.vol_ratio_30s, f.change_60s_pct, f.extension_pct,
    f.ema9_slope, f.vwap, f.above_vwap, f.quiet_break,
    f.change_3s_pct, f.change_5s_pct, f.change_10s_pct, f.change_15s_pct, f.change_30s_pct,
    f.accel_15s_pp, f.dollar_volume_15s, f.dollar_volume_30s, f.trades_15s, f.trades_30s,
    f.quality_label, f.quality_score, f.actionable_rank, f.directional_efficiency,
    f.active_bucket_ratio, f.direction_reversals, f.float_turnover, f.candidate_profile_json,
    f.engine_source, f.shadow_mode,
    o.max_1m_pct, o.max_5m_pct, o.max_15m_pct, o.max_session_pct
FROM findings f
JOIN outcomes o ON o.finding_id = f.id
WHERE o.max_5m_pct IS NOT NULL
"""


def load_rows(db_path: Path) -> list[dict]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = [dict(row) for row in con.execute(QUERY)]
    finally:
        con.close()
    for row in rows:
        try:
            row["candidate_profile"] = json.loads(row.pop("candidate_profile_json") or "{}")
        except (TypeError, ValueError):
            row["candidate_profile"] = {}
        row["above_vwap"] = bool(row["above_vwap"])
        row["quiet_break"] = bool(row["quiet_break"])
        row["date"] = datetime.fromtimestamp(float(row["detected_at"]), tz=timezone.utc).strftime("%Y-%m-%d")
    return rows


def label_rows(rows: list[dict], *, label_field: str, expansion_pct: float, actionable_only: bool) -> list[dict]:
    selected = [row for row in rows if not actionable_only or not row["shadow_mode"]]
    for row in selected:
        row["label"] = int(float(row[label_field] or 0.0) >= expansion_pct)
    return selected


def load_extra_jsonl(paths: list[str]) -> list[dict]:
    """Load pre-labeled rows (e.g. from scripts.label_backtest_outcomes) that already
    match load_rows()'s output shape, so they can be pooled with live DB rows."""
    rows: list[dict] = []
    for path in paths:
        with open(path, encoding="utf-8") as handle:
            rows.extend(json.loads(line) for line in handle if line.strip())
    return rows


def split_metrics(labels: np.ndarray, probability: np.ndarray, threshold: float) -> dict:
    predicted = probability >= threshold
    tp = int(np.sum(predicted & (labels == 1)))
    fp = int(np.sum(predicted & (labels == 0)))
    fn = int(np.sum(~predicted & (labels == 1)))
    return {
        "alerts": int(len(labels)), "positive_alerts": int(np.sum(labels)), "alerts_passed": int(np.sum(predicted)),
        "true_positives": tp, "false_positives": fp, "false_negatives": fn,
        "precision": tp / (tp + fp) if tp + fp else None,
        "alert_recall": tp / (tp + fn) if tp + fn else None,
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
    parser = argparse.ArgumentParser(description="Train a shadow gate over Scout's own captured outcomes")
    parser.add_argument("--db", default="data/state.db")
    parser.add_argument("--extra-jsonl", action="append", default=[], help="Pre-labeled rows (e.g. from scripts.label_backtest_outcomes) to pool with live DB rows")
    parser.add_argument("--model", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--label-field", default="max_5m_pct", choices=["max_1m_pct", "max_5m_pct", "max_15m_pct", "max_session_pct"])
    parser.add_argument("--expansion-pct", type=float, default=3.0, help="Forward move (percent) required for a positive label")
    parser.add_argument("--actionable-only", action="store_true", help="Train only on non-shadow-mode findings")
    parser.add_argument("--target-precision", type=float, default=0.4)
    parser.add_argument("--min-validation-alerts", type=int, default=30)
    parser.add_argument("--min-training-rows", type=int, default=200)
    args = parser.parse_args()

    rows = label_rows(
        load_rows(Path(args.db)) + load_extra_jsonl(args.extra_jsonl), label_field=args.label_field,
        expansion_pct=args.expansion_pct, actionable_only=args.actionable_only,
    )
    dates = sorted({row["date"] for row in rows})
    if len(dates) < 3:
        raise SystemExit("At least three distinct dates are required for train/validation/test separation")
    train_dates, validation_date, test_date = dates[:-2], dates[-2], dates[-1]

    def matrix(chosen: set[str]):
        subset = [row for row in rows if row["date"] in chosen]
        x = np.asarray([feature_vector(row) for row in subset]) if subset else np.zeros((0, len(FEATURES)))
        y = np.asarray([row["label"] for row in subset])
        return subset, x, y

    train_rows, train_x, train_y = matrix(set(train_dates))
    validation_rows, validation_x, validation_y = matrix({validation_date})
    test_rows, test_x, test_y = matrix({test_date})
    if len(train_rows) < args.min_training_rows:
        raise SystemExit(f"At least {args.min_training_rows} matured training rows are required, found {len(train_rows)}")
    if len(set(train_y.tolist())) < 2:
        raise SystemExit("Training data must contain both positive and negative outcomes")

    model = HistGradientBoostingClassifier(
        learning_rate=0.05, max_iter=250, max_leaf_nodes=15, min_samples_leaf=30,
        l2_regularization=2.0, class_weight="balanced", random_state=23,
    )
    model.fit(train_x, train_y)
    validation_probability = model.predict_proba(validation_x)[:, 1]
    threshold, target_met = choose_threshold(
        validation_y, validation_probability, args.target_precision, args.min_validation_alerts,
    )
    test_probability = model.predict_proba(test_x)[:, 1]

    report = {
        "shadow_only": True, "features": list(FEATURES), "db": args.db,
        "label_field": args.label_field, "expansion_pct": args.expansion_pct, "actionable_only": args.actionable_only,
        "train_dates": train_dates, "validation_date": validation_date, "test_date": test_date,
        "target_precision": args.target_precision, "min_validation_alerts": args.min_validation_alerts,
        "selected_threshold": threshold, "threshold_target_met": target_met,
        "train": {"rows": len(train_rows), "positive_rows": int(np.sum(train_y))},
        "validation": split_metrics(validation_y, validation_probability, threshold),
        "test": split_metrics(test_y, test_probability, threshold),
    }

    model_path = Path(args.model)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    artifact = {
        "model": model, "features": FEATURES, "threshold": threshold,
        "shadow_only": True, "train_dates": train_dates,
        "validation_date": validation_date, "test_date": test_date,
        "label_field": args.label_field, "expansion_pct": args.expansion_pct,
        "training_rows": len(train_rows), "test_date_trained": test_date,
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
