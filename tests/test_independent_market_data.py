import importlib.util
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("imd", ROOT/"scripts"/"independent_market_data.py")
m=importlib.util.module_from_spec(spec); assert spec and spec.loader
sys.modules[spec.name]=m
spec.loader.exec_module(m)


class FakeProvider(m.IndependentProvider):
    name="fake"
    @property
    def configured(self): return True
    def bars(self,symbol,detected_at):
        return [
            m.IndependentBar(detected_at,10,10.1,9.9,10),
            m.IndependentBar(detected_at+300,11,11.2,10.8,11),
            m.IndependentBar(detected_at+900,12,12.3,11.8,12),
        ]


def test_independent_metrics_are_separate_from_scout_metrics():
    checker=m.IndependentCrossChecker(FakeProvider(), tolerance_pct=0.5)
    r=checker.metrics("ABC",1000,10)
    assert r["status"]=="OK"
    assert round(r["return_300s_pct"],6)==10.0
    assert round(r["return_900s_pct"],6)==20.0


def test_crosscheck_flags_large_disagreement():
    checker=m.IndependentCrossChecker(FakeProvider(), tolerance_pct=0.5)
    independent={"status":"OK","return_300s_pct":10.0,"return_900s_pct":20.0,
                 "mfe_300s_pct":12.0,"mae_300s_pct":-1.0}
    scout={"return_300s_pct":8.0,"return_900s_pct":20.1,"mfe_300s_pct":12.1,"mae_300s_pct":-1.1}
    c=checker.compare(scout,independent)
    assert c["within_tolerance"] is False
