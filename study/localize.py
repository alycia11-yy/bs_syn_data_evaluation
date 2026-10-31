"""Localization check: did the rater's clicks land on the edits?

Every click means "an edit is here". The edit area is the image's **mask** (white =
edited pixels, all black for an authentic page). A click **hits** when an edited pixel
lies within `margin` pixels of it — a tolerance for imprecise clicks, by default half the
height of the edited text (doc 12 §6). Without a mask the label boxes, grown by the same
margin, are used.

An edited page can have several edited **regions** (one label box per edited field,
`labels.jsonl` `boxes`). A region is found when a hitting click lies in it, so a rater
who clicks both edits of a two-edit page gets full marks.

Stored with the rating:
- `clicks[].hit`
- `regions_total`, `regions_found`, `localization_score` = found / total (None if the page
  has no edit);
- `edit_found`  — at least one click hits an edit;
- `false_alarm` — at least one click hits nothing (every click on an authentic page).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

DEFAULT_MARGIN_PX = 10


def localize(
    clicks: List[Dict[str, Any]],
    *,
    mask_path: Optional[Path],
    boxes: Sequence[Sequence[float]] = (),
    margin_fraction: float = 0.5,
) -> Dict[str, Any]:
    boxes = [b for b in boxes if len(b) == 4]
    margin = _margin(boxes, margin_fraction)
    mask = _open_mask(mask_path)
    if mask is not None:
        source = "mask"

        def hit(x, y):
            return _mask_hit(mask, x, y, margin)
        has_edit = mask.getextrema()[1] > 127
    elif boxes:
        source = "boxes"

        def hit(x, y):
            return _in_boxes(boxes, x, y, margin)
        has_edit = True
    else:
        return {"localization": "none"}

    scored = [{"x": c["x"], "y": c["y"], "hit": bool(hit(c["x"], c["y"]))} for c in clicks]
    regions = [list(b) for b in boxes] if boxes else ([None] if has_edit else [])
    found = [
        i for i, box in enumerate(regions)
        if any(c["hit"] and (box is None or _in_boxes([box], c["x"], c["y"], margin))
               for c in scored)
    ]
    return {
        "clicks": scored,
        "localization": source,
        "margin_px": round(margin, 1),
        "regions_total": len(regions),
        "regions_found": len(found),
        "localization_score": round(len(found) / len(regions), 3) if regions else None,
        "edit_found": any(c["hit"] for c in scored),
        "false_alarm": any(not c["hit"] for c in scored),
    }


def _margin(boxes: Sequence[Sequence[float]], fraction: float) -> float:
    heights = sorted(b[3] - b[1] for b in boxes if b[3] > b[1])
    if not heights:
        return DEFAULT_MARGIN_PX
    return max(2.0, fraction * heights[len(heights) // 2])


def _open_mask(path: Optional[Path]):
    if path is None or not Path(path).is_file():
        return None
    from PIL import Image

    with Image.open(path) as img:
        return img.convert("L")


def _mask_hit(mask, x: float, y: float, margin: float) -> bool:
    w, h = mask.size
    left, top = max(0, int(x - margin)), max(0, int(y - margin))
    right, bottom = min(w, int(x + margin) + 1), min(h, int(y + margin) + 1)
    if left >= right or top >= bottom:
        return False
    return mask.crop((left, top, right, bottom)).getextrema()[1] > 127


def _in_boxes(boxes, x: float, y: float, margin: float) -> bool:
    return any(x0 - margin <= x <= x1 + margin and y0 - margin <= y <= y1 + margin
               for x0, y0, x1, y1 in boxes)
