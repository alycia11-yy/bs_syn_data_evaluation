#!/usr/bin/env python3
"""Carry the Stage 1 decisions over to the fixed images (bs_syn_data docs/10 §7d). Read-only on decisions.

    # before replacing data/set1..4 with the fixed sets:
    cp data/stage1/manifest.jsonl data/stage1/_set_aside/manifest_before_fixes.jsonl
    # replace data/set1..4/30_dataset with the new ones, then
    python scripts/build_manifest.py
    python scripts/stage1_carryover.py [--before data/stage1/_set_aside/manifest_before_fixes.jsonl] \
        [--fixes-csv ~/Downloads/bs_syn_data_fixes/stage1_fixes/images.csv]

Checks that every reviewed image id still exists, counts per set how many image files changed (deep-learning
re-composite, rebuilt layout) or stayed identical, flags images whose edit text changed (their decision was made
on a different edit: look at them), and recomputes the selection with the same decisions. Writes
results/stage1_carryover/{summary.md, changed.csv, to_check.txt}. to_check.txt = the tampered images to look at
in the review app (python review_app.py --only results/stage1_carryover/to_check.txt): new images without a
decision, plus (with --fixes-csv, bs_syn_data's fixes report) images whose edit changed or whose model ran
again. The decisions themselves are never modified.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.jsonstore import RecordStore  # noqa: E402
from core.manifest import load_manifest  # noqa: E402
from core.settings import DECISIONS_DIR, RESULTS_DIR, STAGE1_DIR  # noqa: E402
from review.selection import compute_selection  # noqa: E402


def edits_of(item: dict) -> list:
    return [(b.get("words_before"), b.get("words_after")) for b in (item.get("extra") or {}).get("box_info") or []]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--before", default=str(STAGE1_DIR / "_set_aside" / "manifest_before_fixes.jsonl"))
    ap.add_argument("--out", default=str(RESULTS_DIR / "stage1_carryover"))
    ap.add_argument("--fixes-csv", help="bs_syn_data data/sets/_fixes/stage1_fixes/images.csv (downloaded)")
    a = ap.parse_args()
    before = {r["id"]: r for r in map(json.loads, Path(a.before).read_text().splitlines()) if r}
    now_items = load_manifest()
    now = {r["id"]: r for r in now_items}
    decisions = RecordStore(DECISIONS_DIR, "decisions", key=lambda r: r["id"]).latest()
    sel = compute_selection(now_items, decisions, review_only_tampered=True)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rows, md = [], ["# Stage 1 carry-over to the fixed images\n"]
    missing = [i for i in before if i not in now]
    new = [i for i in now if i not in before]
    undecided_new = [i for i in new if now[i]["label"] == "tampered" and i not in decisions]
    md.append(f"- images before {len(before)}, now {len(now)}; missing now {len(missing)}, new {len(new)} "
              f"(new tampered without a decision: {len(undecided_new)})")
    st = Counter()
    for i, r in now.items():
        b = before.get(i)
        if not b:
            continue
        changed = b.get("sha256") != r.get("sha256")
        ed = edits_of(b) != edits_of(r)
        key = (r["set"], r["side"], r["family"], r["label"], "changed" if changed else "identical")
        st[key] += 1
        if changed:
            d = decisions.get(i) or decisions.get(r.get("duplicate_of") or "") or {}
            rows.append({"id": i, "set": r["set"], "label": r["label"], "layout": r.get("layout"),
                         "model_or_method": r.get("model") or r.get("method"), "edit_changed": ed,
                         "decision": d.get("decision", "auto-keep" if r["label"] == "authentic" else "none"),
                         "reviewer": d.get("reviewer", "")})
    md += ["", "| set | side | family | label | identical | changed |", "|---|---|---|---|---|---|"]
    for s, side, fam, lab in sorted({k[:4] for k in st}):
        md.append(f"| {s} | {side} | {fam} | {lab} | {st[(s, side, fam, lab, 'identical')]} | "
                  f"{st[(s, side, fam, lab, 'changed')]} |")
    flagged = [r for r in rows if r["edit_changed"]]
    md += ["", f"- changed images: {len(rows)}; of these the edit text changed (rebuilt layout): {len(flagged)} "
           f"(kept by Stage 1: {sum(r['decision'] == 'keep' for r in flagged)}) -> look at them (changed.csv)",
           "- selection with the same decisions: " + ", ".join(
               f"{s} {c['selected']} kept / {c['rejected']} rejected / {c['pending']} pending"
               for s, c in sorted(sel['counts'].items()))]
    check = {i: "new, no decision" for i in undecided_new}
    if a.fixes_csv:
        by_stem = {(r["set"], Path(r["name"]).stem): r["id"] for r in now_items}
        n_fx = 0
        for r in csv.DictReader(open(Path(a.fixes_csv).expanduser(), encoding="utf-8")):
            if r.get("edit_changed") != "True" or r.get("status_after") != "ok":
                continue
            stem = r.get("release_id") or r.get("capture_id")
            iid = by_stem.get((r["set"], stem))
            if iid and iid not in check:
                check[iid] = "model ran again" if r.get("model_rerun") == "True" else "edit changed"
                n_fx += 1
        md.append(f"- from the fixes report: {n_fx} images whose edit changed or whose model ran again")
    else:
        for r in rows:
            if r["edit_changed"] and r["label"] == "tampered":
                check.setdefault(r["id"], "edit changed")
    (out / "to_check.txt").write_text("".join(f"{i},{why}\n" for i, why in sorted(check.items())), encoding="utf-8")
    md.append(f"- to look at in the review app: {len(check)} images -> {out / 'to_check.txt'} "
              f"({dict(Counter(check.values()))})")
    with (out / "changed.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else ["id"])
        w.writeheader()
        w.writerows(rows)
    (out / "summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
