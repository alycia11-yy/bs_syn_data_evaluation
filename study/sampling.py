"""Draw the realism-study pool from the Stage 1 selection. Pure logic, no file I/O.

Rules (doc 12 §3):
- only images the author kept in Stage 1;
- one version per document: a rater never sees two versions of the same page, so a
  (side, doc_id) contributes at most one image to the whole pool;
- strata are filled in config order — tampered strata first, so the authentic strata
  take documents "not used above";
- inside a stratum, round-robin over `balance_by` (method / model) so each gets an
  equal share, and no layout takes more than `max_layout_share` of the stratum
  (relaxed, with a warning, only if the stratum cannot be filled otherwise).

If the manifest has no authentic/tampered labels yet for a side, that side falls back
to a plain random draw of `fallback_per_side` kept images (still one per document).
"""
from __future__ import annotations

import math
import random
from collections import defaultdict
from typing import Any, Dict, List, Optional, Sequence, Tuple

Item = Dict[str, Any]


def draw_pool(
    kept: Sequence[Item], config: Dict[str, Any]
) -> Tuple[List[Tuple[str, Item]], List[str]]:
    """Return ([(stratum name, item)], warnings)."""
    rng = random.Random(config["seed"])
    candidates = sorted((i for i in kept if not i.get("duplicate_of")), key=lambda i: i["id"])
    one_version = config.get("one_version_per_document", True)
    used_docs: set = set()
    picked: List[Tuple[str, Item]] = []
    warnings: List[str] = []

    sides = []
    for stratum in config["strata"]:
        if stratum["side"] not in sides:
            sides.append(stratum["side"])

    for side in sides:
        side_items = [i for i in candidates if i["side"] == side]
        labelled = any(i["label"] != "unknown" for i in side_items)
        strata = [s for s in config["strata"] if s["side"] == side]
        if not labelled:
            n = int(config.get("fallback_per_side", sum(s["n"] for s in strata)))
            warnings.append(
                f"{side}: no authentic/tampered labels in the manifest — drew {n} kept "
                "images at random instead of the configured strata"
            )
            strata = [{"name": f"{side}_all", "side": side,
                       "sets": sorted({i['set'] for i in side_items}), "label": None, "n": n,
                       "balance_by": "set"}]
        for stratum in strata:
            chosen, note = _fill_stratum(
                side_items, stratum, rng, used_docs, one_version,
                float(config.get("max_layout_share", 1.0)),
            )
            picked += [(stratum["name"], item) for item in chosen]
            if note:
                warnings.append(note)
    return picked, warnings


def _fill_stratum(
    items: Sequence[Item],
    stratum: Dict[str, Any],
    rng: random.Random,
    used_docs: set,
    one_version: bool,
    max_layout_share: float,
) -> Tuple[List[Item], Optional[str]]:
    n = int(stratum["n"])
    sets = set(stratum.get("sets") or [])
    label = stratum.get("label")
    pool = [
        i for i in items
        if (not sets or i["set"] in sets) and (label is None or i["label"] == label)
    ]
    rng.shuffle(pool)
    groups: Dict[str, List[Item]] = defaultdict(list)
    for item in pool:
        groups[str(item.get(stratum.get("balance_by") or "") or "_")].append(item)
    order = sorted(groups)
    rng.shuffle(order)

    layout_cap = max(1, math.floor(max_layout_share * n)) if max_layout_share < 1 else n
    chosen: List[Item] = []
    note = None
    for cap in (layout_cap, n):              # second pass relaxes the layout cap
        _round_robin(groups, order, n, cap, chosen, used_docs, one_version)
        if len(chosen) >= n:
            break
        if cap != n:
            note = (f"{stratum['name']}: layout cap {cap} relaxed to fill the stratum "
                    f"(layouts too few or too uneven)")
    if len(chosen) < n:
        note = (f"{stratum['name']}: only {len(chosen)} of {n} images available "
                f"(kept, unused documents, label={label})")
    return chosen, note


def _round_robin(groups, order, n, layout_cap, chosen, used_docs, one_version) -> None:
    layout_count: Dict[str, int] = defaultdict(int)
    chosen_ids = {item["id"] for item in chosen}
    for item in chosen:
        layout_count[item.get("layout") or ""] += 1
    cursor = {g: 0 for g in order}
    active = [g for g in order if groups[g]]
    while len(chosen) < n and active:
        still_active = []
        for group in active:
            if len(chosen) >= n:
                break
            members = groups[group]
            while cursor[group] < len(members):
                item = members[cursor[group]]
                cursor[group] += 1
                doc = _doc_key(item)
                layout = item.get("layout") or ""
                if item["id"] in chosen_ids or (one_version and doc in used_docs):
                    continue
                if layout and layout_count[layout] >= layout_cap:
                    continue
                chosen.append(item)
                chosen_ids.add(item["id"])
                used_docs.add(doc)
                layout_count[layout] += 1
                still_active.append(group)
                break
        active = still_active


def _doc_key(item: Item) -> Tuple[str, str]:
    return (item["side"], item.get("doc_id") or f"image:{item['id']}")


# --------------------------------------------------------------------------- #
# study ids, repeats and controls
# --------------------------------------------------------------------------- #


def assign_study_ids(
    main: List[Tuple[str, Item]],
    controls: List[Dict[str, Any]],
    n_repeats: int,
    seed: int,
) -> List[Dict[str, Any]]:
    """Entries with random study ids S0001.. so the id says nothing about the image.

    Each entry: {study_id, kind: main | repeat | control, stratum, item | control,
    repeat_of}. Repeats are extra entries pointing at a main entry's image.
    """
    rng = random.Random(f"{seed}:ids")
    entries: List[Dict[str, Any]] = [
        {"kind": "main", "stratum": stratum, "item": item} for stratum, item in main
    ]
    for originals in rng.sample(range(len(entries)), min(n_repeats, len(entries))):
        entries.append({"kind": "repeat", "stratum": entries[originals]["stratum"],
                        "item": entries[originals]["item"], "_orig": originals})
    entries += [{"kind": "control", "stratum": c["stratum"], "control": c} for c in controls]
    ids = [f"S{n:04d}" for n in range(1, len(entries) + 1)]
    rng.shuffle(ids)
    for entry, study_id in zip(entries, ids):
        entry["study_id"] = study_id
    for entry in entries:
        if entry["kind"] == "repeat":
            entry["repeat_of"] = entries[entry.pop("_orig")]["study_id"]
    return entries
