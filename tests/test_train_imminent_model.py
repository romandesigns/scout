import numpy as np

from scripts.train_imminent_model import feature_vector, notification_indices, select_threshold


def rows(labels):
    return [
        {
            "ticker": "TEST", "date": "2026-08-21", "sample_at": 100 + index * 5,
            "target_completion_at": 140.0 if label else None,
        }
        for index, label in enumerate(labels)
    ]


def test_notification_indices_collapse_dense_predictions_and_apply_cooldown():
    sample = rows([0] * 9)
    predicted = np.asarray([False, True, True, False, True, False, False, False, True])
    assert notification_indices(sample, predicted, cooldown_seconds=30) == [1, 8]


def test_notification_indices_can_require_sustained_probability():
    sample = rows([0] * 7)
    predicted = np.asarray([False, True, False, True, True, True, False])
    assert notification_indices(
        sample, predicted, cooldown_seconds=0, min_consecutive_samples=2,
    ) == [4]


def test_feature_vector_contains_finite_derived_features():
    row = rows([0])[0] | {
        "price": 3.5, "trades_5s": 8, "trades_15s": 12, "trades_30s": 20,
        "trades_60s": 30, "dollar_5s": 1000, "dollar_15s": 1200,
        "dollar_30s": 1800, "dollar_60s": 2500,
    }
    vector = feature_vector(row)
    assert len(vector) > 22
    assert np.isfinite(vector).all()


def test_threshold_selection_requires_notification_support():
    sample = rows([0, 1, 1, 0, 0, 1, 0, 0])
    labels = np.asarray([0, 1, 1, 0, 0, 1, 0, 0])
    probability = np.asarray([0.1, 0.9, 0.8, 0.2, 0.3, 0.85, 0.4, 0.2])
    threshold, result, target_met = select_threshold(
        sample, labels, probability, target_precision=0.5,
        min_notifications=1, cooldown_seconds=0,
    )
    assert 0.4 < threshold <= 0.9
    assert result["notification_precision"] >= 0.5
    assert target_met


def test_threshold_selection_reports_failed_support_gate():
    sample = rows([0, 1, 0])
    labels = np.asarray([0, 1, 0])
    probability = np.asarray([0.1, 0.9, 0.2])
    _, result, target_met = select_threshold(
        sample, labels, probability, target_precision=0.5,
        min_notifications=10, cooldown_seconds=30,
    )
    assert result["notification_episodes"] < 10
    assert not target_met
