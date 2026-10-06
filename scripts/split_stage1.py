#!/usr/bin/env python3
"""Split the Stage 1 check between reviewers into one folder each (seeded, reproducible).

    python scripts/split_stage1.py --dry-run                 # show the shares, copy nothing
    python scripts/split_stage1.py                           # reviewer1..reviewer4
    python scripts/split_stage1.py --reviewers ana ben       # other names / another count
    python scripts/split_stage1.py --hardlink                # no extra disk space (same disk)
    python scripts/split_stage1.py --write-manifests         # add manifest.jsonl to old folders

Output (default data/stage1_split/, see review/assign.py):

    split.json                          seed, reviewers, manifest checksum, counts
    reviewerN/manifest.jsonl            this folder's manifest rows: the folder works on its own
    reviewerN/assignment.csv            every file in the folder: set, file, label, doc, to_review
    reviewerN/checksums.sha256          `shasum -a 256 -c checksums.sha256` re-verifies the copy
    reviewerN/set1/30_dataset/{images,masks,labels.jsonl}   … same for set2, set3, set4

Whole documents are dealt out: the authentic page, every tampered version (in both
paired sets), their masks and their labels.jsonl rows land in the same reviewer's folder.
Only the tampered images are reviewed; the authentic pages are there for comparison
(the A key). The account name must equal the folder name (reviewer1 … reviewer4).
Same manifest + same reviewers + same seed = the same split, file for file.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.datasets import load_dataset_config  # noqa: E402
from core.jsonstore import RecordStore, write_json_atomic  # noqa: E402
from core.manifest import load_manifest, manifest_sha256  # noqa: E402
from core.settings import DECISIONS_DIR, STAGE1_SPLIT_DIR  # noqa: E402
from review.assign import (  # noqa: E402
    doc_key,
    load_split,
    split_by_document,
    write_reviewer_manifest,
)
from review.selection import needs_review, now_iso  # noqa: E402

DEFAULT_SEED = 20261006
DEFAULT_REVIEWERS = ["reviewer1", "reviewer2", "reviewer3", "reviewer4"]


def main() -> None:
    args = _parse_args()
    config = load_dataset_config()
    root = config.root
    items = load_manifest()
    if args.write_manifests:
        _write_manifests_only(args.out, items)
        return
    to_review = [i for i in items
                 if not i.get("duplicate_of") and needs_review(i, config.review_only_tampered)]
    shares = split_by_document(to_review, args.reviewers, args.seed)
    by_id = {i["id"]: i for i in items}

    # every file of an assigned document goes with it: authentic page(s) + all versions
    docs_of = {r: {doc_key(by_id[i]) for i in ids} for r, ids in shares.items()}
    owner_of_doc = {d: r for r, docs in docs_of.items() for d in docs}
    review_ids = {i for ids in shares.values() for i in ids}
    files_of: dict = defaultdict(list)
    for item in items:
        reviewer = owner_of_doc.get(doc_key(item))
        if reviewer:
            files_of[reviewer].append(item)

    _print_table(shares, files_of, by_id)
    if args.dry_run:
        return
    out = args.out
    if out.exists() and any(out.iterdir()):
        if not args.force:
            sys.exit(f"{out} is not empty. Pass --force to delete and redo it.")
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    labels = _original_label_rows(config)
    counts = {}
    for reviewer in args.reviewers:
        folder = out / reviewer
        rows, checksums, per_set_rows = [], [], defaultdict(list)
        for item in sorted(files_of[reviewer], key=lambda i: i["id"]):
            digest = _place(root / item["file"], folder / item["file"], args.hardlink)
            if item.get("sha256") and digest != item["sha256"]:
                sys.exit(f"Checksum mismatch after copying {item['file']} — stopping.")
            checksums.append((digest, item["file"]))
            mask = (item.get("views") or {}).get("mask")
            if mask:
                checksums.append((_place(root / mask, folder / mask, args.hardlink), mask))
            label_row = labels.get((item["set"], item["name"]))
            if label_row:
                per_set_rows[label_row[0]].append(label_row[1])
            rows.append({
                "set": item["set"], "file": item["file"], "label": item["label"],
                "doc_id": item.get("doc_id"), "method": item.get("model") or item.get("method"),
                "to_review": "yes" if item["id"] in review_ids else "no",
                "sha256": digest,
            })
        for labels_path, set_rows in per_set_rows.items():
            target = folder / labels_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n"
                                      for r in set_rows), encoding="utf-8")
        _write_csv(folder / "assignment.csv", rows)
        write_reviewer_manifest(folder, sorted(files_of[reviewer], key=lambda i: i["id"]))
        (folder / "checksums.sha256").write_text(
            "".join(f"{d}  {p}\n" for d, p in checksums), encoding="utf-8")
        counts[reviewer] = {
            "to_review": dict(sorted(Counter(r["set"] for r in rows
                                             if r["to_review"] == "yes").items())),
            "files": len(rows), "documents": len(docs_of[reviewer]),
        }

    write_json_atomic(out / "split.json", {
        "seed": args.seed, "reviewers": args.reviewers, "unit": "document",
        "created_at": now_iso(), "manifest_sha256": manifest_sha256(),
        "source_root": str(root), "mode": "hardlink" if args.hardlink else "copy",
        "counts": counts,
    })
    print(f"Done -> {out} (every copy checked against the manifest SHA-256)")
    decided = RecordStore(DECISIONS_DIR, "decisions", key=lambda r: r["id"]).latest()
    if decided:
        print(f"NOTE: {len(decided)} decisions already exist; they stay valid and show as "
              "done in the reviewer's share.")


def _write_manifests_only(out: Path, items) -> None:
    """Add reviewerN/manifest.jsonl to folders made before this file existed (no copying)."""
    split = load_split(out, items)
    if not split.active:
        sys.exit(f"No reviewer folders with images in {out}.")
    by_file = {i["file"]: i for i in items}
    for reviewer in split.assignments:
        mine = sorted((by_file[f] for f, owner in split.file_owner.items()
                       if owner == reviewer and f in by_file), key=lambda i: i["id"])
        write_reviewer_manifest(out / reviewer, mine)
        print(f"{reviewer}: manifest.jsonl with {len(mine)} images")


def _original_label_rows(config) -> dict:
    """(set, image file name) -> (labels.jsonl path relative to root, original row)."""
    out = {}
    for set_cfg in config.sets:
        spec = set_cfg.metadata
        if not spec:
            continue
        path = (set_cfg.folder / spec["file"]).resolve()
        if not path.is_file():
            continue
        rel = path.relative_to(config.root.resolve()).as_posix()
        key = spec.get("key", "file")
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                out[(set_cfg.name, Path(str(row.get(key, ""))).name)] = (rel, row)
    return out


def _place(source: Path, target: Path, hardlink: bool) -> str:
    target.parent.mkdir(parents=True, exist_ok=True)
    if hardlink:
        try:
            os.link(source, target)
        except OSError:
            shutil.copy2(source, target)
    else:
        shutil.copy2(source, target)
    return _sha256(target)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_csv(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["set", "file", "label", "doc_id", "method",
                                                "to_review", "sha256"])
        writer.writeheader()
        writer.writerows(rows)


def _print_table(shares, files_of, by_id) -> None:
    sets = sorted({by_id[i]["set"] for ids in shares.values() for i in ids})
    print("Images to review per reviewer (seeded split by document):")
    print(f"  {'reviewer':12s}" + "".join(f"{s:>7s}" for s in sets)
          + "    total   docs  files in folder")
    for reviewer, ids in shares.items():
        per_set = Counter(by_id[i]["set"] for i in ids)
        docs = len({doc_key(by_id[i]) for i in ids})
        print(f"  {reviewer:12s}" + "".join(f"{per_set.get(s, 0):7d}" for s in sets)
              + f"  {len(ids):7d}  {docs:5d}  {len(files_of[reviewer]):6d}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--reviewers", nargs="+", default=DEFAULT_REVIEWERS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--out", type=Path, default=STAGE1_SPLIT_DIR)
    parser.add_argument("--hardlink", action="store_true",
                        help="hard-link instead of copy (same disk only; falls back to copy)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--write-manifests", action="store_true",
                        help="only add reviewerN/manifest.jsonl to existing folders")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if len(set(args.reviewers)) != len(args.reviewers):
        parser.error("reviewer names must be unique")
    return args


if __name__ == "__main__":
    main()
