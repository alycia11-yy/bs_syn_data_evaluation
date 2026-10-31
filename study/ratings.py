"""Realism ratings: the scale, validation, and per-rater storage.

    data/stage2/outputs/<username>/ratings.jsonl        every submitted rating
    data/stage2/outputs/<username>/ratings_latest.json  study_id -> rating

Per page the rater either presses **No edit on this page** (score 5), or clicks every
place they think was edited (each click = "an edit is here") and gives a realism score
from 1 to 5 — the higher, the more genuine the page looks. Where they clicked and how
genuine the page looks are separate answers: a rater may spot an edit that still looks
fairly real.

One rating:
    {"rater": "R1", "study_id": "S4649c22930", "no_traces": false,
     "clicks": [{"x": 512.0, "y": 88.5, "hit": true}], "score": 2,
     "time_ms": 9312, "position": 41, "timestamp": "...", "max_zoom": 150, ...}
Click coordinates are in the image's own pixels; `hit` and the other localization fields
are added on the server from the mask (study/localize.py).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from core.jsonstore import RecordStore
from core.settings import RATINGS_DIR

NO_EDIT_SCORE = 5

# score, label, meaning shown to raters — higher = looks more genuine
SCALE: Tuple[Tuple[int, str, str], ...] = (
    (5, "Genuine", "Looks like an untouched statement, even on close inspection."),
    (4, "Probably genuine",
     "Small oddities that printing, scanning or the bank's own layout could explain."),
    (3, "Unsure", "Something is off, but I cannot say whether it was edited."),
    (2, "Probably edited",
     "A word or number whose font, size, weight, colour, spacing or alignment differs "
     "from its surroundings."),
    (1, "Clearly edited",
     "Obvious signs: wrong font, misaligned or overlapping text, smudge or blur around a "
     "word, patch of different background."),
)
SCORES = tuple(score for score, _, _ in SCALE)

_SAFE_USERNAME = re.compile(r"(?!\.?\.?$)[A-Za-z0-9._-]+")


def validate(clicks: List[Dict[str, Any]], score: Optional[int], max_clicks: int,
             no_traces: bool = False) -> str | None:
    """Return a message for the rater if the answer is not complete, else None."""
    if no_traces:
        return None if not clicks and score == NO_EDIT_SCORE else "Remove the marked places."
    if not clicks:
        return "Click where you think the page was edited, or press 'No edit on this page'."
    if len(clicks) > max_clicks:
        return f"At most {max_clicks} places."
    if score not in SCORES:
        return "Choose how genuine the page looks (1–5)."
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
