"""The practice round (training, not scored): which images, and where they come from.

1. data/stage2/practice/practice.json, if present — the reference examples written by
   `scripts/make_training.py` (or made by hand):
       {"items": [{"image": "p01.jpg", "mask": "masks/p01.png", "label": "tampered",
                   "boxes": [[x0, y0, x1, y1]], "width": 2480, "note": "…"}]}
   plus practice/reference.json (the admin's answers, written by "Save reference PDF"), shown to
   raters after each page.
2. otherwise, for testing: `practice_size` images picked at random (fixed seed) from the
   rater folders.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.jsonstore import read_json


@dataclass
class PracticeItem:
    path: Path
    label: str
    width: Optional[int]
    boxes: List[List[float]]
    mask: Optional[Path]
    note: str = ""
    source_id: str = ""
    expected_score: Optional[int] = None      # unused since 31 Oct 2026 (the admin's answer is the reference)
    short: str = ""
    reference: Optional[Dict[str, Any]] = None  # the admin's answer (practice/reference.json), shown to raters


def load_practice(practice_dir: Path, pools: Dict[str, Any], size: int,
                  seed: int) -> List[PracticeItem]:
    raw = read_json(practice_dir / "practice.json")
    if raw and raw.get("items"):
        ref = (read_json(practice_dir / "reference.json") or {}).get("answers") or {}
        ref = {img: a for img, a in ref.items()
               if a.get("source_id") == next((i.get("source_id") for i in raw["items"] if i["image"] == img), None)}
        return [
            PracticeItem(
                path=practice_dir / item["image"],
                label=item.get("label", "unknown"),
                width=item.get("width"),
                boxes=item.get("boxes") or [],
                mask=practice_dir / item["mask"] if item.get("mask") else None,
                note=item.get("note", ""),
                source_id=item.get("source_id", ""),
                expected_score=item.get("expected_score"),
                short=item.get("short", ""),
                reference=ref.get(item["image"]),
            )
            for item in raw["items"] if (practice_dir / item["image"]).is_file()
        ]
    candidates = {}
    for pool in pools.values():
        for sid, item in getattr(pool, "meta", {}).items():
            if pool.items[sid]["kind"] == "main":
                candidates.setdefault(item["id"], item)
    if not candidates:
        return []
    rng = random.Random(f"{seed}:practice")
    chosen = rng.sample(sorted(candidates), min(size, len(candidates)))
    return [
        PracticeItem(
            path=Path(candidates[i]["_path"]), label=candidates[i].get("label", "unknown"),
            width=candidates[i].get("width"), boxes=candidates[i].get("boxes") or [],
            mask=Path(candidates[i]["_mask_path"]) if candidates[i].get("_mask_path") else None,
            note="Picked at random for testing; the real practice images come from Stage 1 "
                 "rejects.",
        )
        for i in chosen
    ]


def reference_store(username: str):
    """The admin's own answers on the practice pages (the reference examples for the raters):
    $STAGE2_DIR/reference/<username>/reference.jsonl (+ _latest.json, one record per page)."""
    from core.jsonstore import RecordStore
    from core.settings import STAGE2_DIR
    return RecordStore(STAGE2_DIR / "reference" / username, "reference", key=lambda r: r["image"])


def save_reference(username: str, index: int, item: PracticeItem, answer: Dict[str, Any],
                   result: Dict[str, Any]) -> None:
    from review.selection import now_iso
    from study.outcome import page_measures
    clicks = result.get("clicks", answer["clicks"])
    reference_store(username).append({
        "rater": username, "index": index, "image": item.path.name, "source_id": item.source_id,
        "label": item.label, "boxes": item.boxes, "width": item.width,
        **answer, "clicks": clicks, **{k: v for k, v in result.items() if k != "clicks"},
        "measures": page_measures(item.label, answer["score"], clicks, result.get("regions_found"),
                                  result.get("regions_total")),
        "timestamp": now_iso()})
