"""Unit tests for the pure logic of both stages (no browser, no real data)."""
from __future__ import annotations

import json
import random
from collections import Counter

from PIL import Image

from core.datasets import load_dataset_config, normalise_boxes, scan_datasets
from core.jsonstore import RecordStore
from review.selection import KEEP, REJECT, cohen_kappa, compute_selection, recheck_sample
from study.plan import Pool, build_plan
from study.ratings import validate
from study.sampling import assign_study_ids, draw_pool

# --------------------------------------------------------------------------- #
# datasets / manifest
# --------------------------------------------------------------------------- #


def _write_sets(root, *, identical_auth=True):
    for set_name in ("set1", "set2"):
        folder = root / set_name
        (folder / "masks").mkdir(parents=True)
        for doc in ("d1", "d2"):
            colour = 200 if identical_auth else (200 if set_name == "set1" else 201)
            Image.new("RGB", (40, 20), (colour,) * 3).save(folder / f"{doc}_auth.png")
            Image.new("RGB", (40, 20), (10, 20, 30 if set_name == "set1" else 31)).save(
                folder / f"{doc}_tamp1_m.png")
            Image.new("L", (40, 20)).save(folder / "masks" / f"{doc}_tamp1_m.png")
        (folder / "labels.jsonl").write_text(
            json.dumps({"file": "d1_tamp1_m.png", "layout": "L1", "bbox": [1, 2, 3, 4]}) + "\n")
    config = {
        "root": str(root),
        "exclude_patterns": ["*_mask.*"],
        "label_values": {"authentic": ["auth"], "tampered": ["tamp"]},
        "sets": {"set1": {"side": "real"}, "set2": {"side": "real"}},
        "filename_pattern": r"^(?P<doc_id>d\d)_(?P<label>auth|tamp)\d?(?:_(?P<method>\w+))?$",
        "metadata": {"file": "labels.jsonl", "key": "file", "fields": {"boxes": "bbox"}},
        "boxes_format": "xywh",
        "view_templates": {"mask": "masks/{name}", "authentic": "{doc_id}_auth.png"},
    }
    path = root / "datasets.json"
    path.write_text(json.dumps(config))
    return load_dataset_config(path)


def test_scan_reads_pattern_metadata_views_and_duplicates(tmp_path):
    items, warnings = scan_datasets(_write_sets(tmp_path))
    assert not warnings
    by_id = {i["id"]: i for i in items}
    assert len(items) == 8                                     # masks/ not scanned (no recurse)
    t = by_id["set1/d1_tamp1_m.png"]
    assert (t["doc_id"], t["label"], t["method"], t["layout"]) == ("d1", "tampered", "m", "L1")
    assert t["boxes"] == [[1.0, 2.0, 4.0, 6.0]]                # xywh -> xyxy
    assert t["views"] == {"mask": "set1/masks/d1_tamp1_m.png", "authentic": "set1/d1_auth.png"}
    assert by_id["set1/d1_auth.png"]["views"] == {}           # not its own authentic view
    assert by_id["set2/d1_auth.png"]["duplicate_of"] == "set1/d1_auth.png"
    assert by_id["set2/d1_tamp1_m.png"]["duplicate_of"] is None


def test_normalise_boxes_accepts_common_shapes():
    assert normalise_boxes("[[0, 0, 2, 2]]") == [[0, 0, 2, 2]]
    assert normalise_boxes([0, 0, 2, 2]) == [[0, 0, 2, 2]]
    assert normalise_boxes([{"bbox": [1, 1, 2, 2]}], "xywh") == [[1, 1, 3, 3]]
    assert normalise_boxes(None) == []


# --------------------------------------------------------------------------- #
# stage 1 selection
# --------------------------------------------------------------------------- #


def _decision(item_id, decision, reason="", scope="image"):
    return {"id": item_id, "decision": decision, "reason": reason, "scope": scope}


def test_selection_document_reject_and_duplicates(tmp_path):
    items, _ = scan_datasets(_write_sets(tmp_path))
    decisions = {
        "set1/d1_auth.png": _decision("set1/d1_auth.png", KEEP),
        "set1/d1_tamp1_m.png": _decision("set1/d1_tamp1_m.png", REJECT, "damaged_page"),
        "set1/d2_tamp1_m.png": _decision("set1/d2_tamp1_m.png", REJECT, "broken_base",
                                         "document"),
    }
    sel = compute_selection(items, decisions)
    assert sel["sets"]["set1"]["selected"] == ["d1_auth.png"]
    assert sel["sets"]["set2"]["selected"] == ["d1_auth.png"]     # duplicate inherits keep
    assert set(sel["sets"]["set2"]["rejected"]) == {"d2_auth.png", "d2_tamp1_m.png"}
    assert sel["sets"]["set2"]["pending"] == ["d1_tamp1_m.png"]
    assert not sel["complete"]


def test_recheck_sample_is_fixed_and_kappa():
    items = [{"id": f"x{i}"} for i in range(100)]
    decisions = {f"x{i}": {} for i in range(100)}
    assert recheck_sample(items, decisions, 0.05, 7) == recheck_sample(items, decisions, 0.05, 7)
    assert len(recheck_sample(items, decisions, 0.05, 7)) == 5
    assert cohen_kappa(["keep", "reject"] * 5, ["keep", "reject"] * 5) == 1.0
    assert cohen_kappa(["keep"] * 4, ["keep"] * 4) == 1.0


# --------------------------------------------------------------------------- #
# stage 2 sampling and plans
# --------------------------------------------------------------------------- #


def _fake_side(side, prefix, n_docs, sets, methods, rng):
    items = []
    for d in range(n_docs):
        doc = f"{prefix}{d}"
        layout = f"L{d % 15}"
        items.append({"id": f"{sets[0]}/{doc}_a", "set": sets[0], "side": side, "doc_id": doc,
                      "label": "authentic", "layout": layout})
        for s, ms in zip(sets, methods):
            for v in (1, 2):
                items.append({"id": f"{s}/{doc}_t{v}", "set": s, "side": side, "doc_id": doc,
                              "label": "tampered", "method": rng.choice(ms), "layout": layout})
    return items


STRATA = [
    {"name": "s1", "side": "real", "sets": ["set1"], "label": "tampered", "n": 30,
     "balance_by": "method"},
    {"name": "s2", "side": "real", "sets": ["set2"], "label": "tampered", "n": 30,
     "balance_by": "method"},
    {"name": "ra", "side": "real", "sets": ["set1", "set2"], "label": "authentic", "n": 30},
]


def test_draw_pool_one_version_per_document_balance_and_layout_cap():
    rng = random.Random(0)
    items = _fake_side("real", "r", 150, ["set1", "set2"], [["a", "b", "c"], ["x", "y", "z"]],
                       rng)
    picked, warnings = draw_pool(items, {"seed": 1, "strata": STRATA, "max_layout_share": 0.1})
    assert not warnings
    assert len(picked) == 90
    docs = Counter(item["doc_id"] for _, item in picked)
    assert max(docs.values()) == 1
    for name in ("s1", "s2"):
        methods = Counter(i["method"] for s, i in picked if s == name)
        assert set(methods.values()) == {10}
    for name in ("s1", "s2", "ra"):
        assert max(Counter(i["layout"] for s, i in picked if s == name).values()) <= 3
    again, _ = draw_pool(items, {"seed": 1, "strata": STRATA, "max_layout_share": 0.1})
    assert [i["id"] for _, i in again] == [i["id"] for _, i in picked]


def test_draw_pool_falls_back_when_labels_unknown():
    items = [{"id": f"set1/{n}", "set": "set1", "side": "real", "label": "unknown"}
             for n in range(50)]
    picked, warnings = draw_pool(items, {"seed": 1, "strata": STRATA, "fallback_per_side": 20})
    assert len(picked) == 20 and "no authentic/tampered labels" in warnings[0]


def _pool(n_main=300, n_repeats=10, n_controls=4):
    picked = [("s", {"id": f"i{n}"}) for n in range(n_main)]
    controls = [{"stratum": "control_crude"} for _ in range(n_controls)]
    entries = assign_study_ids(picked, controls, n_repeats, seed=3)
    items = {e["study_id"]: {"kind": e["kind"], "repeat_of": e.get("repeat_of")}
             for e in entries}
    return Pool("v1", 3, 10, 3, [100, 200], items)


def test_plan_is_one_list_with_repeats_well_after_originals_and_stable():
    from study.plan import REPEAT_GAP

    pool = _pool()
    plan = build_plan(pool, "R1", "rater")
    assert len(plan["sessions"]) == 1                          # no sessions any more
    order = plan["sessions"][0]
    assert sorted(order) == sorted(pool.items)                 # every entry exactly once
    for sid, item in pool.items.items():
        if item["kind"] == "repeat":
            assert order.index(sid) - order.index(item["repeat_of"]) > REPEAT_GAP
    assert build_plan(pool, "R1", "rater") == plan
    assert build_plan(pool, "R2", "rater")["sessions"] != plan["sessions"]
    pilot = build_plan(pool, "P1", "pilot")
    assert len(pilot["sessions"]) == 1 and len(pilot["sessions"][0]) == 10
    assert all(pool.items[s]["kind"] == "main" for s in pilot["sessions"][0])


# --------------------------------------------------------------------------- #
# ratings + storage
# --------------------------------------------------------------------------- #


def test_validate_clicks_and_free_1_to_5_score():
    click = {"x": 1, "y": 1}
    assert validate([], 5, 3, no_traces=True) is None          # "No edit on this page"
    assert validate([click], 5, 3, no_traces=True) is not None
    assert validate([], 3, 3) is not None                      # a score needs a click
    for score in (1, 2, 3, 4, 5):                              # any score with clicks
        assert validate([click], score, 3) is None
    assert validate([click], None, 3) is not None
    assert validate([click] * 4, 2, 3) is not None             # at most max_clicks


def test_record_store_latest_and_repair(tmp_path):
    store = RecordStore(tmp_path, "ratings", key=lambda r: r["study_id"])
    store.append({"study_id": "S1", "score": 2})
    store.append({"study_id": "S1", "score": 3})
    store.append({"study_id": "S2", "score": 5})
    assert {k: v["score"] for k, v in store.latest().items()} == {"S1": 3, "S2": 5}
    (tmp_path / "ratings_latest.json").write_text("{}")          # a lost publish
    fresh = RecordStore(tmp_path, "ratings", key=lambda r: r["study_id"])
    assert set(fresh.latest()) == {"S1", "S2"}
    assert len(fresh.history()) == 3


# --------------------------------------------------------------------------- #
# the real 30_dataset layout: images/ + masks/ + labels.jsonl, Stage 1 tampered only
# --------------------------------------------------------------------------- #


def _write_30_dataset(root):
    rows = {"set1": [], "set3": []}
    for set_name, stems in (("set1", ["L1__abc_c0", "L1__abc_t0_c0", "L1__abc_t1_c0"]),
                            ("set3", ["aa11", "bb22"])):
        base = root / set_name / "30_dataset"
        (base / "images").mkdir(parents=True)
        (base / "masks").mkdir()
        for n, stem in enumerate(stems):
            tampered = n > 0
            green = 0 if set_name == "set1" else 90      # no accidental identical files
            Image.new("RGB", (40, 20), (n * 40, green, 0)).save(base / "images" / f"{stem}.jpg")
            Image.new("L", (40, 20), 255 if tampered else 0).save(base / "masks" / f"{stem}.png")
            row = {"image": f"images/{stem}.jpg", "mask": f"masks/{stem}.png",
                   "label": "tampered" if tampered else "authentic",
                   "boxes": [[1, 2, 3, 4]] if tampered else [], "layout": "L1",
                   "capture": {"condition": "jpeg", "quality": [80]},
                   "category": "date" if tampered else None,
                   "method": "deep_learning" if tampered else None,
                   "model": "UDiffText (ECCV 2024)" if tampered else None}
            row.update({"internal_id": stem} if set_name == "set1" else {"group": "g1"})
            rows[set_name].append(row)
        (base / "labels.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows[set_name]))
    real = json.loads((__import__("pathlib").Path(__file__).parents[1] / "config" /
                       "datasets.json").read_text())
    real["root"] = str(root)
    real["sets"] = {k: v for k, v in real["sets"].items() if k in ("set1", "set3")}
    for k, v in real["sets"].items():      # the real config may point at 31_filtered: same layout
        v["folder"] = f"{k}/30_dataset/images"
    path = root / "datasets.json"
    path.write_text(json.dumps(real))
    return load_dataset_config(path)


def test_real_layout_config_reads_labels_masks_and_doc_ids(tmp_path):
    items, warnings = scan_datasets(_write_30_dataset(tmp_path))
    assert not warnings
    by_id = {i["id"]: i for i in items}
    t = by_id["set1/L1__abc_t1_c0.jpg"]
    assert (t["doc_id"], t["label"], t["model"], t["edit_type"], t["capture"], t["image_id"]) \
        == ("L1__abc", "tampered", "UDiffText (ECCV 2024)", "date", "jpeg", "L1__abc_t1_c0")
    assert t["views"] == {"authentic": "set1/30_dataset/images/L1__abc_c0.jpg",
                          "mask": "set1/30_dataset/masks/L1__abc_t1_c0.png"}
    assert by_id["set1/L1__abc_c0.jpg"]["doc_id"] == "L1__abc"
    assert by_id["set3/bb22.jpg"]["doc_id"] == "g1"


def test_stage1_reviews_tampered_only_and_keeps_authentic(tmp_path):
    items, _ = scan_datasets(_write_30_dataset(tmp_path))
    decisions = {"set1/L1__abc_t0_c0.jpg": _decision("set1/L1__abc_t0_c0.jpg", KEEP),
                 "set1/L1__abc_t1_c0.jpg": _decision("set1/L1__abc_t1_c0.jpg", KEEP)}
    sel = compute_selection(items, decisions, review_only_tampered=True)
    assert sel["sets"]["set1"]["pending"] == []
    assert sel["counts"]["set1"]["authentic_auto_kept"] == 1
    assert sel["sets"]["set3"]["pending"] == ["bb22.jpg"]      # tampered, not decided yet
    decisions["set1/L1__abc_t0_c0.jpg"] = _decision("set1/L1__abc_t0_c0.jpg", REJECT,
                                                    "broken_base", "document")
    sel = compute_selection(items, decisions, review_only_tampered=True)
    assert sorted(sel["sets"]["set1"]["rejected"]) == [
        "L1__abc_c0.jpg", "L1__abc_t0_c0.jpg", "L1__abc_t1_c0.jpg"]


def test_split_by_document_is_reproducible_balanced_and_keeps_documents_together():
    from review.assign import split_by_document

    rng = random.Random(3)
    items = []
    for d in range(400):
        side = "real" if d < 100 else "synthetic"
        sets = ("set1", "set2") if side == "real" else ("set3", "set4")
        for s in sets:
            for v in range(rng.randint(0, 2)):
                items.append({"id": f"{s}/{d}_t{v}", "set": s, "side": side, "doc_id": str(d)})
    reviewers = ["A", "B", "C", "D"]
    split = split_by_document(items, reviewers, seed=11)
    assert split == split_by_document(list(reversed(items)), reviewers, seed=11)
    assert split != split_by_document(items, reviewers, seed=12)
    assigned = [i for ids in split.values() for i in ids]
    assert sorted(assigned) == sorted(i["id"] for i in items)           # each exactly once
    sizes = [len(ids) for ids in split.values()]
    assert max(sizes) - min(sizes) <= 4                                  # ≤ one document apart
    owner = {i: r for r, ids in split.items() for i in ids}
    by_doc = {}
    for item in items:
        by_doc.setdefault((item["side"], item["doc_id"]), set()).add(owner[item["id"]])
    assert all(len(owners) == 1 for owners in by_doc.values())          # documents not split


def test_load_split_reads_reviewer_folders(tmp_path):
    from review.assign import load_split

    items = [{"id": "set1/a.jpg", "file": "set1/30_dataset/images/a.jpg",
              "views": {"mask": "set1/30_dataset/masks/a.png"}},
             {"id": "set1/b.jpg", "file": "set1/30_dataset/images/b.jpg", "views": {}}]
    for reviewer, rel in (("reviewer1", "set1/30_dataset/images/a.jpg"),
                          ("reviewer1", "set1/30_dataset/masks/a.png"),
                          ("reviewer1", "set1/30_dataset/labels.jsonl"),
                          ("reviewer2", "set1/30_dataset/images/b.jpg"),
                          ("reviewer2", "set1/30_dataset/labels.jsonl")):
        path = tmp_path / reviewer / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
    split = load_split(tmp_path, items)
    assert split.assignments == {"reviewer1": {"set1/a.jpg"}, "reviewer2": {"set1/b.jpg"}}
    assert split.path_of("set1/30_dataset/masks/a.png") == \
        tmp_path / "reviewer1" / "set1/30_dataset/masks/a.png"
    assert not split.warnings
    assert load_split(tmp_path / "missing", items).active is False


def test_record_store_latest_follows_timestamps_not_merge_order(tmp_path):
    store = RecordStore(tmp_path, "decisions", key=lambda r: r["id"])
    store.append({"id": "a", "decision": "reject", "timestamp": "2026-10-08T10:00:00Z"})
    store.append({"id": "a", "decision": "keep", "timestamp": "2026-10-07T10:00:00Z"})  # merged
    assert store.latest()["a"]["decision"] == "reject"
    fresh = RecordStore(tmp_path, "decisions", key=lambda r: r["id"])
    (tmp_path / "decisions_latest.json").unlink()
    assert fresh.latest()["a"]["decision"] == "reject"
    store.append({"id": "a", "decision": "keep", "timestamp": "2026-10-08T10:00:00Z"})  # tie
    assert store.latest()["a"]["decision"] == "keep"


def test_reviewer_folder_manifests_round_trip(tmp_path):
    from review.assign import load_split_manifests, write_reviewer_manifest

    write_reviewer_manifest(tmp_path / "reviewer1", [{"id": "set1/a", "set": "set1", "name": "a"}])
    write_reviewer_manifest(tmp_path / "reviewer2", [{"id": "set3/b", "set": "set3", "name": "b"}])
    assert [i["id"] for i in load_split_manifests(tmp_path)] == ["set1/a", "set3/b"]
    assert load_split_manifests(tmp_path / "missing") == []


def test_keep_after_whole_document_reject_sticks(tmp_path):
    items, _ = scan_datasets(_write_30_dataset(tmp_path))
    t0, t1, auth = "set1/L1__abc_t0_c0.jpg", "set1/L1__abc_t1_c0.jpg", "set1/L1__abc_c0.jpg"
    decisions = {t0: {**_decision(t0, REJECT, "broken_base", "document"),
                      "timestamp": "2026-10-10T07:00:00Z"}}
    sel = compute_selection(items, decisions, review_only_tampered=True)
    assert sorted(sel["sets"]["set1"]["rejected"]) == [
        "L1__abc_c0.jpg", "L1__abc_t0_c0.jpg", "L1__abc_t1_c0.jpg"]
    # the user changes their mind about one version: it must stay kept
    decisions[t1] = {**_decision(t1, KEEP), "timestamp": "2026-10-10T07:05:00Z"}
    sel = compute_selection(items, decisions, review_only_tampered=True)
    assert sel["sets"]["set1"]["selected"] == ["L1__abc_t1_c0.jpg"]
    assert sorted(sel["sets"]["set1"]["rejected"]) == ["L1__abc_c0.jpg", "L1__abc_t0_c0.jpg"]
    # an older own decision does not beat a newer document reject
    decisions[t1]["timestamp"] = "2026-10-10T06:00:00Z"
    sel = compute_selection(items, decisions, review_only_tampered=True)
    assert "L1__abc_t1_c0.jpg" in sel["sets"]["set1"]["rejected"]
    # keeping the image that carried the document reject lifts it for the whole document
    decisions[t0] = {**_decision(t0, KEEP), "timestamp": "2026-10-10T07:10:00Z"}
    sel = compute_selection(items, decisions, review_only_tampered=True)
    assert sel["sets"]["set1"]["rejected"] == []
    assert auth.split("/")[1] in sel["sets"]["set1"]["selected"]


# --------------------------------------------------------------------------- #
# stage 2: clicks scored against the mask, rater folders
# --------------------------------------------------------------------------- #


def test_localize_against_mask_and_boxes(tmp_path):
    from study.localize import localize

    mask = tmp_path / "m.png"
    img = Image.new("L", (200, 100), 0)
    img.paste(255, (50, 40, 90, 60))                 # the edited word
    img.save(mask)
    clicks = [{"x": 70, "y": 50},                    # on the edit
              {"x": 95, "y": 50},                    # 5 px beside it: within the margin
              {"x": 180, "y": 10}]                   # far away
    out = localize(clicks, mask_path=mask, boxes=[[50, 40, 90, 60]])
    assert [c["hit"] for c in out["clicks"]] == [True, True, False]
    assert out["localization"] == "mask" and out["margin_px"] == 10
    assert out["edit_found"] and out["false_alarm"]
    assert (out["regions_total"], out["regions_found"], out["localization_score"]) == (1, 1, 1)
    black = tmp_path / "black.png"
    Image.new("L", (200, 100), 0).save(black)        # an authentic page
    assert localize(clicks[:1], mask_path=black)["false_alarm"]
    by_box = localize(clicks, mask_path=None, boxes=[[50, 40, 90, 60]])
    assert by_box["localization"] == "boxes" and by_box["edit_found"]
    assert localize(clicks, mask_path=None) == {"localization": "none"}
    authentic = localize(clicks[:1], mask_path=black)
    assert authentic["regions_total"] == 0 and authentic["localization_score"] is None


def test_two_edited_places_both_clicked_get_full_localization(tmp_path):
    from study.localize import localize

    mask = tmp_path / "m.png"
    img = Image.new("L", (300, 100), 0)
    img.paste(255, (20, 20, 60, 40))
    img.paste(255, (200, 60, 260, 80))
    img.save(mask)
    boxes = [[20, 20, 60, 40], [200, 60, 260, 80]]
    one = localize([{"x": 40, "y": 30}], mask_path=mask, boxes=boxes)
    both = localize([{"x": 40, "y": 30}, {"x": 230, "y": 70}], mask_path=mask, boxes=boxes)
    assert (one["regions_found"], one["localization_score"]) == (1, 0.5)
    assert (both["regions_found"], both["localization_score"]) == (2, 1)
    twice = localize([{"x": 30, "y": 30}, {"x": 50, "y": 30}], mask_path=mask, boxes=boxes)
    assert twice["regions_found"] == 1                          # same place clicked twice


def test_folder_pools_one_per_rater_blinded_ids(tmp_path):
    import json as _json

    from study.folders import load_folder_pools

    for rater in ("R1", "R2"):
        base = tmp_path / "split" / rater / "set3/30_dataset"
        (base / "images").mkdir(parents=True)
        (base / "masks").mkdir()
        rows = []
        for n in range(6):
            Image.new("RGB", (40, 20), (n, 0, 0)).save(base / "images" / f"h{n}.png")
            Image.new("L", (40, 20), 255 if n % 2 else 0).save(base / "masks" / f"h{n}.png")
            rows.append({"id": f"set3/h{n}.png", "set": "set3", "name": f"h{n}.png",
                         "file": f"set3/30_dataset/images/h{n}.png", "side": "synthetic",
                         "doc_id": f"d{n}", "label": "tampered" if n % 2 else "authentic",
                         "views": {"mask": f"set3/30_dataset/masks/h{n}.png"},
                         "boxes": [], "width": 40, "height": 20})
        (tmp_path / "split" / rater / "manifest.jsonl").write_text(
            "".join(_json.dumps(r) + "\n" for r in rows))
    config = tmp_path / "study.json"
    config.write_text(_json.dumps({"seed": 1, "repeats": 2,
                                   "controls_dir": str(tmp_path / "none")}))
    pools, warnings = load_folder_pools(tmp_path / "split", config)
    assert set(pools) == {"R1", "R2"} and not warnings
    r1 = pools["R1"]
    kinds = Counter(i["kind"] for i in r1.items.values())
    assert kinds == {"main": 6, "repeat": 2}
    assert all(sid.startswith("S") and "h" not in sid[1:] for sid in r1.items)
    assert set(r1.items).isdisjoint(pools["R2"].items)       # ids differ per rater
    sid = next(s for s, m in r1.meta.items() if m["label"] == "tampered")
    scored = r1.localize(sid, [{"x": 20, "y": 10}])
    assert scored["item_id"] == r1.meta[sid]["id"] and scored["edit_found"]
    plan = build_plan(r1, "R1", "rater")
    assert sorted(s for ss in plan["sessions"] for s in ss) == sorted(r1.items)
