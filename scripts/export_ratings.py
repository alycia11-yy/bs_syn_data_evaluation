#!/usr/bin/env python3
"""Stage 2 outputs: every rater's latest rating per study image, as JSON and CSV.

    python scripts/export_ratings.py              # blinded: study ids only
    python scripts/export_ratings.py --with-key   # analyst: joins set, label, method, boxes
    python scripts/export_ratings.py --include-pilot

Folder mode (data/stage2_split/<rater>/): the ratings already carry the image id and the
mask-based localization (`clicks[].hit`, `regions_found` / `regions_total`,
`localization_score`, `edit_found`, `false_alarm`), so
ratings.csv is written with the image metadata joined (set, label, method / model, …) —
keep it private. --with-key is not needed there.

Writes results/stage2/ratings.json and ratings.csv (doc 12 §8 columns: rater, study_id,
no_traces, clicks, score, time_ms, timestamp, max_zoom, + position, role);
with --with-key also ratings_with_key.csv, where each click is marked hit/miss against
the label boxes grown by `--margin` × box height (doc 12 §6, localization).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.jsonstore import RecordStore, read_json, write_json_atomic  # noqa: E402
from core.settings import (  # noqa: E402
    POOL_ITEMS_PATH,
    RATINGS_DIR,
    RESULTS_DIR,
    STAGE2_SPLIT_DIR,
    STUDY_KEY_PATH,
)
from study.folders import load_folder_pools  # noqa: E402
from study.outcome import page_measures  # noqa: E402

OUT = RESULTS_DIR / "stage2"
COLUMNS = ["rater", "role", "study_id", "no_traces", "clicks", "score", "time_ms",
           "position", "timestamp", "max_zoom", "pool_version"]
KEY_COLUMNS = ["kind", "repeat_of", "stratum", "source_id", "set", "side", "family", "label",
               "method", "model", "edit_type", "layout", "capture", "doc_id"]


def main() -> None:
    args = _parse_args()
    pools, _ = load_folder_pools(STAGE2_SPLIT_DIR)
    if pools:
        _export_folder_mode(pools, args.include_pilot)
        return
    pool = read_json(POOL_ITEMS_PATH)
    if not pool:
        sys.exit(f"{POOL_ITEMS_PATH} not found.")
    version = pool["pool_version"]
    ratings = []
    for directory in sorted(p for p in RATINGS_DIR.iterdir() if p.is_dir()) \
            if RATINGS_DIR.is_dir() else []:
        latest = RecordStore(directory, "ratings", key=lambda r: r["study_id"]).latest()
        rows = [r for r in latest.values() if r.get("pool_version") == version]
        if not args.include_pilot:
            rows = [r for r in rows if r.get("role") != "pilot"]
        ratings += sorted(rows, key=lambda r: r["position"])
        if rows:
            print(f"{directory.name}: {len(rows)} ratings")

    OUT.mkdir(parents=True, exist_ok=True)
    write_json_atomic(OUT / "ratings.json", {"pool_version": version, "ratings": ratings})
    _write_csv(OUT / "ratings.csv", ratings, COLUMNS)
    print(f"{len(ratings)} ratings -> {OUT / 'ratings.json'}")

    if args.with_key:
        key = read_json(STUDY_KEY_PATH)
        if not key or key.get("pool_version") != version:
            sys.exit(f"{STUDY_KEY_PATH} missing or for another pool version.")
        joined = []
        for rating in ratings:
            meta = key["items"].get(rating["study_id"], {})
            boxes = meta.get("boxes") or []
            hits = [_hits(c, boxes, args.margin) for c in rating["clicks"]]
            joined.append({
                **rating, **{k: meta.get(k) for k in KEY_COLUMNS},
                "n_boxes": len(boxes),
                "click_hits": hits,
                "edit_found": any(hits),
            })
        _write_csv(OUT / "ratings_with_key.csv", joined,
                   COLUMNS + KEY_COLUMNS + ["n_boxes", "click_hits", "edit_found"])
        print(f"Analyst file -> {OUT / 'ratings_with_key.csv'} (keep private)")


FOLDER_COLUMNS = COLUMNS + [
    "kind", "repeat_of", "item_id", "set", "side", "family", "label", "method", "model",
    "edit_type", "layout", "capture", "doc_id", "localization", "margin_px", "regions_total",
    "regions_found", "localization_score", "edit_found", "false_alarm",
    # study/outcome.py (doc 12 §6): score and clicks combined, nothing changed in the score
    "hits", "misses", "false_alarm_clicks", "edit_visibility", "outcome", "first_click_hit",
    "edit_visibility_strict"]
MEASURE_KEYS = ("hits", "misses", "false_alarm_clicks", "edit_visibility", "outcome", "first_click_hit",
                "edit_visibility_strict")


def _export_folder_mode(pools, include_pilot: bool) -> None:
    rows = []
    for rater, pool in sorted(pools.items()):
        directory = RATINGS_DIR / rater
        if not directory.is_dir():
            continue
        latest = RecordStore(directory, "ratings", key=lambda r: r["study_id"]).latest()
        mine = [r for r in latest.values() if r.get("pool_version") == pool.version
                and (include_pilot or r.get("role") != "pilot")]
        for rating in sorted(mine, key=lambda r: r["position"]):
            entry, item = pool.items.get(rating["study_id"], {}), pool.meta.get(
                rating["study_id"], {})
            measures = page_measures(item.get("label"), rating["score"], rating.get("clicks") or [],
                                     rating.get("regions_found"), rating.get("regions_total"))
            rows.append({
                **rating, **{k: measures[k] for k in MEASURE_KEYS},
                "kind": entry.get("kind"), "repeat_of": entry.get("repeat_of"),
                **{k: item.get(k) for k in ("set", "side", "family", "method", "model",
                                            "edit_type", "layout", "capture", "doc_id")},
                "item_id": item.get("id"), "label": item.get("label"),
            })
        if mine:
            found = [r for r in mine if r.get("label") == "tampered"]
            rate = sum(bool(r.get("edit_found")) for r in found) / max(1, len(found))
            print(f"{rater}: {len(mine)} ratings, edit found on {rate:.0%} of tampered pages")
    OUT.mkdir(parents=True, exist_ok=True)
    write_json_atomic(OUT / "ratings.json", {"mode": "folders", "ratings": rows})
    _write_csv(OUT / "ratings.csv", rows, FOLDER_COLUMNS)
    print(f"{len(rows)} ratings -> {OUT / 'ratings.json'} and ratings.csv "
          "(include labels: keep private)")


def _hits(click, boxes, margin: float) -> bool:
    for x0, y0, x1, y1 in boxes:
        pad = margin * (y1 - y0)
        if x0 - pad <= click["x"] <= x1 + pad and y0 - pad <= click["y"] <= y1 + pad:
            return True
    return False


def _write_csv(path: Path, rows, columns) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v) if isinstance(v, (list, dict)) else v
                             for k, v in row.items()})


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--with-key", action="store_true")
    parser.add_argument("--include-pilot", action="store_true")
    parser.add_argument("--margin", type=float, default=0.5,
                        help="grow each label box by this × its height (default 0.5)")
    return parser.parse_args()


if __name__ == "__main__":
    main()
