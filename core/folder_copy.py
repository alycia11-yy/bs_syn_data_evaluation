"""Copying images into per-person folders (Stage 1 reviewers, Stage 2 raters)."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Dict, Tuple

from core.datasets import DatasetConfig


def original_label_rows(config: DatasetConfig) -> Dict[Tuple[str, str], Tuple[str, dict]]:
    """(set, image file name) -> (labels.jsonl path relative to root, original row)."""
    out = {}
    for set_cfg in config.sets:
        spec = set_cfg.metadata
        if not spec:
            continue
        path = (set_cfg.folder / spec["file"]).resolve()
        if not path.is_file():
            continue
        rel = path.relative_to(config.root.resolve()).as_posix()
        key = spec.get("key", "file")
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                out[(set_cfg.name, Path(str(row.get(key, ""))).name)] = (rel, row)
    return out


def place(source: Path, target: Path, hardlink: bool) -> str:
    """Copy (or hard-link) `source` to `target`; return the SHA-256 of what landed."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if hardlink:
        try:
            os.link(source, target)
        except OSError:
            shutil.copy2(source, target)
    else:
        shutil.copy2(source, target)
    return sha256(target)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
