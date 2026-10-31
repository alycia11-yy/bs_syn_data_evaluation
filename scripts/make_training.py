#!/usr/bin/env python3
"""Reference examples for the realism study: 15 synthetic pages, genuine and edited, from obvious to invisible
(config/training.json). No expected score: the admin (jiayu) rates them on the website (admin page → Reference
examples); those answers are saved (data/stage2/reference/) and are the reference. "Save reference PDF" writes the
PDF for the raters and $STAGE2_DIR/practice/reference.json (study/reference.py). Raters rate the same pages on
Practice; after each page they see their own measures, the reference answer and the note. Their practice answers
are not saved.

    python scripts/make_training.py                 # build from config/training.json
    STAGE2_DIR=data/stage2_test python scripts/make_training.py      # the same, for a test run
    python scripts/make_training.py --candidates    # draw new candidates to choose from (seeded)

Rules
- Only synthetic images (Sets 3 and 4): no real customer data in training material, so the guide can be
  shown before anyone is cleared for Sets 1–2.
- Only documents that are NOT in the study pool (data/stage2_split): no rater sees a training page again in
  the study. Checked every time the training is built; the build stops if a training page is in the pool.
- The choice and the notes are fixed in config/training.json (picked by hand from the seeded candidates), so
  the training is the same for every rater and can be rebuilt exactly.

Writes
    $STAGE2_DIR/practice/practice.json + p01…p15 images + masks/ + originals/   what /practice shows
                                                                  (originals: the pages before the edit, for the PDF)
    docs/rater_guide.md                                           the guide to hand to raters before they start
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import shutil
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw  # noqa: E402

from core.datasets import load_dataset_config  # noqa: E402
from core.jsonstore import write_json_atomic  # noqa: E402
from core.manifest import load_manifest  # noqa: E402
from core.settings import CONFIG_DIR, RESULTS_DIR, STAGE2_DIR, STAGE2_SPLIT_DIR  # noqa: E402

TRAINING = CONFIG_DIR / "training.json"
GUIDE = ROOT / "docs" / "rater_guide.md"
GUIDE_IMG = ROOT / "docs" / "rater_guide"
SEED = "20261006:training"
METHOD_TEXT = {"m1_copymove": "copy-move (pixels copied from elsewhere on the same page)",
               "m3_retypeset": "re-typeset (the old text covered, new text drawn in a matching font)",
               "m7_pdf_edit": "edited in the PDF source before rendering"}


def describe(it: dict) -> str:
    """One line on what the page is, for the reference PDF."""
    capture = {"native": "clean digital copy", "jpeg": "JPEG", "resize_jpeg": "resized JPEG",
               "scan_sim": "simulated scan"}.get(it.get("capture"), it.get("capture") or "")
    if it["label"] != "tampered":
        return f"Genuine synthetic page ({it['set'].replace('set', 'Set ')}) · {capture}"
    if it.get("model"):
        how = f"deep-learning text editing ({it['model']})"
    else:
        how = "hand-crafted: " + METHOD_TEXT.get(it.get("method"), str(it.get("method")))
    return f"Edited synthetic page ({it['set'].replace('set', 'Set ')}) · {how} · {capture}"


def short(it: dict) -> str:
    """A few words for the table on the PDF's cover page."""
    capture = {"native": "clean copy", "jpeg": "JPEG", "resize_jpeg": "resized JPEG",
               "scan_sim": "scan"}.get(it.get("capture"), it.get("capture") or "")
    if it["label"] != "tampered":
        return f"Genuine · {capture}"
    how = (it["model"].split(" ")[0] if it.get("model") else
           {"m1_copymove": "copy-move", "m3_retypeset": "re-typeset", "m7_pdf_edit": "PDF-source edit"}.get(
               it.get("method"), str(it.get("method"))))
    return f"Edited · {how} · {capture}"


def pool_documents() -> set:
    docs = set()
    for f in STAGE2_SPLIT_DIR.glob("*/assignment.csv"):
        for r in csv.DictReader(open(f, encoding="utf-8")):
            docs.add(("real" if r["set"] in ("set1", "set2") else "synthetic", r["doc_id"]))
    return docs


def panel(item: dict, root: Path, out: Path, answer: bool) -> None:
    """What the rater sees around the edit (top) and, with answer=True, the same with the edit boxed."""
    im = Image.open(root / item["file"]).convert("RGB")
    W, H = im.size
    if item["label"] == "tampered" and item.get("boxes"):
        x0, y0, x1, y1 = [int(v) for v in item["boxes"][0]]
        pad = max(60, 3 * (y1 - y0))
        box = (max(0, x0 - 4 * pad), max(0, y0 - pad), min(W, x1 + 4 * pad), min(H, y1 + pad))
        crop = im.crop(box)
        rows = [crop]
        if answer:
            mark = crop.copy()
            d = ImageDraw.Draw(mark)
            for b in item["boxes"]:
                d.rectangle([b[0] - box[0], b[1] - box[1], b[2] - box[0], b[3] - box[1]], outline=(220, 0, 0),
                            width=3)
            rows.append(mark)
        canvas = Image.new("RGB", (crop.width, sum(r.height for r in rows) + 8 * (len(rows) - 1)), (150, 150, 150))
        y = 0
        for r in rows:
            canvas.paste(r, (0, y))
            y += r.height + 8
        im = canvas
    im.thumbnail((1400, 1400))
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out, optimize=True)


def candidates(items: list, root: Path, pool: set) -> int:
    """Seeded candidates per kind (authentic per capture, tampered per method / model and tier) from synthetic
    documents outside the pool -> results/_training_candidates/ (c01.png …, candidates.csv) to choose from."""
    cand = [i for i in items if i["side"] == "synthetic" and not i.get("duplicate_of")
            and (i["side"], i["doc_id"]) not in pool]

    def kind(i):
        if i["label"] == "authentic":
            return "authentic:" + str(i.get("capture"))
        return f"{i['set']}:{i.get('model') or i.get('method')}:{(i.get('extra') or {}).get('tier')}"

    groups = defaultdict(list)
    for i in sorted(cand, key=lambda i: i["id"]):
        groups[kind(i)].append(i)
    rng = random.Random(SEED)
    picks, used = [], set()
    for k in sorted(groups):
        rng.shuffle(groups[k])
        n = 0
        for i in groups[k]:
            if i["doc_id"] in used:
                continue
            picks.append(i)
            used.add(i["doc_id"])
            n += 1
            if n >= (2 if k.startswith("authentic") else 3):
                break
    out = RESULTS_DIR / "_training_candidates"
    rows = []
    for n, i in enumerate(picks, 1):
        panel(i, root, out / f"c{n:02d}.png", answer=True)
        rows.append({"cand": f"c{n:02d}", "id": i["id"], "set": i["set"], "label": i["label"], "kind": kind(i),
                     "capture": i.get("capture"), "edits": json.dumps(
                         [(b.get("words_before"), b.get("words_after")) for b in (i.get("extra") or {}).get(
                             "box_info") or []], ensure_ascii=False)})
    with open(out / "candidates.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"{len(picks)} candidates from {len(cand)} synthetic images outside the pool -> {out}")
    return 0


def build(items: list, root: Path, pool: set) -> int:
    by_id = {i["id"]: i for i in items}
    cfg = json.loads(TRAINING.read_text(encoding="utf-8"))
    refs = cfg["reference"]
    problems = []
    for e in refs:
        it = by_id.get(e["id"])
        if it is None:
            problems.append(f"{e['id']}: not in the manifest")
        elif (it["side"], it["doc_id"]) in pool:
            problems.append(f"{e['id']}: its document is in the study pool")
        elif it["side"] != "synthetic":
            problems.append(f"{e['id']}: real-side image (reference pages are synthetic only)")
    if problems:
        print("Reference examples not built:\n  " + "\n  ".join(problems))
        return 1
    pdir = STAGE2_DIR / "practice"
    if pdir.exists():
        shutil.rmtree(pdir)
    (pdir / "masks").mkdir(parents=True)
    out = []
    for n, e in enumerate(refs, 1):
        it = by_id[e["id"]]
        name = f"p{n:02d}{Path(it['file']).suffix}"
        shutil.copy2(root / it["file"], pdir / name)
        mask = (it.get("views") or {}).get("mask")
        row = {"image": name, "label": it["label"], "boxes": it.get("boxes") or [], "width": it.get("width"),
               "source_id": it["id"], "about": describe(it), "short": short(it), "note": e["note"]}
        if mask and (root / mask).exists():
            shutil.copy2(root / mask, pdir / "masks" / f"p{n:02d}.png")
            row["mask"] = f"masks/p{n:02d}.png"
        orig = (it.get("views") or {}).get("authentic")       # the same page before the edit (for the PDF)
        if it["label"] == "tampered" and orig and (root / orig).exists():
            (pdir / "originals").mkdir(exist_ok=True)
            name_o = f"p{n:02d}{Path(orig).suffix}"
            shutil.copy2(root / orig, pdir / "originals" / name_o)
            row["original"] = f"originals/{name_o}"
        out.append(row)
    write_json_atomic(pdir / "practice.json", {"items": out, "from": str(TRAINING.relative_to(ROOT))})
    print(f"reference examples: {len(out)} pages -> {pdir}")
    _guide(refs, by_id, root)
    return 0


def _guide(refs: list, by_id: dict, root: Path) -> None:
    """The rater guide (text only): how to rate, the scale, and the order Practice -> reference PDF -> study."""
    if GUIDE_IMG.exists():                                   # images of the earlier 5-example guide
        shutil.rmtree(GUIDE_IMG)
    md = ["# Rater guide: realism study\n",
          "Thank you for taking part. You will see the top part of bank statements, one at a time. **Some are "
          "genuine and some were edited in one or two places.** We do not tell you how many. For each page, "
          "decide whether you can see any sign of editing.\n",
          "Use only the account you were given. Each account has its own list of pages and its own saved answers.\n",
          "## Before you start\n",
          "1. Read this guide.",
          f"2. Sign in and open **Practice**. Rate the {len(refs)} practice pages. After each one the website shows "
          "whether the page was edited, where (red box), what your answer measured (your clicks on or off the "
          "edit, the outcome), the reference answer and a short note. Practice answers are not saved and do not "
          "count.",
          f"3. Read the **reference PDF** (`reference_examples_….pdf`). It shows the same {len(refs)} pages: the "
          "page before and after the edit side by side, where the study author clicked, the author's score and "
          "what it measured, and a note on what was edited. There is no single right score: the reference is one "
          "careful reader's answer.",
          "4. Start the study (**Go to the study**). Your answers are saved as you go; you can stop and sign in "
          "again later to continue.\n",
          "## What to do on each page\n",
          "1. Look at the whole page. You may zoom up to 200 %. Take the time you need.",
          "2. If you see **no sign of editing**, press **\"No edit on this page\"**. That records score 5.",
          "3. Otherwise **click on each place** you think was edited (up to 3; a click means \"an edit is here\"), "
          "**the place you are most sure about first**, then choose how genuine the **whole page** looks, 1–5. "
          "Where you clicked and how genuine the page looks are separate answers: you may mark a place and still "
          "give 3 or 4 if it only looks slightly odd.",
          "4. **One score per page, not one per place.** If you marked several places, the score is for the page as "
          "a whole: judge by the strongest sign of editing you found (the most convincing place decides), not by "
          "an average of the places and not by how many places you marked.",
          "5. The scale:\n",
          "| score | meaning |", "|---|---|",
          "| 5 · Genuine | Looks like an untouched statement, even on close inspection (\"No edit on this page\"). |",
          "| 4 · Probably genuine | Small oddities that printing, scanning or the bank's own layout could explain. |",
          "| 3 · Unsure | Something is off, but I cannot say whether it is an edit. |",
          "| 2 · Probably edited | A word or number whose font, size, weight, colour, spacing or alignment differs from its surroundings. |",
          "| 1 · Clearly edited | Obvious signs: wrong font, misaligned or overlapping text, smudge or blur around a word, a patch of different background. |",
          "",
          "**How your answer is read.** Your score and your clicks are kept as given; they are never changed. "
          "Each click is checked against where the page was really edited. A low score with a click on the edit "
          "means the edit was seen; a low score with every click elsewhere means the page looked wrong for another "
          "reason and the edit itself was not seen. Clicks on unedited places are counted as false alarms, on "
          "genuine and edited pages alike, so click only where you see something, not everywhere.",
          "",
          "**Good habits**",
          "- Compare a value with the values **around it**: same font, size, weight, colour, spacing, baseline.",
          "- Things that are the same over the **whole page** (blur, tilt, compression, a typeface used for all "
          "values) come from printing or scanning, not from an edit.",
          "- Many edited pages show no trace at all. If you cannot see anything, **5 is the right answer**. Do not "
          "guess, and do not try to find an edit on every page.",
          "- Some pages come back a second time later in your list. Rate them again as if new; do not try to "
          "remember your earlier answer.",
          "- There is no feedback during the study and you cannot go back.",
          "- The documents are confidential: do not copy, photograph or share them.\n",
          f"The {len(refs)} practice and reference pages are synthetic and none of them is in the study.\n"]
    GUIDE.parent.mkdir(parents=True, exist_ok=True)
    GUIDE.write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"rater guide -> {GUIDE}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--candidates", action="store_true", help="draw candidates to choose from instead of building")
    a = ap.parse_args()
    items = load_manifest()
    root = load_dataset_config().root
    pool = pool_documents()
    if not pool:
        print(f"No rater folders in {STAGE2_SPLIT_DIR}: run scripts/split_stage2.py first.")
        return 1
    return candidates(items, root, pool) if a.candidates else build(items, root, pool)


if __name__ == "__main__":
    raise SystemExit(main())
