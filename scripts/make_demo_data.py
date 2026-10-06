#!/usr/bin/env python3
"""Make a small fake set1..set4 to try both websites without touching the real data.

    python scripts/make_demo_data.py            # writes data/demo/ + config/*.demo.json
    DATA_DIR=data/demo_run DATASETS_CONFIG=config/datasets.demo.json python review_app.py

Every page is invented (no real names or numbers). The layout copies the real sets:

    data/demo/set1/30_dataset/images/<layout>__<doc>_c0.jpg        authentic
    data/demo/set1/30_dataset/images/<layout>__<doc>_t0_c0.jpg     tampered (t0 / t1)
    data/demo/set1/30_dataset/masks/<same stem>.png               black for authentic
    data/demo/set1/30_dataset/labels.jsonl                         one row per image

Sets 3 and 4 use anonymised hex file names and a `group` column as the document id, as in
the release. Set 1/2 share the real-side documents and Set 3/4 the synthetic ones.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SETS = {
    "set1": ("real", "handcrafted", ["m1_copymove", "m3_retypeset", "m7_pdf_edit"]),
    "set2": ("real", "deeplearning", ["TextCtrl (NeurIPS 2024)", "UDiffText (ECCV 2024)",
                                      "AnyText v1.1.3 (ICLR 2024)"]),
    "set3": ("synthetic", "handcrafted", ["m1_copymove", "m3_retypeset", "m7_pdf_edit"]),
    "set4": ("synthetic", "deeplearning", ["TextCtrl (NeurIPS 2024)", "UDiffText (ECCV 2024)",
                                           "AnyText v1.1.3 (ICLR 2024)"]),
}
LAYOUTS = ["afbq_v1", "bank_a_v2", "bank_b_v1", "bank_c_v1", "bank_d_v1", "bank_e_v2",
           "bank_f_v1", "bank_g_v1", "bank_h_v3", "bank_i_v1", "bank_j_v1", "bank_k_v1"]
FIELDS = [("Account No", "account_no"), ("Statement Date", "date"), ("Customer", "name"),
          ("Branch", "address"), ("Opening Balance", "amount")]
W, H = 1200, 400


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--docs-per-side", type=int, default=150)
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "demo")
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()
    rng = random.Random(args.seed)
    if args.out.exists():
        shutil.rmtree(args.out)

    for side in ("real", "synthetic"):
        docs = [hashlib.md5(f"{side}{n}".encode()).hexdigest()[:10]
                for n in range(args.docs_per_side)]
        pages = {doc: _page(doc, rng) for doc in docs}
        for set_name, (set_side, family, methods) in SETS.items():
            if set_side != side:
                continue
            base = args.out / set_name / "30_dataset"
            (base / "images").mkdir(parents=True)
            (base / "masks").mkdir()
            rows = []
            for doc in docs:
                image, lines, layout = pages[doc]
                rows.append(_save(base, set_name, side, family, layout, doc, None, image,
                                  None, None, None))
                for variant in (0, 1):
                    if rng.random() < 0.15:          # some edits failed QC, as in the sets
                        continue
                    method = rng.choice(methods)
                    tampered, box, edit = _tamper(image, lines, rng)
                    rows.append(_save(base, set_name, side, family, layout, doc, variant,
                                      tampered, box, edit, method))
            (base / "labels.jsonl").write_text(
                "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
            print(f"{set_name}: {len(rows)} images")
    _write_configs(args.out)


def _save(base, set_name, side, family, layout, doc, variant, image, box, edit, method):
    if side == "real":
        stem = f"{layout}__{doc}" + (f"_t{variant}" if variant is not None else "") + "_c0"
    else:
        stem = hashlib.md5(f"{set_name if variant is not None else side}{doc}{variant}"
                           .encode()).hexdigest()[:16]
    ext = "png" if int(hashlib.md5(stem.encode()).hexdigest(), 16) % 5 == 0 else "jpg"
    image.save(base / "images" / f"{stem}.{ext}", quality=88)
    mask = Image.new("L", image.size, 0)
    if box:
        ImageDraw.Draw(mask).rectangle(box, fill=255)
    mask.save(base / "masks" / f"{stem}.png")
    tampered = variant is not None
    dl = family == "deeplearning"
    row = {
        "image": f"images/{stem}.{ext}", "mask": f"masks/{stem}.png",
        "label": "tampered" if tampered else "authentic",
        "boxes": [box] if box else [],
        "box_info": [{"op": edit["category"], "category": edit["category"],
                      "words_before": edit["before"], "words_after": edit["after"]}]
        if tampered else [],
        "set": set_name, "source": side, "tamper_family": family, "layout": layout,
        "capture": {"condition": "jpeg" if ext == "jpg" else "native"},
        "category": edit["category"] if tampered else None,
        "method": ("deep_learning" if dl else method) if tampered else None,
        "model": method if tampered and dl else None,
        "tier": ("dl" if dl else "easy") if tampered else None,
        "n_edits": 1 if tampered else 0,
    }
    if side == "real":
        row["internal_id"] = stem
        row["edits"] = [{"category": edit["category"], "text_before": edit["before"],
                         "text_after": edit["after"]}] if tampered else None
    else:
        row["group"] = doc
    return row


def _page(doc: str, rng: random.Random):
    image = Image.new("RGB", (W, H), (252, 252, 250))
    draw = ImageDraw.Draw(image)
    draw.text((30, 20), f"FICTIONAL BANK {rng.choice('ABCDEFGHIJK')}", fill=(20, 40, 120),
              font=ImageFont.load_default(size=30))
    font = ImageFont.load_default(size=22)
    lines = []
    for n, (label, category) in enumerate(rng.sample(FIELDS, 4)):
        value = (f"{rng.randint(10**9, 10**10 - 1)}" if category == "account_no"
                 else f"{rng.randint(100, 99999):,}.{rng.randint(0, 99):02d}"
                 if category == "amount" else f"DEMO {doc[:6].upper()}")
        y = 100 + n * 65
        draw.text((30, y), f"{label}:", fill=(30, 30, 30), font=font)
        draw.text((330, y), value, fill=(30, 30, 30), font=font)
        lines.append((category, value, y))
    return image, lines, rng.choice(LAYOUTS)


def _tamper(image: Image.Image, lines, rng: random.Random):
    out = image.copy()
    draw = ImageDraw.Draw(out)
    category, value, y = rng.choice(lines)
    font = ImageFont.load_default(size=22)
    box = [328, y - 2, 332 + int(font.getlength(value)), y + 27]
    draw.rectangle(box, fill=(252, 252, 250))
    new = "".join(str(rng.randint(0, 9)) if c.isdigit() else c for c in value)
    draw.text((330, y), new, fill=(30, 30, 30), font=font)
    return out, box, {"category": category, "before": value, "after": new}


def _write_configs(out: Path) -> None:
    datasets = json.loads((ROOT / "config" / "datasets.json").read_text())
    datasets["root"] = out.relative_to(ROOT).as_posix()
    (ROOT / "config" / "datasets.demo.json").write_text(json.dumps(datasets, indent=2))
    study = json.loads((ROOT / "config" / "study.json").read_text())
    for stratum in study["strata"]:
        stratum["n"] = 30 if stratum["label"] == "tampered" else 34
    study.update({"repeats": 6, "session_size": 40, "pilot_size": 10})
    (ROOT / "config" / "study.demo.json").write_text(json.dumps(study, indent=2))
    print("wrote config/datasets.demo.json and config/study.demo.json")


if __name__ == "__main__":
    main()
