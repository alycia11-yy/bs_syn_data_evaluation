#!/usr/bin/env python3
"""Stage 2 — draw the 1,000-image realism pool from the Stage 1 selection.

    python scripts/build_study_pool.py               # needs a complete Stage 1
    python scripts/build_study_pool.py --allow-incomplete   # dry runs / pilot only

Writes
    data/stage2/pool/study_items.json   what the study website loads (blinded)
    data/stage2/pool/images/S0001.png   every image renamed to its study id
    data/stage2/key/study_key.json      study id -> set, label, method, boxes ... (analyst
    data/stage2/key/study_key.csv       only — never copy key/ to the study server)
    data/stage2/key/frozen_selection.json  the exact Stage 1 selection the pool came from

Controls (optional): put the author's crude edits in data/controls/crude/ and clean
authentic pages in data/controls/clean/ (images; optional boxes.json {file: [[x0,y0,x1,y1]]}).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.datasets import load_dataset_config  # noqa: E402
from core.images import to_png_bytes  # noqa: E402
from core.jsonstore import read_json, write_json_atomic  # noqa: E402
from core.manifest import load_manifest, manifest_sha256  # noqa: E402
from core.settings import (  # noqa: E402
    KEY_DIR,
    POOL_DIR,
    POOL_IMAGES_DIR,
    POOL_ITEMS_PATH,
    PROJECT_ROOT,
    RATINGS_DIR,
    SELECTION_PATH,
    STUDY_CONFIG_PATH,
    STUDY_KEY_PATH,
)
from review.selection import now_iso, selected_ids  # noqa: E402
from study.sampling import assign_study_ids, draw_pool  # noqa: E402

_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp"}
_KEY_FIELDS = ("study_id", "kind", "repeat_of", "stratum", "source_id", "set", "side", "family",
               "label", "method", "model", "edit_type", "layout", "capture", "doc_id", "image_id",
               "width", "height", "boxes", "file", "sha256")


def main() -> None:
    args = _parse_args()
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    if args.seed is not None:
        config["seed"] = args.seed

    selection = read_json(SELECTION_PATH)
    if selection is None:
        sys.exit(f"{SELECTION_PATH} not found — finish Stage 1 (python review_app.py) first.")
    if not selection.get("complete") and not args.allow_incomplete:
        pending = sum(c["pending"] for c in selection["counts"].values())
        sys.exit(f"Stage 1 is not complete ({pending} images pending). "
                 "Finish it, or pass --allow-incomplete for a dry run.")
    if POOL_ITEMS_PATH.exists() and not args.force:
        if RATINGS_DIR.is_dir() and any(RATINGS_DIR.iterdir()):
            sys.exit("Ratings already exist for the current pool. Refusing to redraw it "
                     "(the protocol freezes the pool). Use --force only if you mean it.")
        sys.exit(f"{POOL_ITEMS_PATH} exists. Pass --force to redraw.")

    items = load_manifest()
    if selection.get("manifest_sha256") and selection["manifest_sha256"] != manifest_sha256():
        print("WARNING: the manifest changed since selection.json was written.")
    by_id = {i["id"]: i for i in items}
    kept = [by_id[i] for i in selected_ids(selection) if i in by_id]
    picked, warnings = draw_pool(kept, config)
    controls = _load_controls(PROJECT_ROOT / config.get("controls_dir", "data/controls"))
    entries = assign_study_ids(picked, controls, int(config.get("repeats", 0)), config["seed"])

    if POOL_DIR.exists():
        shutil.rmtree(POOL_DIR)
    POOL_IMAGES_DIR.mkdir(parents=True)
    dataset_root = load_dataset_config().root
    pool_items, key_rows = [], []
    for entry in sorted(entries, key=lambda e: e["study_id"]):
        source = (dataset_root / entry["item"]["file"]) if "item" in entry else entry[
            "control"]["path"]
        image_name = _copy_image(source, entry["study_id"])
        meta = entry.get("item") or entry["control"]
        pool_items.append({
            "study_id": entry["study_id"], "image": image_name, "kind": entry["kind"],
            "repeat_of": entry.get("repeat_of"),
            "width": meta.get("width"), "height": meta.get("height"),
        })
        key_rows.append(_key_row(entry, meta))

    pool_version = hashlib.sha256(
        json.dumps(pool_items, sort_keys=True).encode()).hexdigest()[:12]
    write_json_atomic(POOL_ITEMS_PATH, {
        "pool_version": pool_version,
        "created_at": now_iso(),
        "seed": config["seed"],
        "session_size": config.get("session_size", 150),
        "pilot_size": config.get("pilot_size", 50),
        "max_clicks": config.get("max_clicks", 3),
        "zoom_steps": config.get("zoom_steps", [100, 125, 150, 175, 200]),
        "items": pool_items,
    })
    KEY_DIR.mkdir(parents=True, exist_ok=True)
    write_json_atomic(STUDY_KEY_PATH, {
        "pool_version": pool_version, "created_at": now_iso(), "config": config,
        "manifest_sha256": manifest_sha256(), "selection_generated_at":
        selection.get("generated_at"), "warnings": warnings,
        "items": {row["study_id"]: row for row in key_rows},
    })
    write_json_atomic(KEY_DIR / "frozen_selection.json", selection)
    with (KEY_DIR / "study_key.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_KEY_FIELDS)
        writer.writeheader()
        for row in key_rows:
            writer.writerow({**row, "boxes": json.dumps(row.get("boxes") or [])})

    _report(entries, warnings, pool_version)


def _key_row(entry, meta) -> dict:
    row = {k: meta.get(k) for k in _KEY_FIELDS if k in meta}
    row.update({
        "study_id": entry["study_id"], "kind": entry["kind"],
        "repeat_of": entry.get("repeat_of"), "stratum": entry["stratum"],
        "source_id": meta.get("id"), "file": meta.get("file"),
    })
    return row


def _copy_image(source: Path, study_id: str) -> str:
    """PNG / TIFF are re-encoded losslessly to PNG, which also drops embedded text such as
    generator settings. JPEG is copied byte for byte so its compression is untouched."""
    if source.suffix.lower() in {".jpg", ".jpeg"}:
        name = f"{study_id}.jpg"
        shutil.copyfile(source, POOL_IMAGES_DIR / name)
    else:
        name = f"{study_id}.png"
        (POOL_IMAGES_DIR / name).write_bytes(to_png_bytes(source))
    return name


def _load_controls(folder: Path) -> list:
    controls = []
    for sub, label in (("crude", "tampered"), ("clean", "authentic")):
        directory = folder / sub
        if not directory.is_dir():
            continue
        boxes = read_json(directory / "boxes.json", {}) or {}
        for path in sorted(p for p in directory.iterdir() if p.suffix.lower() in _IMAGE_EXT):
            from PIL import Image

            with Image.open(path) as img:
                width, height = img.size
            controls.append({
                "stratum": f"control_{sub}", "path": path, "id": f"control/{sub}/{path.name}",
                "file": path.relative_to(PROJECT_ROOT).as_posix(), "label": label,
                "side": "control", "set": "control", "boxes": boxes.get(path.name, []),
                "width": width, "height": height,
            })
    return controls


def _report(entries, warnings, version) -> None:
    from collections import Counter

    print(f"Pool {version}: {len(entries)} entries -> {POOL_ITEMS_PATH}")
    for (kind, stratum), count in sorted(Counter((e["kind"], e["stratum"]) for e in entries
                                                 ).items()):
        print(f"  {kind:8s} {stratum:24s} {count}")
    for warning in warnings:
        print(f"WARNING: {warning}")
    print(f"Key (keep private): {STUDY_KEY_PATH}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--config", default=str(STUDY_CONFIG_PATH))
    parser.add_argument("--seed", type=int)
    parser.add_argument("--allow-incomplete", action="store_true")
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    main()
