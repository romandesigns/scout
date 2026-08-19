import importlib
import subprocess
import sys
from pathlib import Path


def test_detection_quality_imports_as_package():
    module = importlib.import_module("scripts.detection_quality")
    assert callable(module.classification)
    assert callable(module.make_provider)


def test_detection_quality_executes_as_direct_script():
    root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [sys.executable, str(root / "scripts" / "detection_quality.py"), "--help"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert "Scout forward detection-quality audit" in proc.stdout
