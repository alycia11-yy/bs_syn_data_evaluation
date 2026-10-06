#!/usr/bin/env python3
"""Scan set1..set4 (config/datasets.json) into data/stage1/manifest.jsonl.

    python scripts/build_manifest.py            # with SHA-256 per file (finds duplicates)
    python scripts/build_manifest.py --no-hash  # faster, no duplicate detection
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.manifest import build_manifest, summarise  # noqa: E402
from core.settings import MANIFEST_PATH  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-hash", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    items, warnings = build_manifest(with_hash=not args.no_hash)
    print(f"{len(items)} images -> {MANIFEST_PATH}")
    print(json.dumps(summarise(items), indent=2))
    for warning in warnings:
        print(f"WARNING: {warning}")


if __name__ == "__main__":
    main()
