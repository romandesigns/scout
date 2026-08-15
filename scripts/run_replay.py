from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from app.config import settings
from app.replay import run_dataset


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a notification-isolated Scout historical replay.")
    parser.add_argument("dataset", type=Path, help="Canonical NDJSON dataset")
    parser.add_argument("--output", type=Path, default=settings.data_dir / "replays")
    args = parser.parse_args()
    report = asyncio.run(run_dataset(args.dataset, args.output))
    print(json.dumps({key: value for key, value in report.items() if key != "findings"}, indent=2))


if __name__ == "__main__":
    main()
