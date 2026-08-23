from app.imminent_gate import FEATURES, feature_vector, score_finding
from app.models import Finding
from scripts.train_imminent_alert_gate import feature_vector as training_feature_vector
import math


def finding() -> Finding:
    return Finding(
        ticker="TEST", stage="EARLY", detected_at=1.0, price=2.0, score=8,
        vol_ratio_15s=4.0, vol_ratio_30s=3.0, change_60s_pct=1.0,
        extension_pct=0.5, ema9=2.0, ema21=1.9, ema9_slope=0.1,
        vwap=1.95, above_vwap=True, quiet_break=False, evidence=[],
        actionable_rank="A", engine_source="python",
    )


def test_runtime_feature_contract_has_expected_width():
    item = finding()
    runtime = feature_vector(item)
    training_row = dict(item.__dict__)
    training_row["source"] = item.engine_source
    assert len(runtime) == len(FEATURES)
    assert runtime == training_feature_vector(training_row)


def test_shadow_gate_fails_open_when_model_is_missing(tmp_path):
    result = score_finding(finding(), tmp_path / "missing.joblib")
    assert result["status"] == "error"
    assert result["shadow_only"] is True


def test_derived_learning_features_are_finite_and_bounded():
    item = finding()
    item.change_5s_pct = 0.1
    item.change_15s_pct = 0.3
    item.change_30s_pct = 0.8
    item.dollar_volume_15s = 20_000
    item.trades_15s = 30
    item.float_turnover = 1.5
    item.candidate_profile = {"compression_quality": 82}
    values = feature_vector(item)
    assert all(math.isfinite(value) for value in values)
    assert values[FEATURES.index("velocity_persistence")] == 1.0
    assert values[FEATURES.index("compression_quality")] == 0.82
