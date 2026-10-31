"""What one answer measures, and the study-level metrics built from many answers (doc 12 §6).

One answer on one page = ONE realism score for the whole page (1–5; "No edit on this page" = 5; with
several places marked, raters judge by the strongest sign of editing) + up to 3 clicks, the place the
rater is most sure about first, each checked against the edit mask (hit = on an edit, miss = elsewhere;
study/localize.py). The score and the clicks are separate answers; nothing here changes the score.
This is the ROC/LROC design of observer studies (one image-level rating + location), not FROC (one
rating per mark); the first click plays the LROC "most suspicious location" (bs_syn_data doc 12 §2).

Per page (`page_measures`):
    score              the realism score as given (how genuine the whole page looks)
    clicks, hits, misses
    edits_found / edits_total     edited places with at least one click on them (tampered pages)
    edit_found         at least one click on an edit (tampered pages)
    false_alarm_clicks clicks that are not on an edit (every click on a genuine page)
    edit_visibility    tampered pages: the score if a click is on the edit, else 5 — the score
                       credited to the edit itself (a low score given for another reason does not
                       count against the edit). None on genuine pages.
    first_click_hit    tampered pages: the first click (the place the rater was most sure about) is
                       on an edit
    edit_visibility_strict   the score if the FIRST click is on the edit, else 5 (LROC rule; a
                       sensitivity check for pages with a hit among several clicks)
    outcome            the case, combining score and clicks (OUTCOMES)

Study level (`summarize`, per group of pages): MOS, realism gap, share scored 5, AUC, edit-found rate,
edits-found rate, detection rate, false-alarm rates, mean edit visibility, localization-aware AUC.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

# outcome code -> (short label, meaning)
OUTCOMES: Dict[str, tuple] = {
    "not_noticed": ("Not noticed", "edited page, no place marked (score 5): the edit passed"),
    "detected": ("Edit detected", "a click on the edit and score 1–2: the edit was seen and judged an edit"),
    "located_unsure": ("Edit located, unsure", "a click on the edit but score 3–5: the edit was sensed, not "
                       "judged a clear edit"),
    "wrong_place": ("Judged edited, wrong place", "score 1–2 but every click misses: the page looked edited "
                    "because of something else; the edit itself passed"),
    "doubt_elsewhere": ("Doubt elsewhere", "score 3–5 and every click misses: a doubt not caused by the edit; "
                        "the edit passed"),
    "accepted": ("Accepted as genuine", "genuine page, score 5"),
    "doubt": ("Doubt on a genuine page", "genuine page, score 3–4"),
    "false_alarm": ("False alarm", "genuine page judged edited (score 1–2)"),
}
EDIT_PASSED = {"not_noticed", "wrong_place", "doubt_elsewhere"}


def page_measures(label: str, score: int, clicks: List[Dict[str, Any]],
                  regions_found: Optional[int] = None, regions_total: Optional[int] = None) -> Dict[str, Any]:
    """`clicks` carry `hit` (study/localize.py). label: "tampered" or "authentic"."""
    hits = sum(1 for c in clicks if c.get("hit"))
    misses = len(clicks) - hits
    tampered = label == "tampered"
    if tampered:
        if hits:
            outcome = "detected" if score <= 2 else "located_unsure"
        elif clicks:
            outcome = "wrong_place" if score <= 2 else "doubt_elsewhere"
        else:
            outcome = "not_noticed"
    else:
        outcome = "accepted" if score >= 5 else "doubt" if score >= 3 else "false_alarm"
    total = regions_total if tampered else 0
    return {
        "score": score, "clicks": len(clicks), "hits": hits, "misses": misses,
        "edits_total": total, "edits_found": (regions_found if regions_found is not None
                                              else (1 if hits else 0)) if tampered else 0,
        "edit_found": bool(hits) if tampered else None,
        "false_alarm_clicks": misses,
        "edit_visibility": (score if hits else 5) if tampered else None,
        "first_click_hit": bool(clicks and clicks[0].get("hit")) if tampered else None,
        "edit_visibility_strict": (score if clicks and clicks[0].get("hit") else 5) if tampered else None,
        "outcome": outcome,
    }


def outcome_label(code: str) -> str:
    return OUTCOMES.get(code, (code, ""))[0]


def _auc(low: List[float], high: List[float]) -> Optional[float]:
    """P(a tampered value < a genuine value) + ½ P(equal): 0.5 = cannot be told apart, 1 = always lower."""
    if not low or not high:
        return None
    wins = sum((t < a) + 0.5 * (t == a) for t in low for a in high)
    return wins / (len(low) * len(high))


def summarize(pages: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """pages: page_measures(...) dicts with "label" added. Rates are shares of pages (0–1)."""
    pages = list(pages)
    t = [p for p in pages if p["label"] == "tampered"]
    a = [p for p in pages if p["label"] != "tampered"]

    def mean(xs):
        xs = list(xs)
        return sum(xs) / len(xs) if xs else None

    mos_t, mos_a = mean(p["score"] for p in t), mean(p["score"] for p in a)
    clicks_t = sum(p["clicks"] for p in t)
    return {
        "n_tampered": len(t), "n_authentic": len(a),
        "mos_tampered": mos_t, "mos_authentic": mos_a,
        "realism_gap": None if mos_t is None or mos_a is None else mos_a - mos_t,
        "share5_tampered": mean(p["score"] == 5 for p in t),
        "share5_authentic": mean(p["score"] == 5 for p in a),
        "auc": _auc([p["score"] for p in t], [p["score"] for p in a]),
        "edit_found_rate": mean(p["edit_found"] for p in t),
        "edits_found_rate": (sum(p["edits_found"] for p in t) / max(1, sum(p["edits_total"] for p in t))
                             if t else None),
        "detection_rate": mean(p["outcome"] == "detected" for p in t),
        "edit_passed_rate": mean(p["outcome"] in EDIT_PASSED for p in t),
        "wrong_place_rate": mean(p["outcome"] == "wrong_place" for p in t),
        "miss_click_share": (sum(p["misses"] for p in t) / clicks_t) if clicks_t else None,
        "false_alarm_rate": mean(p["outcome"] == "false_alarm" for p in a),
        "authentic_clicked_rate": mean(p["clicks"] > 0 for p in a),
        "mean_edit_visibility": mean(p["edit_visibility"] for p in t),
        "auc_localized": _auc([p["edit_visibility"] for p in t], [p["score"] for p in a]),
        "first_click_hit_rate": mean(p["first_click_hit"] for p in t),
        "auc_localized_strict": _auc([p["edit_visibility_strict"] for p in t], [p["score"] for p in a]),
        "outcomes": {k: sum(p["outcome"] == k for p in pages) for k in OUTCOMES},
    }


def measures_of(label: str, answer: Dict[str, Any]) -> Dict[str, Any]:
    """The measures of a saved answer (stored ones, or computed from its clicks)."""
    return answer.get("measures") or page_measures(label, answer.get("score", 5), answer.get("clicks") or [],
                                                   answer.get("regions_found"), answer.get("regions_total"))


def describe(label: str, m: Dict[str, Any]) -> List[str]:
    """Plain-language lines for one answer's measures (practice feedback)."""
    tampered = label == "tampered"
    lines = [f"Realism score {m['score']} for the whole page" + (" (No edit on this page)"
                                                                if not m["clicks"] and m["score"] == 5 else "")]
    if m["clicks"]:
        lines.append(f"{m['clicks']} click{'s' if m['clicks'] != 1 else ''}: "
                     + (f"{m['hits']} on the edit, {m['misses']} elsewhere · edited places found "
                        f"{m['edits_found']} of {m['edits_total']}"
                        + (f" · first click {'on' if m.get('first_click_hit') else 'not on'} the edit"
                           if m["clicks"] > 1 else "") if tampered
                        else f"all on a genuine page ({m['misses']} false-alarm click"
                             f"{'s' if m['misses'] != 1 else ''})"))
    label_, meaning = OUTCOMES[m["outcome"]]
    lines.append(f"Outcome: {label_} — {meaning}")
    if m.get("edit_visibility") is not None:
        lines.append(f"Edit visibility {m['edit_visibility']} (score credited to the edit: the score if a click "
                     "is on it, else 5)")
    return lines
