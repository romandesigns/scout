import importlib.util
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"scripts"))

spec=importlib.util.spec_from_file_location("dq", ROOT/"scripts"/"detection_quality.py")
dq=importlib.util.module_from_spec(spec); assert spec and spec.loader
sys.modules[spec.name]=dq
spec.loader.exec_module(dq)


def test_mixed_positive_fade_is_not_flat_mixed():
    m={"return_30s_pct":0.1,"return_120s_pct":0.2,"return_300s_pct":0.6,
       "return_900s_pct":-0.2,"mfe_300s_pct":1.2,"mae_300s_pct":-0.4,
       "mfe_900s_pct":1.2}
    assert dq.classification(m)=="MIXED_POSITIVE_FADE"
    b=dq.mixed_breakdown(m)
    assert b["direction_5m"]=="POSITIVE"
    assert b["magnitude_5m"]=="SMALL"
    assert b["resolution_15m"]=="FADE"


def test_mixed_negative_recovery_is_distinct():
    m={"return_30s_pct":-0.2,"return_120s_pct":-0.1,"return_300s_pct":-0.6,
       "return_900s_pct":0.4,"mfe_300s_pct":0.8,"mae_300s_pct":-0.9,
       "mfe_900s_pct":1.0}
    assert dq.classification(m)=="MIXED_NEGATIVE_RECOVERY"


def test_provisional_mixed_retains_pending_15m_resolution():
    m={"return_30s_pct":0.1,"return_120s_pct":0.1,"return_300s_pct":0.4,
       "return_900s_pct":None,"mfe_300s_pct":1.0,"mae_300s_pct":-0.4,
       "mfe_900s_pct":None}
    assert dq.classification(m)=="PROVISIONAL_MIXED_POSITIVE_PENDING_15M"
