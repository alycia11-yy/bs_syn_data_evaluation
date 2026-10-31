#!/usr/bin/env python3
"""Stage 1 summary: merge the reviewers' decisions READ-ONLY and report what is left.

    python scripts/stage1_summary.py                      # data/stage1/reviewer*/ + data/stage1/decisions/
    python scripts/stage1_summary.py data/stage1/reviewer1 data/stage1/reviewer2/decisions.jsonl ...
    python scripts/stage1_summary.py --verify-files [--sha256]   # also check the image files

It applies the same rules as the review app (review/selection.py): latest decision per
image wins, a whole-document reject (broken_base) removes every version of that page in
both paired sets, authentic images are kept unless their document was rejected. Then it
dry-runs the Stage 2 draw (study/sampling.py, config/study.json) on the kept images.

It never writes data/stage1/decisions or selection.json (merge_stage1_decisions.py does).
Output: results/stage1_summary/ — summary.md, sets.csv, hierarchy.csv, reviewers.csv, reasons.csv,
groups.csv, reviewer_set.csv, rejected.csv, pending.csv, stage2_draw.csv
(rejected.csv carries reviewer notes: internal only.)
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.manifest import load_manifest, manifest_sha256  # noqa: E402
from core.settings import STAGE1_DIR, STUDY_CONFIG_PATH, RESULTS_DIR  # noqa: E402
from review.selection import (  # noqa: E402
    canonical_id, document_rejects, resolve_status)
from study.sampling import draw_pool  # noqa: E402

SPLIT_DIR = STAGE1_DIR.parent / "stage1_split"


def read_jsonl(path: Path):
    if path.suffix == ".json":                    # *_latest.json: {id: record}
        data = json.loads(path.read_text(encoding="utf-8"))
        return list(data.values()) if isinstance(data, dict) else list(data)
    out = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                print(f"  WARN {path}:{n} unreadable line skipped")
    return out


def source_files(sources):
    if not sources:
        # the reviewers' returned files, then the app's own store (merged records + decisions made here,
        # e.g. the images new since the split); identical records are counted once
        sources = sorted(str(p) for p in STAGE1_DIR.glob("reviewer*") if p.is_dir())
        if (STAGE1_DIR / "decisions" / "decisions.jsonl").exists():
            sources.append(str(STAGE1_DIR / "decisions" / "decisions.jsonl"))
    out = []
    for s in sources:
        p = Path(s)
        files = [p / "decisions.jsonl", p / "decisions_latest.json"] if p.is_dir() else [p]
        name = p.name if p.is_dir() else p.parent.name
        found = [f for f in files if f.exists()]
        if not found:
            print(f"  NOTE {p}: no decisions file")
        out += [(name, f) for f in found]
    return out


def group_of(item):
    return item.get("model") or item.get("method") or "-"


def pct(a, b):
    return f"{100 * a / b:.1f}%" if b else "-"


def write_csv(path, rows, fields):
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def md_table(rows, fields):
    out = ["| " + " | ".join(fields) + " |", "|" + "---|" * len(fields)]
    out += ["| " + " | ".join(str(r.get(f, "")) for f in fields) + " |" for r in rows]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sources", nargs="*", help="reviewer folders or decisions.jsonl files")
    ap.add_argument("--out", default=str(RESULTS_DIR / "stage1_summary"))
    ap.add_argument("--verify-files", action="store_true", help="check every manifest image exists")
    ap.add_argument("--sha256", action="store_true", help="with --verify-files: also hash every image")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    md = []
    say = lambda s="": (print(s), md.append(s))  # noqa: E731

    items = load_manifest()
    by_id = {i["id"]: i for i in items}
    msha = manifest_sha256()
    split = json.loads((SPLIT_DIR / "split.json").read_text()) if (SPLIT_DIR / "split.json").exists() else {}
    say("# Stage 1 summary\n")
    say(f"- manifest: {len(items)} images, sha256 {msha[:12]}"
        + ("" if not split else (" — same manifest as the split" if split.get("manifest_sha256") == msha
                                 else " — **DIFFERENT from the manifest the split was made from**")))

    # ---- files on disk
    if a.verify_files:
        from core.datasets import load_dataset_config
        root = load_dataset_config().root
        missing, badsha = [], []
        for i in items:
            p = root / i["file"]
            if not p.exists():
                missing.append(i["id"])
            elif a.sha256 and i.get("sha256") and hashlib.sha256(p.read_bytes()).hexdigest() != i["sha256"]:
                badsha.append(i["id"])
        known = {(root / i["file"]).resolve() for i in items}
        extra = [p for p in root.glob("set*/30_dataset/images/*") if p.is_file() and p.resolve() not in known]
        say(f"- files: {len(missing)} manifest images missing on disk, {len(extra)} images on disk not in the "
            f"manifest" + (f", {len(badsha)} sha256 mismatches" if a.sha256 else ""))

    # ---- assignment
    owner, to_review = {}, set()
    for adir in sorted(SPLIT_DIR.glob("reviewer*")):
        f = adir / "assignment.csv"
        if not f.exists():
            continue
        for r in csv.DictReader(f.open(encoding="utf-8")):
            iid = f"{r['set']}/{Path(r['file']).name}"
            if iid in owner:
                say(f"- WARN {iid} assigned to {owner[iid]} and {adir.name}")
            owner[iid] = adir.name
            if r.get("to_review", "").lower() in ("yes", "true", "1"):
                to_review.add(iid)
    tampered = [i for i in items if i["label"] != "authentic"]
    unassigned = [i["id"] for i in tampered if i["id"] not in to_review]
    say(f"- tampered images to review: {len(tampered)}; assigned to a reviewer: {len(tampered) - len(unassigned)}"
        + (f"; **{len(unassigned)} never assigned**" if unassigned else " (all)"))

    # ---- decisions
    recs = []
    for name, f in source_files(a.sources):
        for k, r in enumerate(read_jsonl(f)):
            r["_src"], r["_order"] = name, (len(recs), k)
            recs.append(r)
    seen, uniq = set(), []
    for r in recs:                                 # the same record can sit in both files
        fp = (r.get("id"), r.get("reviewer"), r.get("timestamp"), r.get("decision"), r.get("reason"))
        if fp not in seen:
            seen.add(fp)
            uniq.append(r)
    recs = uniq
    latest, history = {}, defaultdict(list)
    for r in recs:
        history[r["id"]].append(r)
        cur = latest.get(r["id"])
        if cur is None or str(r.get("timestamp", "")) >= str(cur.get("timestamp", "")):
            latest[r["id"]] = r
    unknown = [i for i in latest if i not in by_id]
    outside = [r for r in latest.values() if r["id"] in owner and owner[r["id"]] != r["_src"]]
    flips = [i for i, h in history.items() if len({x.get("decision") for x in h}) > 1]
    conflicts = [i for i, h in history.items() if len({x["_src"] for x in h}) > 1]
    say(f"- decision records: {len(recs)} distinct from {len(source_files(a.sources))} files; distinct images: {len(latest)}; "
        f"ids not in manifest: {len(unknown)}; decided outside own assignment: {len(outside)}; "
        f"changed mind (keep<->reject on the same image): {len(flips)}; same image in 2 reviewers' files: {len(conflicts)}")

    # ---- resolve every image like the app does
    decisions = {k: v for k, v in latest.items() if k in by_id}
    doc_rej = document_rejects(by_id, decisions)
    status, how = {}, {}
    for i in items:
        s, rec = resolve_status(i, decisions, doc_rej, review_only_tampered=True)
        status[i["id"]] = s
        if rec is None:
            how[i["id"]] = "auto" if s == "keep" else "none"
        elif rec.get("id") in (i["id"], canonical_id(i)) and rec.get("scope") != "document":
            how[i["id"]] = "own"
        else:
            how[i["id"]] = "document"

    # ---- per set x label
    say("\n## 1. Per set (after resolving document rejects)\n")
    rows = []
    for st in sorted({i["set"] for i in items}):
        for lab in ("tampered", "authentic"):
            sel = [i for i in items if i["set"] == st and (i["label"] == "authentic") == (lab == "authentic")]
            c = Counter((status[i["id"]], how[i["id"]]) for i in sel)
            keep = sum(v for (s, _), v in c.items() if s == "keep")
            rej_own = c[("reject", "own")]
            rej_doc = c[("reject", "document")]
            pend = sum(v for (s, _), v in c.items() if s == "pending")
            rows.append({"set": st, "label": lab, "total": len(sel), "kept": keep, "rejected_image": rej_own,
                         "rejected_by_document": rej_doc, "pending": pend,
                         "reject_rate_of_decided": pct(rej_own + rej_doc, keep + rej_own + rej_doc) if lab == "tampered" else "-"})
    tot = {k: sum(r[k] for r in rows) for k in ("total", "kept", "rejected_image", "rejected_by_document", "pending")}
    rows.append({"set": "ALL", "label": "", **tot, "reject_rate_of_decided": ""})
    f = ["set", "label", "total", "kept", "rejected_image", "rejected_by_document", "pending", "reject_rate_of_decided"]
    say(md_table(rows, f))
    write_csv(out / "sets.csv", rows, f)
    say("\nSet2/Set4 authentic images are the same files as Set1/Set3 authentic (duplicate_of); count them once.")

    # ---- hierarchy: side -> family -> method / model (the order of the dataset paper)
    say("\n## 1b. Real / synthetic -> hand-crafted / deep learning -> method or model\n")
    hrows = []
    for side in ("real", "synthetic"):
        S_ = [i for i in items if i["side"] == side]
        for fam in ("handcrafted", "deep_learning"):
            F_ = [i for i in S_ if i["family"] == fam]
            groups = [("all", [i for i in F_ if i["label"] == "tampered"])] + sorted(
                ((g, [i for i in F_ if i["label"] == "tampered" and group_of(i) == g])
                 for g in {group_of(i) for i in F_ if i["label"] == "tampered"}), key=lambda x: x[0])
            for g, sel in groups:
                c = Counter((status[i["id"]], how[i["id"]]) for i in sel)
                keep = sum(v for (s, _), v in c.items() if s == "keep")
                ri, rd = c[("reject", "own")], c[("reject", "document")]
                pend = sum(v for (s, _), v in c.items() if s == "pending")
                hrows.append({"side": side, "family": fam, "method_or_model": g, "tampered": len(sel), "kept": keep,
                              "rejected_image": ri, "rejected_document": rd, "pending": pend,
                              "reject_rate": pct(ri + rd, keep + ri + rd),
                              "documents_kept": len({i["doc_id"] for i in sel if status[i["id"]] == "keep"})})
        au = [i for i in S_ if i["label"] == "authentic" and not i.get("duplicate_of")]
        hrows.append({"side": side, "family": "authentic (shared by both families)", "method_or_model": "-",
                      "tampered": f"({len(au)} authentic)", "kept": sum(status[i["id"]] == "keep" for i in au),
                      "rejected_image": 0, "rejected_document": sum(status[i["id"]] == "reject" for i in au),
                      "pending": 0, "reject_rate": "-",
                      "documents_kept": len({i["doc_id"] for i in au if status[i["id"]] == "keep"})})
    f = ["side", "family", "method_or_model", "tampered", "kept", "rejected_image", "rejected_document", "pending",
         "reject_rate", "documents_kept"]
    say(md_table(hrows, f))
    write_csv(out / "hierarchy.csv", hrows, f)

    # ---- per reviewer
    say("\n## 2. Per reviewer (assigned tampered images)\n")
    rrows, pend_rows = [], []
    gone = sorted(i for i in to_review if i not in by_id)
    if gone:
        say(f"- {len(gone)} assigned images are no longer in the dataset (dropped by the fixes, bs_syn_data "
            f"docs/11 §13): {', '.join(gone[:5])}{' …' if len(gone) > 5 else ''}\n")
    for rv in sorted(set(owner.values())):
        ids = [i for i in to_review if owner.get(i) == rv and i in by_id]
        own = [i for i in ids if i in decisions or canonical_id(by_id[i]) in decisions]
        by_doc_only = [i for i in ids if i not in own and status[i] == "reject"]
        pending = [i for i in ids if status[i] == "pending"]
        recs_rv = [r for r in recs if r["_src"] == rv]
        ts = sorted(str(r.get("timestamp", "")) for r in recs_rv)
        dec = Counter(decisions[i]["decision"] for i in own if i in decisions)
        rrows.append({"reviewer": rv, "assigned": len(ids), "decided": len(own), "covered_by_document_reject": len(by_doc_only),
                      "pending": len(pending), "done": pct(len(ids) - len(pending), len(ids)),
                      "keep": dec["keep"], "reject": dec["reject"], "reject_rate": pct(dec["reject"], dec["keep"] + dec["reject"]),
                      "accounts": " ".join(f"{k}:{v}" for k, v in Counter(r.get("reviewer") for r in recs_rv).most_common()),
                      "first": ts[0][:10] if ts else "", "last": ts[-1][:10] if ts else ""})
        for i in sorted(pending):
            it = by_id[i]
            pend_rows.append({"reviewer": rv, "id": i, "set": it["set"], "doc_id": it.get("doc_id"), "group": group_of(it)})
    new_pending = sorted(i["id"] for i in tampered if i["id"] not in owner and status[i["id"]] == "pending")
    for i in new_pending:
        it = by_id[i]
        pend_rows.append({"reviewer": "(new since the split)", "id": i, "set": it["set"], "doc_id": it.get("doc_id"),
                          "group": group_of(it)})
    f = ["reviewer", "assigned", "decided", "covered_by_document_reject", "pending", "done", "keep", "reject",
         "reject_rate", "accounts", "first", "last"]
    say(md_table(rrows, f))
    if unassigned:
        say(f"\n{len(unassigned)} tampered images are new since the split (made by the fixes); "
            f"{len(new_pending)} still need a decision (pending.csv), the rest are covered by a document reject.")
    write_csv(out / "reviewers.csv", rrows, f)
    write_csv(out / "pending.csv", pend_rows, ["reviewer", "id", "set", "doc_id", "group"])

    # ---- reviewer x set reject rate (is one reviewer stricter on the same kind of images?)
    say("\n## 3. Reject rate by reviewer x set (own decisions on tampered images)\n")
    cnt = defaultdict(Counter)
    for i, r in decisions.items():
        it = by_id[i]
        if it["label"] != "authentic" and i in owner:
            cnt[(owner[i], it["set"])][r["decision"]] += 1
    sets = sorted({i["set"] for i in items})
    xrows = []
    for rv in sorted(set(owner.values())):
        row = {"reviewer": rv}
        for st in sets:
            c = cnt[(rv, st)]
            row[st] = f"{pct(c['reject'], c['keep'] + c['reject'])} (n={c['keep'] + c['reject']})"
        xrows.append(row)
    say(md_table(xrows, ["reviewer"] + sets))
    write_csv(out / "reviewer_set.csv", xrows, ["reviewer"] + sets)

    # ---- reasons
    say("\n## 4. Reject reasons (images rejected, by reviewer)\n")
    reason = defaultdict(Counter)
    for i in items:
        if status[i["id"]] == "reject":
            s, rec = resolve_status(i, decisions, doc_rej, True)
            reason[rec.get("reason") or "-"][owner.get(rec["id"], "?")] += 1
    revs = sorted(set(owner.values()))
    rsrows = [{"reason": k, **{rv: v[rv] for rv in revs}, "total": sum(v.values())} for k, v in sorted(reason.items())]
    say(md_table(rsrows, ["reason"] + revs + ["total"]))
    say("\n(counts every rejected image, including authentic and paired-set images removed by a document reject)")
    write_csv(out / "reasons.csv", rsrows, ["reason"] + revs + ["total"])

    # ---- by method / model
    say("\n## 5. By set x method/model (tampered)\n")
    g = defaultdict(Counter)
    for i in tampered:
        g[(i["set"], group_of(i))][status[i["id"]]] += 1
    grows = [{"set": s, "group": m, "total": sum(c.values()), "kept": c["keep"], "rejected": c["reject"],
              "pending": c["pending"], "reject_rate_of_decided": pct(c["reject"], c["keep"] + c["reject"])}
             for (s, m), c in sorted(g.items())]
    f = ["set", "group", "total", "kept", "rejected", "pending", "reject_rate_of_decided"]
    say(md_table(grows, f))
    write_csv(out / "groups.csv", grows, f)

    rej_rows = []
    for i in items:
        if status[i["id"]] == "reject":
            _, rec = resolve_status(i, decisions, doc_rej, True)
            rej_rows.append({"id": i["id"], "set": i["set"], "label": i["label"], "group": group_of(i),
                             "doc_id": i.get("doc_id"), "reason": rec.get("reason"), "scope": rec.get("scope"),
                             "decided_on": rec.get("id"), "reviewer": owner.get(rec.get("id"), "?"),
                             "account": rec.get("reviewer"), "note": rec.get("note", "")})
    write_csv(out / "rejected.csv", rej_rows, list(rej_rows[0]) if rej_rows else ["id"])

    # ---- Stage 2 dry run
    config = json.loads(Path(STUDY_CONFIG_PATH).read_text())
    say("\n## 6. Stage 2 pool dry run (config/study.json, nothing written)\n")
    kept_now = [i for i in items if status[i["id"]] == "keep"]
    kept_max = [i for i in items if status[i["id"]] in ("keep", "pending")]
    srows = []
    res = {}
    for tag, kept in (("now", kept_now), ("if_all_pending_kept", kept_max)):
        picked, warns = draw_pool(kept, config)
        res[tag] = (Counter(n for n, _ in picked), warns)
    for s in config["strata"]:
        avail = len({(i["side"], i.get("doc_id")) for i in kept_now if i["set"] in s["sets"]
                     and i["label"] == s["label"] and not i.get("duplicate_of")})
        srows.append({"stratum": s["name"], "target": s["n"], "documents_with_a_kept_image": avail,
                      "drawn_now": res["now"][0][s["name"]],
                      "drawn_if_pending_kept": res["if_all_pending_kept"][0][s["name"]]})
    f = ["stratum", "target", "documents_with_a_kept_image", "drawn_now", "drawn_if_pending_kept"]
    say(md_table(srows, f))
    for tag, (_, warns) in res.items():
        for w in warns:
            say(f"- {tag}: {w}")
    write_csv(out / "stage2_draw.csv", srows, f)

    complete = not any(s == "pending" for i, s in status.items() if by_id[i]["label"] != "authentic")
    say(f"\nStage 1 complete: {'yes' if complete else 'NO'}")
    (out / "summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"\nwrote {out}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
