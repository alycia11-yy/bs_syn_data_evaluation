#!/usr/bin/env python3
"""Make the Stage 2 rater folders from what Stage 1 kept (seeded, reproducible).

    python scripts/split_stage2.py --dry-run               # show the draw, copy nothing
    python scripts/split_stage2.py                         # R1..R4, every rater the same images
    python scripts/split_stage2.py --mode split            # the images divided between raters
    python scripts/split_stage2.py --raters ana ben cy dee --hardlink
    python scripts/split_stage2.py --practice 15           # practice images (default: config)

The images are drawn exactly like build_study_pool.py (config/study.json): only images
kept in Stage 1, one version per document, the six strata (Set 1/2/3/4 tampered,
real / synthetic authentic), balanced over method / model, ≤ 10 % per layout.

--mode shared (default, doc 12 §3): every rater gets the same pool — needed for the
  agreement between raters (Krippendorff's alpha, ICC) and a mean score over 4 raters.
--mode split: each image goes to one rater only (dealt per stratum, so every rater gets
  the same mix). Four times fewer ratings per rater, but no agreement and one rating per
  image.

Output (default data/stage2_split/), the same layout as the Stage 1 reviewer folders:

    split.json                       seed, mode, raters, counts, warnings of the draw
    R1/manifest.jsonl                the rows of these images (+ `stratum`)
    R1/assignment.csv                set, file, label, doc, method / model, stratum
    R1/checksums.sha256              shasum -a 256 -c checksums.sha256
    R1/set1/30_dataset/{images,masks,labels.jsonl}   … set2, set3, set4

Practice round: `--practice N` (default `practice_size` in config/study.json) images
**rejected in Stage 1** — unusable for the study, so nothing is lost by practising on them
— are copied with their masks to data/stage2/practice/ and listed in practice.json.
Tampered rejects first, picked at random with the seed.

The folders hold masks and labels: they stay on the study server (or your machine). Never
send them to a rater — raters only ever see the website, under blinded study ids.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.datasets import load_dataset_config  # noqa: E402
from core.folder_copy import original_label_rows, place  # noqa: E402
from core.jsonstore import read_json, write_json_atomic  # noqa: E402
from core.manifest import load_manifest, manifest_sha256  # noqa: E402
from core.settings import (  # noqa: E402
    SELECTION_PATH,
    STAGE2_DIR,
    STAGE2_SPLIT_DIR,
    STUDY_CONFIG_PATH,
)
from review.assign import write_reviewer_manifest  # noqa: E402
from review.selection import REJECT_REASONS, now_iso, selected_ids  # noqa: E402
from study.sampling import draw_pool  # noqa: E402

DEFAULT_RATERS = ["R1", "R2", "R3", "R4"]
REASON_TEXT = {key: label.lower() for key, label, _ in REJECT_REASONS}


def main() -> None:
    args = _parse_args()
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    selection = read_json(SELECTION_PATH)
    if selection is None:
        sys.exit(f"{SELECTION_PATH} not found — finish Stage 1 (and merge the reviewers' "
                 "decisions) first.")
    if not selection.get("complete") and not args.allow_incomplete:
        pending = sum(c["pending"] for c in selection["counts"].values())
        sys.exit(f"Stage 1 is not complete ({pending} images pending). Finish it, or pass "
                 "--allow-incomplete for a dry run.")

    items = load_manifest()
    by_id = {i["id"]: i for i in items}
    kept = [by_id[i] for i in selected_ids(selection) if i in by_id]
    picked, warnings = draw_pool(kept, config)
    shares = _deal(picked, args.raters, args.mode, config["seed"])
    _print_table(shares, args.mode, warnings)
    if args.dry_run:
        return

    out = args.out
    if out.exists() and any(out.iterdir()):
        if not args.force:
            sys.exit(f"{out} is not empty. Pass --force to delete and redo it.")
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    dataset = load_dataset_config()
    labels = original_label_rows(dataset)
    for rater, share in shares.items():
        _write_folder(out / rater, share, dataset.root, labels, args.hardlink)
    write_json_atomic(out / "split.json", {
        "seed": config["seed"], "mode": args.mode, "raters": args.raters,
        "created_at": now_iso(), "manifest_sha256": manifest_sha256(),
        "selection_generated_at": selection.get("generated_at"),
        "copy": "hardlink" if args.hardlink else "copy", "warnings": warnings,
        "counts": {r: dict(Counter(s for s, _ in share)) for r, share in shares.items()},
    })
    n_practice = args.practice if args.practice is not None else int(
        config.get("practice_size", 1))
    _write_practice(selection, by_id, dataset.root, n_practice, config["seed"])
    print(f"Done -> {out} (every copy checked against the manifest SHA-256)")


def _write_practice(selection, by_id, root: Path, n: int, seed: int) -> None:
    """Practice images = Stage 1 rejects (tampered first), with their masks."""
    if n <= 0:
        return
    rejected = [d for d in selection.get("rejected_details", []) if d["id"] in by_id]
    rng = random.Random(f"{seed}:practice")
    tampered = sorted((d for d in rejected if by_id[d["id"]]["label"] == "tampered"),
                      key=lambda d: d["id"])
    others = sorted((d for d in rejected if by_id[d["id"]]["label"] != "tampered"),
                    key=lambda d: d["id"])
    rng.shuffle(tampered)
    rng.shuffle(others)
    chosen = (tampered + others)[:n]
    if not chosen:
        print("No Stage 1 rejects to practise on — the website will pick practice images "
              "at random from the rater folders.")
        return
    practice_dir = STAGE2_DIR / "practice"
    if practice_dir.exists():
        shutil.rmtree(practice_dir)
    entries = []
    for number, detail in enumerate(chosen, start=1):
        item = by_id[detail["id"]]
        name = f"p{number:02d}{Path(item['file']).suffix}"
        place(root / item["file"], practice_dir / name, hardlink=False)
        entry = {"image": name, "label": item["label"], "boxes": item.get("boxes") or [],
                 "width": item.get("width"), "source_id": item["id"],
                 "note": "Left out of the study in Stage 1: "
                         f"{REASON_TEXT.get(detail.get('reason'), detail.get('reason'))}."}
        mask = (item.get("views") or {}).get("mask")
        if mask:
            place(root / mask, practice_dir / "masks" / f"p{number:02d}.png", hardlink=False)
            entry["mask"] = f"masks/p{number:02d}.png"
        entries.append(entry)
    write_json_atomic(practice_dir / "practice.json", {"items": entries})
    print(f"Practice: {len(entries)} Stage 1 rejects -> {practice_dir}")


def _deal(picked, raters, mode, seed):
    if mode == "shared":
        return {r: list(picked) for r in raters}
    rng = random.Random(f"{seed}:split")
    shares = {r: [] for r in raters}
    by_stratum = defaultdict(list)
    for stratum, item in picked:
        by_stratum[stratum].append((stratum, item))
    offset = 0
    for stratum in sorted(by_stratum):
        entries = sorted(by_stratum[stratum], key=lambda e: e[1]["id"])
        rng.shuffle(entries)
        for n, entry in enumerate(entries):
            shares[raters[(n + offset) % len(raters)]].append(entry)
        offset += len(entries)          # keeps the totals within one image of each other
    return shares


def _write_folder(folder: Path, share, root: Path, labels, hardlink: bool) -> None:
    rows, checksums, per_set_rows, manifest = [], [], defaultdict(list), []
    for stratum, item in sorted(share, key=lambda e: e[1]["id"]):
        digest = place(root / item["file"], folder / item["file"], hardlink)
        if item.get("sha256") and digest != item["sha256"]:
            sys.exit(f"Checksum mismatch after copying {item['file']} — stopping.")
        checksums.append((digest, item["file"]))
        views = {k: v for k, v in (item.get("views") or {}).items() if k == "mask"}
        if views.get("mask"):
            checksums.append((place(root / views["mask"], folder / views["mask"], hardlink),
                              views["mask"]))
        label_row = labels.get((item["set"], item["name"]))
        if label_row:
            per_set_rows[label_row[0]].append(label_row[1])
        manifest.append({**item, "views": views, "duplicate_of": None, "stratum": stratum})
        rows.append({"set": item["set"], "file": item["file"], "label": item["label"],
                     "doc_id": item.get("doc_id"),
                     "method": item.get("model") or item.get("method"),
                     "stratum": stratum, "sha256": digest})
    for rel, set_rows in per_set_rows.items():
        target = folder / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in set_rows),
                          encoding="utf-8")
    write_reviewer_manifest(folder, manifest)
    with (folder / "assignment.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else ["file"])
        writer.writeheader()
        writer.writerows(rows)
    (folder / "checksums.sha256").write_text(
        "".join(f"{d}  {p}\n" for d, p in checksums), encoding="utf-8")


def _print_table(shares, mode, warnings) -> None:
    strata = sorted({s for share in shares.values() for s, _ in share})
    print(f"Stage 2 images per rater ({mode}):")
    print(f"  {'rater':10s}" + "".join(f"{s[:19]:>21s}" for s in strata) + "   total")
    for rater, share in shares.items():
        c = Counter(s for s, _ in share)
        print(f"  {rater:10s}" + "".join(f"{c.get(s, 0):21d}" for s in strata)
              + f"  {len(share):6d}")
    for warning in warnings:
        print(f"WARNING: {warning}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--raters", nargs="+", default=DEFAULT_RATERS)
    parser.add_argument("--mode", choices=("shared", "split"), default="shared")
    parser.add_argument("--config", default=str(STUDY_CONFIG_PATH))
    parser.add_argument("--out", type=Path, default=STAGE2_SPLIT_DIR)
    parser.add_argument("--hardlink", action="store_true")
    parser.add_argument("--practice", type=int,
                        help="practice images from Stage 1 rejects (default: practice_size)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-incomplete", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if len(set(args.raters)) != len(args.raters):
        parser.error("rater names must be unique")
    return args


if __name__ == "__main__":
    main()
