"""Stage 2 from rater folders — the same layout as the Stage 1 reviewer folders.

    data/stage2_split/
      R1/                                 folder name = the rater's account name
        manifest.jsonl                    (optional; scanned from labels.jsonl if absent)
        set1/30_dataset/{images,masks,labels.jsonl}   … set2, set3, set4
      R2/ …

Every image in a rater's folder is shown to that rater, under a blinded study id
(`S` + 10 hex characters, derived from the seed, the rater and the image — never the
file name). Masks and labels stay on the server: they are used only to score where the
rater clicked (study/localize.py). Repeats are drawn from the folder (`repeats` in
config/study.json) and controls come from data/controls/ as in the pool mode.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import random
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.datasets import load_dataset_config, scan_datasets
from core.settings import PROJECT_ROOT, STUDY_CONFIG_PATH
from study.localize import localize
from study.plan import Pool

logger = logging.getLogger(__name__)

_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp"}


@dataclass
class FolderPool(Pool):
    """One rater's pool, read from their folder."""

    folder: Optional[Path] = None
    paths: Dict[str, Path] = field(default_factory=dict)       # study id -> image file
    meta: Dict[str, Dict[str, Any]] = field(default_factory=dict)  # study id -> item
    margin_fraction: float = 0.5

    def image_path(self, study_id: str) -> Optional[Path]:
        return self.paths.get(study_id)

    def localize(self, study_id: str, clicks: List[Dict[str, Any]]) -> Dict[str, Any]:
        item = self.meta.get(study_id, {})
        mask = item.get("_mask_path")
        result = localize(clicks, mask_path=Path(mask) if mask else None,
                          boxes=item.get("boxes") or [], margin_fraction=self.margin_fraction)
        return {"item_id": item.get("id"), "label": item.get("label"), **result}


def load_folder_pools(root: Path, config_path: Path = STUDY_CONFIG_PATH
                      ) -> Tuple[Dict[str, FolderPool], List[str]]:
    """rater -> their pool, for every sub-folder of `root` holding images."""
    if not root.is_dir():
        return {}, []
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    controls = _load_controls(PROJECT_ROOT / config.get("controls_dir", "data/controls"))
    pools, warnings = {}, []
    for folder in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        items = _folder_items(folder)
        if not items:
            continue
        pools[folder.name] = _build_pool(folder, items, controls, config, warnings)
    return pools, warnings


# --------------------------------------------------------------------------- #


def _folder_items(folder: Path) -> List[Dict[str, Any]]:
    """The folder's own manifest.jsonl, else a scan with config/datasets.json."""
    manifest = folder / "manifest.jsonl"
    if manifest.is_file():
        items = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines()
                 if line.strip()]
    else:
        config = load_dataset_config()
        local = copy.deepcopy(config)
        for set_cfg in local.sets:
            set_cfg.folder = folder / set_cfg.folder.relative_to(config.root)
        local.root = folder
        items, _ = scan_datasets(local, with_hash=True)
    present = []
    for item in items:
        path = folder / item["file"]
        if not path.is_file():
            continue
        mask = (item.get("views") or {}).get("mask")
        present.append({**item, "_path": str(path),
                        "_mask_path": str(folder / mask) if mask else None})
    return present


def _build_pool(folder: Path, items, controls, config, warnings) -> FolderPool:
    rater = folder.name
    seed = config.get("seed", 0)
    by_id = {i["id"]: i for i in items}
    # an identical copy (the authentic page in both paired sets) is shown once
    main = [i for i in items if not (i.get("duplicate_of") and i["duplicate_of"] in by_id)]
    per_doc = Counter((i["side"], i["doc_id"]) for i in main if i.get("doc_id"))
    repeated_docs = sum(1 for n in per_doc.values() if n > 1)
    if repeated_docs:
        warnings.append(f"{rater}: {repeated_docs} documents have more than one version in "
                        "the folder — the rater will see the same page twice (fine for a "
                        "test; for the study make the folders with scripts/split_stage2.py)")

    entries: Dict[str, Dict[str, Any]] = {}
    paths: Dict[str, Path] = {}
    meta: Dict[str, Dict[str, Any]] = {}

    def add(item: Dict[str, Any], kind: str, salt: str = "", repeat_of: str = None) -> str:
        sid = "S" + hashlib.sha256(f"{seed}:{rater}:{item['id']}{salt}".encode()).hexdigest()[:10]
        entries[sid] = {"study_id": sid, "kind": kind, "repeat_of": repeat_of,
                        "width": item.get("width"), "height": item.get("height")}
        paths[sid] = Path(item["_path"])
        meta[sid] = item
        return sid

    main_ids = [add(item, "main") for item in sorted(main, key=lambda i: i["id"])]
    rng = random.Random(f"{seed}:{rater}:repeats")
    n_repeats = min(int(config.get("repeats", 0)), len(main_ids) // 2)
    for sid in rng.sample(sorted(main_ids), n_repeats):
        add(meta[sid], "repeat", salt=":repeat", repeat_of=sid)
    for control in controls:
        add(control, "control")

    version = hashlib.sha256("".join(sorted(entries)).encode()).hexdigest()[:12]
    return FolderPool(
        version=version, seed=seed,
        pilot_size=int(config.get("pilot_size", 50)),
        max_clicks=int(config.get("max_clicks", 3)),
        zoom_steps=list(config.get("zoom_steps", [100, 125, 150, 175, 200])),
        items=entries, folder=folder, paths=paths, meta=meta,
        margin_fraction=float(config.get("click_margin_fraction", 0.5)),
    )


def _load_controls(folder: Path) -> List[Dict[str, Any]]:
    """The author's crude edits (data/controls/crude) and clean pages (…/clean)."""
    out = []
    for sub, label in (("crude", "tampered"), ("clean", "authentic")):
        directory = folder / sub
        if not directory.is_dir():
            continue
        boxes_file = directory / "boxes.json"
        boxes = json.loads(boxes_file.read_text()) if boxes_file.is_file() else {}
        for path in sorted(p for p in directory.iterdir() if p.suffix.lower() in _IMAGE_EXT):
            from PIL import Image

            with Image.open(path) as img:
                width, height = img.size
            mask = directory / "masks" / f"{path.stem}.png"
            out.append({"id": f"control/{sub}/{path.name}", "label": label, "set": "control",
                        "boxes": boxes.get(path.name, []), "_path": str(path),
                        "width": width, "height": height,
                        "_mask_path": str(mask) if mask.is_file() else None})
    return out
