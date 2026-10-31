#!/usr/bin/env python3
"""Analyse the merged Stage 1 result (data/stage1/decisions + manifest). Read-only.

    python scripts/stage1_analysis.py [--out results/stage1_analysis]

1. What is left: images, documents, layouts per set and label; images per document;
   documents shared by the paired sets; train/val split leakage (document, layout).
2. Why images were rejected: reject rate by edit type, text length, text height, box
   aspect, capture, layout, with and without the strictest reviewer (sensitivity).
3. Label shape: mask fill inside the label box (hand-crafted vs deep learning), a check
   for "rectangle" paste traces a detector could learn as a shortcut.
4. Broken bases by set and layout.
Counts only; no text from the images is printed (Sets 1-2 are real customer data).
"""
from __future__ import annotations

import argparse
import csv
import statistics as stt
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.jsonstore import RecordStore  # noqa: E402
from core.manifest import load_manifest  # noqa: E402
from core.settings import DECISIONS_DIR, RESULTS_DIR, STAGE1_DIR  # noqa: E402
from review.selection import document_rejects, resolve_status  # noqa: E402

SPLIT_DIR = STAGE1_DIR.parent / "stage1_split"
md: list = []


def say(s=""):
    print(s)
    md.append(s)


def pct(a, b):
    return f"{100 * a / b:.1f}%" if b else "-"


def table(rows, fields, path=None):
    say("| " + " | ".join(fields) + " |")
    say("|" + "---|" * len(fields))
    for r in rows:
        say("| " + " | ".join(str(r.get(f, "")) for f in fields) + " |")
    if path:
        with path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)


def family(i):
    return "deep_learning" if i["family"].startswith("deep") else "handcrafted"


def text_len(i):
    bi = (i.get("extra") or {}).get("box_info") or []
    return sum(len(b.get("words_before") or "") for b in bi)


def len_change(i):
    bi = (i.get("extra") or {}).get("box_info") or []
    return sum(len(b.get("words_after") or "") - len(b.get("words_before") or "") for b in bi)


def box_h(i):
    return max((b[3] - b[1] for b in i.get("boxes") or []), default=0)


def box_aspect(i):
    return max(((b[2] - b[0]) / max(1, b[3] - b[1]) for b in i.get("boxes") or []), default=0)


def bin_of(v, edges):
    for lo, hi in zip(edges, edges[1:]):
        if lo <= v < hi:
            return f"{lo}-{hi - 1}" if hi != 10 ** 9 else f">={lo}"
    return "?"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(RESULTS_DIR / "stage1_analysis"))
    ap.add_argument("--strict-reviewer", default="reviewer2", help="left out in the sensitivity columns")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    items = load_manifest()
    by_id = {i["id"]: i for i in items}
    decisions = RecordStore(DECISIONS_DIR, "decisions", key=lambda r: r["id"]).latest()
    doc_rej = document_rejects(by_id, decisions)
    owner = {}
    for f in SPLIT_DIR.glob("reviewer*/assignment.csv"):
        for r in csv.DictReader(f.open(encoding="utf-8")):
            owner[f"{r['set']}/{Path(r['file']).name}"] = f.parent.name
    st, rec_of = {}, {}
    for i in items:
        s, rec = resolve_status(i, decisions, doc_rej, review_only_tampered=True)
        st[i["id"]], rec_of[i["id"]] = s, rec
    sets = sorted({i["set"] for i in items})
    T = [i for i in items if i["label"] == "tampered"]

    say("# Stage 1 analysis (merged decisions)\n")
    say(f"Decisions: {len(decisions)} images decided; status: {dict(Counter(st.values()))}\n")

    # ------------------------------------------------------------------ 1
    say("## 1. What is left: images, documents, layouts\n")
    rows = []
    for s in sets:
        for lab in ("tampered", "authentic"):
            sel = [i for i in items if i["set"] == s and i["label"] == lab]
            kept = [i for i in sel if st[i["id"]] == "keep"]
            rows.append({"set": s, "label": lab, "images_kept": len(kept),
                         "documents": len({i["doc_id"] for i in kept}),
                         "layouts": len({i["layout"] for i in kept}),
                         "images_per_document": f"{len(kept) / max(1, len({i['doc_id'] for i in kept})):.2f}",
                         "duplicate_files": sum(1 for i in kept if i.get("duplicate_of"))})
    table(rows, ["set", "label", "images_kept", "documents", "layouts", "images_per_document", "duplicate_files"],
          out / "left_by_set.csv")
    say("\nduplicate_files = Set2/Set4 authentic pages that are the very same files as Set1/Set3 authentic.\n")

    say("Images per document (all kept images of the side, both paired sets, authentic counted once):\n")
    rows = []
    for side in ("real", "synthetic"):
        per_doc = Counter(i["doc_id"] for i in items if i["side"] == side and st[i["id"]] == "keep"
                          and not i.get("duplicate_of"))
        tam_doc = Counter(i["doc_id"] for i in T if i["side"] == side and st[i["id"]] == "keep")
        all_docs = {i["doc_id"] for i in items if i["side"] == side}
        h = Counter(per_doc.values())
        rows.append({"side": side, "documents": len(all_docs), "documents_kept": len(per_doc),
                     "kept_images": sum(per_doc.values()),
                     "docs_with_0_tampered_kept": len(all_docs) - len(tam_doc),
                     "max_images_one_doc": max(per_doc.values(), default=0),
                     "histogram images:docs": " ".join(f"{k}:{v}" for k, v in sorted(h.items()))})
    table(rows, ["side", "documents", "documents_kept", "kept_images", "docs_with_0_tampered_kept",
                 "max_images_one_doc", "histogram images:docs"], out / "per_document.csv")

    say("\nSplit leakage check (syn_train / syn_val / real_test):\n")
    split_docs, split_lay = defaultdict(set), defaultdict(set)
    for i in items:
        sp = (i.get("extra") or {}).get("split")
        split_docs[sp].add((i["side"], i["doc_id"]))
        split_lay[sp].add(i["layout"])
    sp = sorted(split_docs, key=str)
    for x in range(len(sp)):
        for y in range(x + 1, len(sp)):
            say(f"- {sp[x]} vs {sp[y]}: shared documents {len(split_docs[sp[x]] & split_docs[sp[y]])}, "
                f"shared layouts {len(split_lay[sp[x]] & split_lay[sp[y]])}")
    for s_ in sp:
        kept = [i for i in items if (i.get("extra") or {}).get("split") == s_ and st[i["id"]] == "keep"
                and not i.get("duplicate_of")]
        say(f"- {s_}: {len(kept)} kept images, {len({i['doc_id'] for i in kept})} documents, "
            f"{len({i['layout'] for i in kept})} layouts")

    # ------------------------------------------------------------------ 2
    say("\n## 2. Reject rate of decided tampered images by factor\n")
    say(f"`all` = all reviewers; `w/o {a.strict_reviewer}` = the same without the strictest reviewer.\n")

    def who(i):
        r = rec_of[i["id"]]
        return owner.get(r["id"], "?") if r else "?"

    def rates(group_fn, fam, name, min_n=20):
        g = defaultdict(Counter)
        for i in T:
            if family(i) != fam or st[i["id"]] == "pending":
                continue
            k = group_fn(i)
            g[k][st[i["id"]]] += 1
            if who(i) != a.strict_reviewer:
                g[k]["s_" + st[i["id"]]] += 1
        rows = []
        for k, c in sorted(g.items(), key=lambda kv: str(kv[0])):
            n = c["keep"] + c["reject"]
            if n < min_n:
                continue
            rows.append({name: k, "n": n, "reject_all": pct(c["reject"], n),
                         f"w/o {a.strict_reviewer}": pct(c["s_reject"], c["s_keep"] + c["s_reject"]),
                         "n_w/o": c["s_keep"] + c["s_reject"]})
        return rows

    factors = [
        ("model", lambda i: i.get("model") or i.get("method")),
        ("edit_type", lambda i: i.get("edit_type") or "-"),
        ("chars_before", lambda i: bin_of(text_len(i), [0, 5, 9, 13, 21, 10 ** 9])),
        ("length_change", lambda i: "shorter" if len_change(i) < 0 else ("same" if len_change(i) == 0 else "longer")),
        ("text_height_px", lambda i: bin_of(int(box_h(i)), [0, 25, 35, 45, 60, 10 ** 9])),
        ("box_aspect_w/h", lambda i: bin_of(int(box_aspect(i)), [0, 3, 6, 10, 16, 10 ** 9])),
        ("n_edits", lambda i: (i.get("extra") or {}).get("n_edits")),
        ("capture", lambda i: i.get("capture")),
        ("side", lambda i: i["side"]),
    ]
    for fam in ("deep_learning", "handcrafted"):
        say(f"\n### {fam}\n")
        for name, fn in factors:
            rows = rates(fn, fam, name)
            if len(rows) > 1:
                say(f"\n**{name}**\n")
                table(rows, [name, "n", "reject_all", f"w/o {a.strict_reviewer}", "n_w/o"],
                      out / f"reject_{fam}_{name.replace('/', '')}.csv")

    say("\n**Deep learning, model x text height (w/o strict reviewer)**\n")
    g = defaultdict(Counter)
    for i in T:
        if family(i) == "deep_learning" and st[i["id"]] != "pending" and who(i) != a.strict_reviewer:
            g[(i["model"].split(" ")[0], bin_of(int(box_h(i)), [0, 25, 35, 45, 60, 10 ** 9]))][st[i["id"]]] += 1
    hb = sorted({k[1] for k in g}, key=lambda x: int(x.split("-")[0].lstrip(">=")))
    rows = []
    for m in sorted({k[0] for k in g}):
        row = {"model": m}
        for b in hb:
            c = g[(m, b)]
            row[b] = f"{pct(c['reject'], c['keep'] + c['reject'])} ({c['keep'] + c['reject']})"
        rows.append(row)
    table(rows, ["model"] + hb, out / "reject_dl_model_x_height.csv")

    say("\n**Reject reasons by model (decided tampered, own decisions)**\n")
    g = defaultdict(Counter)
    for i in T:
        if st[i["id"]] == "reject":
            g[i.get("model") or i.get("method")][rec_of[i["id"]].get("reason")] += 1
    reasons = sorted({r for c in g.values() for r in c})
    table([{"group": k, **c} for k, c in sorted(g.items())], ["group"] + reasons, out / "reasons_by_model.csv")

    say("\n**Layouts with the highest reject rate (hand-crafted, n >= 30)** — a layout-specific bug shows here\n")
    rows = rates(lambda i: i["layout"], "handcrafted", "layout", min_n=30)
    rows.sort(key=lambda r: -float(r["reject_all"].rstrip("%")) if r["reject_all"] != "-" else 0)
    table(rows[:12], ["layout", "n", "reject_all", f"w/o {a.strict_reviewer}"], out / "reject_hc_layout.csv")

    # ------------------------------------------------------------------ 3
    say("\n## 3. Mask fill inside the label box (kept tampered images)\n")
    say("fill = changed pixels / box area. Near 1.0 means a whole rectangle was repainted (paste trace).\n")
    g = defaultdict(list)
    for i in T:
        if st[i["id"]] != "keep" or not i.get("boxes") or not i.get("width"):
            continue
        area = sum((b[2] - b[0]) * (b[3] - b[1]) for b in i["boxes"])
        frac = (i.get("extra") or {}).get("tampered_area_fraction")
        if frac is None or not area:
            continue
        g[(i["set"], i.get("model") or i.get("method"))].append(frac * i["width"] * i["height"] / area)
    rows = []
    for k, v in sorted(g.items()):
        q = stt.quantiles(v, n=4) if len(v) > 3 else [0, 0, 0]
        rows.append({"set": k[0], "group": k[1], "n": len(v), "q25": f"{q[0]:.2f}", "median": f"{q[1]:.2f}",
                     "q75": f"{q[2]:.2f}", "share_fill>0.9": pct(sum(x > 0.9 for x in v), len(v))})
    table(rows, ["set", "group", "n", "q25", "median", "q75", "share_fill>0.9"], out / "mask_fill.csv")

    # ------------------------------------------------------------------ 4
    say("\n## 4. Whole-document rejects (broken_base)\n")
    rows = Counter()
    for (side, doc), r in doc_rej.items():
        it = by_id[r["id"]]
        rows[(side, it["layout"])] += 1
    side_docs = Counter((i["side"], i["layout"]) for i in items if i["label"] == "authentic" and not i.get("duplicate_of"))
    rr = [{"side": s, "layout": l, "documents_rejected": n, "documents_in_layout": side_docs[(s, l)],
           "share": pct(n, side_docs[(s, l)])} for (s, l), n in rows.most_common()]
    table(rr[:15], ["side", "layout", "documents_rejected", "documents_in_layout", "share"], out / "broken_base.csv")
    say(f"\n{len(doc_rej)} documents rejected as a whole ({sum(1 for k in doc_rej if k[0] == 'real')} real side, "
        f"{sum(1 for k in doc_rej if k[0] == 'synthetic')} synthetic side), over {len(rows)} layouts.")

    (out / "analysis.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"\nwrote {out}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
