#!/usr/bin/env python3
"""Stage 2 outputs: every rater's latest rating per study image, as JSON and CSV.

    python scripts/export_ratings.py              # blinded: study ids only
    python scripts/export_ratings.py --with-key   # analyst: joins set, label, method, boxes
    python scripts/export_ratings.py --include-pilot

Writes results/stage2/ratings.json and ratings.csv (doc 12 §8 columns: rater, study_id,
no_traces, clicks, score, time_ms, session, timestamp, max_zoom, + position, role);
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
from core.settings import POOL_ITEMS_PATH, RATINGS_DIR, RESULTS_DIR, STUDY_KEY_PATH  # noqa: E402

OUT = RESULTS_DIR / "stage2"
COLUMNS = ["rater", "role", "study_id", "no_traces", "clicks", "score", "time_ms", "session",
           "position", "timestamp", "max_zoom", "pool_version"]
KEY_COLUMNS = ["kind", "repeat_of", "stratum", "source_id", "set", "side", "family", "label",
               "method", "model", "edit_type", "layout", "capture", "doc_id"]


def main() -> None:
    args = _parse_args()
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
        ratings += sorted(rows, key=lambda r: (r["session"], r["position"]))
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
                "edit_found": any(h and c["kind"] == "edit"
                                  for h, c in zip(hits, rating["clicks"])),
            })
        _write_csv(OUT / "ratings_with_key.csv", joined,
                   COLUMNS + KEY_COLUMNS + ["n_boxes", "click_hits", "edit_found"])
        print(f"Analyst file -> {OUT / 'ratings_with_key.csv'} (keep private)")


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
