#!/usr/bin/env python3
"""Stage 1 outputs for the dataset pipeline.

    python scripts/export_stage1.py

Writes
    data/stage1/selection.json             (refreshed) selected / rejected / pending per set
    results/stage1/<set>/decisions.csv     doc 12 §1.3 format: id,decision,reason,reviewer,
                                           image,date — copy to data/sets/<set>/35_review/
                                           and run `bash slurm/submit.sh 66_apply_review`
    results/stage1/reject_counts.csv       rejects per set × method × reason (doc 11 §7)
    results/stage1/recheck.json            self-agreement (Cohen's kappa), if a re-check ran
"""
from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.datasets import load_dataset_config  # noqa: E402
from core.jsonstore import RecordStore, write_json_atomic  # noqa: E402
from core.manifest import load_manifest, manifest_sha256  # noqa: E402
from core.settings import DECISIONS_DIR, RECHECK_DIR, RESULTS_DIR, SELECTION_PATH  # noqa: E402
from review.selection import cohen_kappa, compute_selection  # noqa: E402

OUT = RESULTS_DIR / "stage1"


def main() -> None:
    items = load_manifest()
    by_id = {i["id"]: i for i in items}
    decisions = RecordStore(DECISIONS_DIR, "decisions", key=lambda r: r["id"]).latest()
    selection = compute_selection(
        items, decisions, manifest_sha256=manifest_sha256(),
        review_only_tampered=load_dataset_config().review_only_tampered,
    )
    write_json_atomic(SELECTION_PATH, selection)

    # decisions.csv lists rejects only (the pipeline keeps everything not listed).
    # A whole-document reject is written once, with the document id.
    for set_name, bucket in selection["sets"].items():
        rows, seen_docs = [], set()
        for detail in selection["rejected_details"]:
            if detail["set"] != set_name:
                continue
            item = by_id[detail["id"]]
            if detail["scope"] == "document":
                if detail["doc_id"] in seen_docs:
                    continue
                seen_docs.add(detail["doc_id"])
                row_id = detail["doc_id"]
            else:
                row_id = item.get("image_id") or item["stem"]
            source = decisions.get(detail["decided_on"] or detail["id"]) or {}
            rows.append({
                "id": row_id, "decision": "reject",
                "reason": detail["reason"] + (f": {detail['note']}" if detail["note"] else ""),
                "reviewer": source.get("reviewer", ""), "image": item["name"],
                "date": (source.get("timestamp") or "")[:10],
            })
        path = OUT / set_name / "decisions.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=["id", "decision", "reason", "reviewer",
                                                    "image", "date"])
            writer.writeheader()
            writer.writerows(rows)
        print(f"{set_name}: {len(bucket['selected'])} kept, {len(bucket['rejected'])} rejected,"
              f" {len(bucket['pending'])} pending -> {path}")

    counts = Counter(
        (d["set"], by_id[d["id"]].get("method") or "", by_id[d["id"]]["label"], d["reason"])
        for d in selection["rejected_details"]
    )
    with (OUT / "reject_counts.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["set", "method", "label", "reason", "images"])
        for key, n in sorted(counts.items()):
            writer.writerow([*key, n])

    _recheck(decisions)
    if not selection["complete"]:
        print("NOTE: Stage 1 is not complete yet.")


def _recheck(decisions) -> None:
    """Agreement per (original reviewer, re-checker): self re-checks have both equal; the
    admin's audit of a helper's decisions has them different."""
    rechecks = RecordStore(RECHECK_DIR, "recheck",
                           key=lambda r: f"{r['id']}|{r['reviewer']}").latest()
    groups: dict = {}
    for record in rechecks.values():
        original = decisions.get(record["id"])
        if original:
            key = (original.get("reviewer", ""), record["reviewer"])
            groups.setdefault(key, []).append((original["decision"], record["decision"],
                                               record["id"]))
    if not groups:
        return
    report = []
    for (reviewer, rechecker), rows in sorted(groups.items()):
        first, second, ids = zip(*rows)
        entry = {
            "reviewer": reviewer, "rechecked_by": rechecker,
            "kind": "self" if reviewer == rechecker else "audit",
            "n": len(rows),
            "agreement": sum(a == b for a, b, _ in rows) / len(rows),
            "cohen_kappa": cohen_kappa(list(first), list(second)),
            "disagreements": [i for a, b, i in rows if a != b],
        }
        report.append(entry)
        print(f"Re-check {entry['kind']:5s} {reviewer} by {rechecker}: n={entry['n']} "
              f"agreement={entry['agreement']:.3f} kappa={entry['cohen_kappa']}")
    write_json_atomic(OUT / "recheck.json", report)


if __name__ == "__main__":
    main()
