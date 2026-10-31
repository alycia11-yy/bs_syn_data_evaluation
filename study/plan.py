"""The study pool as the server sees it, and each rater's fixed viewing order.

A rater's plan is made on first sign-in, deterministically from the pool seed and the
username, and written to data/stage2/plans/<username>.json; it never changes after that.

- rater: every main image and control in their own random order, one continuous list —
  every rating is saved as it is made and the rater picks up at the next unrated image
  whenever they sign in; each repeat comes well after its original (`REPEAT_GAP` images);
- pilot: `pilot_size` main images, no repeats or controls (not analysed);
- admin: no plan (progress page only).
"""
from __future__ import annotations

import os
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.jsonstore import read_json, write_json_atomic
from core.settings import PLANS_DIR, POOL_IMAGES_DIR, POOL_ITEMS_PATH

PLAN_FORMAT = 2          # 1 = split into sessions (before 10 Oct 2026)
REPEAT_GAP = 100         # a repeat comes at least this many images after its original
RATER = "rater"
PILOT = "pilot"
ADMIN = "admin"


@dataclass
class Pool:
    version: str
    seed: int
    pilot_size: int
    max_clicks: int
    zoom_steps: List[int]
    items: Dict[str, Dict[str, Any]]      # study_id -> {image, kind, repeat_of, width, height}

    def image_path(self, study_id: str) -> Optional[Path]:
        item = self.items.get(study_id)
        return POOL_IMAGES_DIR / item["image"] if item else None

    def localize(self, study_id: str, clicks: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Extra fields stored with a rating. The blinded pool has no masks on the server,
        so localization is scored later from the key (export_ratings.py --with-key)."""
        return {}


def load_pool(path: Path = POOL_ITEMS_PATH) -> Optional[Pool]:
    raw = read_json(path)
    if not raw:
        return None
    return Pool(
        version=raw["pool_version"],
        seed=raw["seed"],
        pilot_size=int(raw.get("pilot_size", 50)),
        max_clicks=int(raw.get("max_clicks", 3)),
        zoom_steps=list(raw.get("zoom_steps", [100, 125, 150, 175, 200])),
        items={i["study_id"]: i for i in raw["items"]},
    )


def get_plan(pool: Pool, username: str, role: str) -> Dict[str, Any]:
    path = PLANS_DIR / f"{username}.json"
    plan = read_json(path)
    if plan and plan.get("pool_version") == pool.version and plan.get("role") == role:
        if plan.get("format") == PLAN_FORMAT:
            return plan
        # an older session-split plan: keep its order, as one continuous list
        plan = {**plan, "format": PLAN_FORMAT,
                "sessions": [[sid for ids in plan["sessions"] for sid in ids]]}
        write_json_atomic(path, plan)
        return plan
    if plan:  # older pool or changed role: keep the old plan for the record, start anew
        archived = f"{username}.{plan.get('pool_version')}.{plan.get('role')}.json"
        os.replace(path, path.with_name(archived))
    plan = build_plan(pool, username, role)
    write_json_atomic(path, plan)
    return plan


def build_plan(pool: Pool, username: str, role: str) -> Dict[str, Any]:
    rng = random.Random(f"{pool.seed}:{username}")
    main = sorted(i for i, it in pool.items.items() if it["kind"] == "main")
    if role == PILOT:
        sessions = [rng.sample(main, min(pool.pilot_size, len(main)))]
    else:
        order = main + sorted(i for i, it in pool.items.items() if it["kind"] == "control")
        rng.shuffle(order)
        repeats = sorted(i for i, it in pool.items.items() if it["kind"] == "repeat")
        sessions = [_place_repeats(order, repeats, pool, rng)]
    return {
        "username": username,
        "role": role,
        "pool_version": pool.version,
        "format": PLAN_FORMAT,
        # one continuous list; kept as a list of lists so older plan files still read
        "sessions": sessions,
    }


def _place_repeats(order: List[str], repeats: List[str], pool: Pool,
                   rng: random.Random) -> List[str]:
    """Insert each repeat at least REPEAT_GAP images after its original (or at the end)."""
    order = list(order)
    originals = {pool.items[r]["repeat_of"] for r in repeats}
    head = max(0, len(order) - REPEAT_GAP)
    if head:   # no original in the last REPEAT_GAP places: swap it to an earlier place
        for original in sorted(originals):
            k = order.index(original)
            if k >= head:
                choices = [i for i in range(head) if order[i] not in originals]
                if choices:
                    j = rng.choice(choices)
                    order[j], order[k] = order[k], order[j]
    for repeat in repeats:
        after = order.index(pool.items[repeat]["repeat_of"]) + 1
        earliest = min(after + REPEAT_GAP, len(order))
        order.insert(rng.randint(earliest, len(order)), repeat)
    return order


def flat(plan: Dict[str, Any]) -> List[tuple[int, int, str]]:
    """[(session index, position in session, study id)] in viewing order."""
    return [(s, p, sid) for s, ids in enumerate(plan["sessions"]) for p, sid in enumerate(ids)]
