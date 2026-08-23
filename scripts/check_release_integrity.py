from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def main() -> int:
    errors: list[str] = []
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if not SEMVER.fullmatch(version):
        errors.append(f"VERSION is not semantic x.y.z: {version!r}")

    package_version = json.loads((ROOT / "web/package.json").read_text(encoding="utf-8"))["version"]
    tauri_version = json.loads((ROOT / "web/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))["version"]
    cargo_text = (ROOT / "web/src-tauri/Cargo.toml").read_text(encoding="utf-8")
    worker_text = (ROOT / "web/public/sw.js").read_text(encoding="utf-8")
    env_text = (ROOT / ".env.example").read_text(encoding="utf-8")
    cargo_match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', cargo_text)
    worker_match = re.search(r'const VERSION="([^"]+)"', worker_text)
    env_match = re.search(r"(?m)^APP_VERSION=(.+)$", env_text)
    observed = {
        "web/package.json": package_version,
        "web/src-tauri/tauri.conf.json": tauri_version,
        "web/src-tauri/Cargo.toml": cargo_match.group(1) if cargo_match else None,
        "web/public/sw.js": worker_match.group(1) if worker_match else None,
        ".env.example": env_match.group(1).strip() if env_match else None,
    }
    for path, found in observed.items():
        if found != version:
            errors.append(f"{path} version is {found!r}; expected {version!r}")

    config_text = (ROOT / "app/config.py").read_text(encoding="utf-8")
    docker_text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    trader_text = (ROOT / "app/trader.py").read_text(encoding="utf-8")
    if "_repository_version()" not in config_text:
        errors.append("backend version does not default through _repository_version()")
    if "COPY VERSION ./VERSION" not in docker_text:
        errors.append("Docker runtime image does not contain VERSION")
    if "ALPACA_TRADING_BASE=https://paper-api.alpaca.markets" not in env_text:
        errors.append(".env.example does not default trading to Alpaca paper")
    if 'hostname == "paper-api.alpaca.markets"' not in trader_text:
        errors.append("paper trader hostname safety check is missing")

    if errors:
        print("Release integrity FAILED:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"Release integrity OK: Scout {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
