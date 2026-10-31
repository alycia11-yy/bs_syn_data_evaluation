"""The worked examples of bs_syn_data doc 12 §2 and §6 (study/outcome.py)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from study.outcome import page_measures, summarize  # noqa: E402

H, M = {"hit": True}, {"hit": False}
CASES = {  # case: (label, score, clicks, regions_found, regions_total, outcome, edit_visibility)
    "T1": ("tampered", 5, [], 0, 1, "not_noticed", 5),
    "T2": ("tampered", 1, [H], 1, 1, "detected", 1),
    "T3": ("tampered", 4, [H], 1, 1, "located_unsure", 4),
    "T4": ("tampered", 2, [M], 0, 1, "wrong_place", 5),
    "T5": ("tampered", 1, [M, M, M], 0, 1, "wrong_place", 5),
    "T6": ("tampered", 2, [H, M, M], 1, 1, "detected", 2),
    "T7": ("tampered", 3, [M, M], 0, 1, "doubt_elsewhere", 5),
    "T8": ("tampered", 2, [H], 1, 2, "detected", 2),
    "A1": ("authentic", 5, [], 0, 0, "accepted", None),
    "A2": ("authentic", 4, [M], 0, 0, "doubt", None),
    "A3": ("authentic", 1, [M, M], 0, 0, "false_alarm", None),
    "A4": ("authentic", 5, [], 0, 0, "accepted", None),
}


def _pages():
    out = {}
    for case, (label, score, clicks, found, total, _, _) in CASES.items():
        out[case] = {**page_measures(label, score, clicks, found, total), "label": label}
    return out


def test_page_cases():
    pages = _pages()
    for case, (*_, outcome, visibility) in CASES.items():
        assert pages[case]["outcome"] == outcome, case
        assert pages[case]["edit_visibility"] == visibility, case
    assert pages["T5"]["score"] == 1 and pages["T5"]["false_alarm_clicks"] == 3   # wrong clicks keep the score
    assert pages["T6"]["edit_found"] and pages["T6"]["false_alarm_clicks"] == 2


def test_summary_worked_example():
    s = summarize(_pages().values())
    assert (s["mos_tampered"], s["mos_authentic"], s["realism_gap"]) == (2.5, 3.75, 1.25)
    assert round(s["auc"], 2) == 0.70 and s["auc_localized"] == 0.5
    assert s["edit_found_rate"] == 0.5 and s["detection_rate"] == 0.375
    assert round(s["edits_found_rate"], 2) == 0.44 and round(s["miss_click_share"], 2) == 0.67
    assert s["false_alarm_rate"] == 0.25 and s["mean_edit_visibility"] == 3.625


def test_first_click_rule():
    first_wrong = page_measures("tampered", 2, [M, H], 1, 1)       # most-sure place wrong, second right
    assert first_wrong["edit_found"] and first_wrong["edit_visibility"] == 2
    assert not first_wrong["first_click_hit"] and first_wrong["edit_visibility_strict"] == 5
    s = summarize(_pages().values())                                # T6 has its hit first
    assert s["first_click_hit_rate"] == 0.5 and s["auc_localized_strict"] == s["auc_localized"]
