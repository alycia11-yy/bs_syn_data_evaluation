"""The Stage 1 manifest: one JSON line per image of set1..set4.

Built once by `scripts/build_manifest.py` (or automatically by the review app when it is
missing). Both stages read it, so every decision and every sampled study item refers to
the same image id: ``<set>/<file name>``, e.g. ``set2/000123_t1.png``.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, List

from core.datasets import DatasetConfig, load_dataset_config, scan_datasets
from core.jsonstore import write_json_atomic
from core.settings import MANIFEST_PATH

logger = logging.getLogger(__name__)


def build_manifest(
    config: DatasetConfig | None = None, path: Path = MANIFEST_PATH, *, with_hash: bool = True
) -> tuple[List[Dict[str, Any]], List[str]]:
    config = config or load_dataset_config()
    items, warnings = scan_datasets(config, with_hash=with_hash)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in items)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    write_json_atomic(
        path.with_name("manifest_summary.json"),
        {"root": str(config.root), "counts": summarise(items), "warnings": warnings},
    )
    return items, warnings


def load_manifest(path: Path = MANIFEST_PATH) -> List[Dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(
            f"{path} not found. Build it with: python scripts/build_manifest.py"
        )
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def manifest_sha256(path: Path = MANIFEST_PATH) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""


def summarise(items: List[Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
    counts: Dict[str, Dict[str, int]] = {}
    for item in items:
        c = counts.setdefault(item["set"], {"images": 0, "duplicates": 0})
        c["images"] += 1
        c["duplicates"] += 1 if item.get("duplicate_of") else 0
        c[item["label"]] = c.get(item["label"], 0) + 1
    return counts
