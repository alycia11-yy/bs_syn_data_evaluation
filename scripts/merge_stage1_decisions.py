#!/usr/bin/env python3
"""Collect the Stage 1 decisions made on the reviewers' own machines into this one.

Each reviewer's machine writes data/stage1/decisions/decisions.jsonl (and, after a
re-check, data/stage1/recheck/recheck.jsonl). Copy their data/stage1 folders back, e.g.

    returned/reviewer1/stage1/decisions/decisions.jsonl
    returned/reviewer2/stage1/decisions/decisions.jsonl   ...

and merge them:

    python scripts/merge_stage1_decisions.py returned/reviewer1/stage1 returned/reviewer2/stage1 …
    python scripts/merge_stage1_decisions.py returned/*/stage1 --dry-run

Every record is appended to data/stage1/decisions/decisions.jsonl (history kept, already
merged records skipped, so running it twice is harmless); the latest decision per image
wins by timestamp (core/jsonstore.py), whatever the merge order. Records for images
outside the reviewer's own folder, or by another account than expected, are reported.
Then selection.json is rewritten.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.datasets import load_dataset_config  # noqa: E402
from core.jsonstore import RecordStore, write_json_atomic  # noqa: E402
from core.manifest import load_manifest, manifest_sha256  # noqa: E402
from core.settings import (  # noqa: E402
    DECISIONS_DIR,
    MANIFEST_PATH,
    RECHECK_DIR,
    SELECTION_PATH,
    STAGE1_SPLIT_DIR,
)
from review.assign import load_split, load_split_manifests  # noqa: E402
from review.selection import compute_selection  # noqa: E402

STORES = (
    ("decisions/decisions.jsonl", DECISIONS_DIR, "decisions", lambda r: r["id"]),
    ("recheck/recheck.jsonl", RECHECK_DIR, "recheck", lambda r: f"{r['id']}|{r['reviewer']}"),
)


def main() -> None:
    args = _parse_args()
    items = load_manifest() if MANIFEST_PATH.is_file() else load_split_manifests(STAGE1_SPLIT_DIR)
    if not items:
        sys.exit("No manifest here: run scripts/build_manifest.py first.")
    by_id = {i["id"]: i for i in items}
    split = load_split(STAGE1_SPLIT_DIR, items)
    owner = {i: r for r, ids in split.assignments.items() for i in ids}

    for rel, directory, name, key in STORES:
        store = RecordStore(directory, name, key=key)
        seen = {_fingerprint(r) for r in store.history()}
        added, problems = Counter(), []
        for source in args.sources:
            path = Path(source) / rel
            if not path.is_file():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                record = json.loads(line)
                if _fingerprint(record) in seen:
                    continue
                if record.get("id") not in by_id:
                    problems.append(f"{path}: unknown image {record.get('id')}")
                    continue
                expected = owner.get(record["id"])
                if name == "decisions" and expected and record.get("reviewer") not in (
                        expected, *args.admins):
                    problems.append(f"{record['id']}: decided by {record.get('reviewer')}, "
                                    f"but it is in {expected}'s folder")
                if not args.dry_run:
                    store.append({**record, "merged_from": str(path)})
                seen.add(_fingerprint(record))
                added[record.get("reviewer", "?")] += 1
        if added or problems:
            print(f"{name}: {sum(added.values())} new records "
                  + ", ".join(f"{r} {n}" for r, n in sorted(added.items())))
        for problem in problems[:30]:
            print(f"  NOTE {problem}")
        if len(problems) > 30:
            print(f"  … {len(problems) - 30} more")

    if args.dry_run:
        print("Dry run: nothing written.")
        return
    selection = compute_selection(
        items, RecordStore(DECISIONS_DIR, "decisions", key=lambda r: r["id"]).latest(),
        manifest_sha256=manifest_sha256(),
        review_only_tampered=load_dataset_config().review_only_tampered,
    )
    write_json_atomic(SELECTION_PATH, selection)
    print("selection.json:", {s: c for s, c in selection["counts"].items()})


def _fingerprint(record: dict) -> tuple:
    return (record.get("id"), record.get("reviewer"), record.get("timestamp"),
            record.get("decision"), record.get("reason"))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("sources", nargs="+",
                        help="returned data/stage1 folders (containing decisions/…)")
    parser.add_argument("--admins", nargs="*", default=["jiayu"],
                        help="accounts allowed to decide on any folder (default: jiayu)")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    main()
