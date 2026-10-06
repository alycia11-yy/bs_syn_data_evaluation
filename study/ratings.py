"""Realism ratings: the scale, validation, and per-rater storage.

    data/stage2/outputs/<username>/ratings.jsonl        every submitted rating
    data/stage2/outputs/<username>/ratings_latest.json  study_id -> rating

One rating (doc 12 §8):
    {"rater": "R1", "study_id": "S0412", "no_traces": false,
     "clicks": [{"x": 512.0, "y": 88.5, "kind": "edit"}], "score": 2,
     "time_ms": 9312, "session": 3, "position": 41, "timestamp": "...", "max_zoom": 150,
     "role": "rater", "pool_version": "..."}
Click coordinates are in the image's own pixels.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from core.jsonstore import RecordStore
from core.settings import RATINGS_DIR

EDIT = "edit"
ODD = "odd"
CLICK_KINDS = {EDIT: "I saw an edit here", ODD: "It just looks odd here"}

# score, label, when allowed, meaning shown to raters (doc 12 §2)
SCALE: Tuple[Tuple[int, str, str], ...] = (
    (5, "No edit traces", "I see no sign of editing, even on close inspection."),
    (4, "Probably genuine",
     "Small oddities that printing, scanning or the bank's own layout could explain."),
    (3, "Unsure", "Something is off, but I cannot say whether it is an edit."),
    (2, "Probably edited",
     "I see a likely edit: a word or number whose font, size, weight, colour, spacing or "
     "alignment differs from its surroundings."),
    (1, "Clearly edited",
     "Obvious signs: wrong font, misaligned or overlapping text, smudge or blur around a "
     "word, patch of different background."),
)

_SAFE_USERNAME = re.compile(r"(?!\.?\.?$)[A-Za-z0-9._-]+")


def allowed_scores(clicks: List[Dict[str, Any]]) -> Tuple[int, ...]:
    """The score must agree with the clicks: no click -> 5, only 'odd' -> 3-4,
    any 'edit' -> 1-2."""
    if not clicks:
        return (5,)
    if any(c.get("kind") == EDIT for c in clicks):
        return (1, 2)
    return (3, 4)


def validate(clicks: List[Dict[str, Any]], score: Optional[int], max_clicks: int) -> str | None:
    """Return a message for the rater if the answer is not complete, else None."""
    if len(clicks) > max_clicks:
        return f"At most {max_clicks} clicks."
    if any(c.get("kind") not in CLICK_KINDS for c in clicks):
        return "Choose 'edit' or 'looks odd' for every click."
    if score not in allowed_scores(clicks):
        return "Choose a score."
    return None


def rating_store(username: str) -> RecordStore:
    if not _SAFE_USERNAME.fullmatch(username or ""):
        raise ValueError(f"unsafe username {username!r}")
    return RecordStore(RATINGS_DIR / username, "ratings", key=lambda r: r["study_id"])


_STORES: Dict[str, RecordStore] = {}


def store_for(username: str) -> RecordStore:
    if username not in _STORES:
        _STORES[username] = rating_store(username)
    return _STORES[username]
