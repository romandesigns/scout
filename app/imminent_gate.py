from __future__ import annotations

from pathlib import Path
from threading import Lock

from .learning_features import FEATURES, feature_vector
from .models import Finding

_cache: dict[str, tuple[int, object]] = {}
_cache_lock = Lock()


def _load_artifact(path: Path) -> object:
    resolved = str(path.resolve())
    modified = path.stat().st_mtime_ns
    with _cache_lock:
        cached = _cache.get(resolved)
        if cached and cached[0] == modified:
            return cached[1]
        import joblib
        artifact = joblib.load(path)
        _cache[resolved] = (modified, artifact)
        return artifact


def score_finding(finding: Finding, model_path: Path) -> dict[str, object]:
    """Attach a fail-open shadow score; this function never controls delivery."""
    try:
        import numpy as np
        artifact = _load_artifact(model_path)
        if tuple(artifact.get("features") or ()) != FEATURES:
            raise ValueError("model feature contract mismatch")
        probability = float(artifact["model"].predict_proba(np.asarray([feature_vector(finding)]))[:, 1][0])
        threshold = float(artifact["threshold"])
        return {
            "status": "scored", "shadow_only": True, "probability": probability,
            "threshold": threshold, "would_pass": probability >= threshold,
            "model_test_date": artifact.get("test_date"),
        }
    except Exception as exc:
        return {"status": "error", "shadow_only": True, "error": f"{type(exc).__name__}: {exc}"}
