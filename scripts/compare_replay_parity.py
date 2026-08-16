from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare Python oracle findings with Rust replay candidates.")
    parser.add_argument("--python-report", type=Path, required=True)
    parser.add_argument("--rust-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tolerance-seconds", type=float, default=5.0)
    args = parser.parse_args()
    python_report = json.loads(args.python_report.read_text(encoding="utf-8"))
    rust_report = json.loads(args.rust_report.read_text(encoding="utf-8"))
    oracle = [item for item in python_report.get("findings", []) if item.get("stage") == "PRE_IGNITION"]
    rust = list(rust_report.get("candidates", []))
    remaining = set(range(len(rust)))
    matches = []
    missing = []
    for finding in oracle:
        choices = [index for index in remaining if rust[index].get("ticker") == finding.get("ticker")]
        if choices:
            index = min(choices, key=lambda item: abs(float(rust[item]["detected_at"]) - float(finding["detected_at"])))
            delta = float(rust[index]["detected_at"]) - float(finding["detected_at"])
            if abs(delta) <= args.tolerance_seconds:
                remaining.remove(index)
                matches.append({"ticker": finding["ticker"], "python_at": finding["detected_at"], "rust_at": rust[index]["detected_at"], "delta_seconds": delta, "recipe_score_equal": finding.get("recipe_score") == rust[index].get("recipe_score")})
                continue
        missing.append({"ticker": finding.get("ticker"), "detected_at": finding.get("detected_at")})
    extras = [rust[index] for index in sorted(remaining)]
    denominator = max(1, len(oracle) + len(extras))
    parity_rate = len(matches) / denominator
    report = {
        "mode": "SIMULATION",
        "python_engine": python_report.get("replay_engine_version"),
        "rust_engine": rust_report.get("engine"),
        "tolerance_seconds": args.tolerance_seconds,
        "python_precursors": len(oracle),
        "rust_precursors": len(rust),
        "matched": len(matches),
        "missing_in_rust": len(missing),
        "extra_in_rust": len(extras),
        "parity_rate": round(parity_rate, 6),
        "production_cutover_ready": bool(oracle) and parity_rate == 1.0 and not missing and not extras and all(item["recipe_score_equal"] for item in matches),
        "matches": matches,
        "missing": missing,
        "extras": extras,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key not in {"matches", "missing", "extras"}}, indent=2))


if __name__ == "__main__":
    main()
