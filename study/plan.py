"""The study pool as the server sees it, and each rater's fixed viewing order.

A rater's plan is made on first sign-in, deterministically from the pool seed and the
username, and written to data/stage2/plans/<username>.json; it never changes after that.

- rater: every main image and control, in their own random order, split into sessions of
  `session_size`; each repeat is placed in a later session than its original;
- pilot: `pilot_size` main images, one session, no repeats or controls (not analysed);
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

RATER = "rater"
PILOT = "pilot"
ADMIN = "admin"


@dataclass
class Pool:
    version: str
    seed: int
    session_size: int
    pilot_size: int
    max_clicks: int
    zoom_steps: List[int]
    items: Dict[str, Dict[str, Any]]      # study_id -> {image, kind, repeat_of, width, height}

    def image_path(self, study_id: str) -> Optional[Path]:
        item = self.items.get(study_id)
        return POOL_IMAGES_DIR / item["image"] if item else None


def load_pool(path: Path = POOL_ITEMS_PATH) -> Optional[Pool]:
    raw = read_json(path)
    if not raw:
        return None
    return Pool(
        version=raw["pool_version"],
        seed=raw["seed"],
        session_size=int(raw.get("session_size", 150)),
        pilot_size=int(raw.get("pilot_size", 50)),
        max_clicks=int(raw.get("max_clicks", 3)),
        zoom_steps=list(raw.get("zoom_steps", [100, 125, 150, 175, 200])),
        items={i["study_id"]: i for i in raw["items"]},
    )


def get_plan(pool: Pool, username: str, role: str) -> Dict[str, Any]:
    path = PLANS_DIR / f"{username}.json"
    plan = read_json(path)
    if plan and plan.get("pool_version") == pool.version and plan.get("role") == role:
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
        sessions = _place_repeats(_chunk(order, pool.session_size), repeats, pool, rng)
    return {
        "username": username,
        "role": role,
        "pool_version": pool.version,
        "sessions": sessions,
    }


def _chunk(order: List[str], size: int) -> List[List[str]]:
    size = max(1, size)
    return [order[i:i + size] for i in range(0, len(order), size)] or [[]]


def _place_repeats(
    sessions: List[List[str]], repeats: List[str], pool: Pool, rng: random.Random
) -> List[List[str]]:
    originals = {pool.items[r]["repeat_of"] for r in repeats}
    last = len(sessions) - 1
    if last > 0:
        # an original in the last session would leave no later session for its repeat:
        # swap it with a non-original from an earlier session
        for original in sorted(originals):
            if original in sessions[last]:
                s = rng.randrange(0, last)
                choices = [i for i, sid in enumerate(sessions[s]) if sid not in originals]
                if choices:
                    j = rng.choice(choices)
                    k = sessions[last].index(original)
                    sessions[s][j], sessions[last][k] = sessions[last][k], sessions[s][j]
    for repeat in repeats:
        original = pool.items[repeat]["repeat_of"]
        home = next(n for n, s in enumerate(sessions) if original in s)
        if home < last:
            target = rng.randint(home + 1, last)
            sessions[target].insert(rng.randint(0, len(sessions[target])), repeat)
        else:  # a single session: anywhere after the original
            after = sessions[home].index(original) + 1
            sessions[home].insert(rng.randint(after, len(sessions[home])), repeat)
    return sessions


def flat(plan: Dict[str, Any]) -> List[tuple[int, int, str]]:
    """[(session index, position in session, study id)] in viewing order."""
    return [(s, p, sid) for s, ids in enumerate(plan["sessions"]) for p, sid in enumerate(ids)]
