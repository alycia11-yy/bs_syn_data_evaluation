"""Stage 1 split between reviewers: who reviews which images.

The split lives on disk, not in code. `scripts/split_stage1.py` copies whole documents
into one folder per reviewer, mirroring the original layout:

    data/stage1_split/
      split.json                       seed, reviewers, counts, checksums of every copy
      reviewer1/
        manifest.jsonl                 the manifest rows of this folder's images, so the
                                       folder works on its own (a reviewer's own machine)
        assignment.csv                 every file in this folder, with set, doc and label
        set1/30_dataset/images/…       the document's authentic page + tampered versions
        set1/30_dataset/masks/…
        set1/30_dataset/labels.jsonl   the original rows of exactly these images
        set2/… set3/… set4/…
      reviewer2/ …

The review app then gives each account the images that are physically in the folder of
the same name (`load_split`) and serves them from there. Move a file to another reviewer's
folder and it moves to that reviewer — the folders are the truth.

The unit of the split is the document: every version of one page, in both paired sets,
goes to the same reviewer, so a whole-document reject stays with one person.
"""
from __future__ import annotations

import json
import logging
import random
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)


def split_by_document(
    items: Sequence[Dict[str, Any]], reviewers: Sequence[str], seed: int
) -> Dict[str, List[str]]:
    """Deal documents to reviewers: shuffled with `seed`, each to whoever has the fewest
    images so far, so the shares are equal in images. Returns reviewer -> image ids."""
    if not reviewers:
        raise ValueError("at least one reviewer is needed")
    groups: Dict[tuple, List[str]] = defaultdict(list)
    for item in sorted(items, key=lambda i: i["id"]):
        groups[doc_key(item)].append(item["id"])
    keys = sorted(groups)
    rng = random.Random(seed)
    rng.shuffle(keys)
    load = {r: 0 for r in reviewers}
    out: Dict[str, List[str]] = {r: [] for r in reviewers}
    for key in keys:
        order = list(reviewers)
        rng.shuffle(order)                 # ties broken by a seeded order, not by name
        target = min(order, key=lambda r: load[r])
        out[target].extend(groups[key])
        load[target] += len(groups[key])
    return {r: sorted(ids) for r, ids in out.items()}


def doc_key(item: Dict[str, Any]) -> tuple:
    return (item["side"], item["doc_id"]) if item.get("doc_id") else ("image", item["id"])


@dataclass
class Split:
    root: Optional[Path] = None
    # reviewer -> image ids found in their folder
    assignments: Dict[str, set] = field(default_factory=dict)
    # original relative path ("set1/30_dataset/images/x.jpg") -> reviewer folder holding it
    file_owner: Dict[str, str] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    @property
    def active(self) -> bool:
        return bool(self.assignments)

    def path_of(self, rel_path: str) -> Optional[Path]:
        owner = self.file_owner.get(rel_path)
        return self.root / owner / rel_path if owner and self.root else None


def load_split(root: Path, items: Sequence[Dict[str, Any]]) -> Split:
    """Read the reviewer folders under `root`; an absent root means no split."""
    if not root.is_dir():
        return Split()
    id_by_file = {item["file"]: item["id"] for item in items}
    # images and masks of the manifest; labels.jsonl, assignment.csv etc. are not served
    known = set(id_by_file) | {v for item in items for v in (item.get("views") or {}).values()}
    split = Split(root=root)
    for folder in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        ids = set()
        for path in folder.rglob("*"):
            if path.name.startswith(".") or not path.is_file():
                continue
            rel = path.relative_to(folder).as_posix()
            if rel not in known:
                continue
            if rel in split.file_owner:
                split.warnings.append(f"{rel} is in both {split.file_owner[rel]}/ and "
                                      f"{folder.name}/ — using {split.file_owner[rel]}/")
                continue
            split.file_owner[rel] = folder.name
            if rel in id_by_file:
                ids.add(id_by_file[rel])
        if ids:
            split.assignments[folder.name] = ids
    return split


REVIEWER_MANIFEST = "manifest.jsonl"


def write_reviewer_manifest(folder: Path, items: Sequence[Dict[str, Any]]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / REVIEWER_MANIFEST).write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in items),
        encoding="utf-8")


def load_split_manifests(root: Path) -> List[Dict[str, Any]]:
    """The manifest rebuilt from the reviewer folders' own manifest.jsonl files — what a
    reviewer's machine (or the Stage 1 server) has instead of the full dataset."""
    if not root.is_dir():
        return []
    items: Dict[str, Dict[str, Any]] = {}
    for path in sorted(root.glob(f"*/{REVIEWER_MANIFEST}")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                item = json.loads(line)
                items.setdefault(item["id"], item)
    return sorted(items.values(), key=lambda i: (i["set"], i["name"]))
