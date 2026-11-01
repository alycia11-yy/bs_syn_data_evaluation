"""Stage 2 pages: the rating flow (one continuous list, autosaved), practice and admin."""
from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from nicegui import ui

from auth import current_role, current_username, logout_session
from auth.middleware import require_active_session, same_account
from core.jsonstore import read_json
from core.settings import PLANS_DIR, RATINGS_DIR
from core.ui import box_overlay, click_markers, zoomable_image
from review.selection import now_iso
from study.localize import localize
from study.outcome import OUTCOMES, describe, measures_of, page_measures
from study.plan import ADMIN, PILOT, RATER, Pool, flat, get_plan
from study.practice import PracticeItem
from study.ratings import SCALE, store_for
from study.widget import rating_widget

HIT, MISS, REF = "#16a34a", "#dc2626", "#2563eb"


def _local(ts: Any) -> str:
    """An ISO timestamp (stored in UTC) as local 'YYYY-MM-DD HH:MM'; '—' if absent."""
    if not ts or ts == "—":
        return "—"
    try:
        return _dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return str(ts)[:16].replace("T", " ")


# --------------------------------------------------------------------------- #
# main study
# --------------------------------------------------------------------------- #


def render_study_page(pool: Pool, has_practice: bool) -> None:
    """Every rating is saved the moment it is given; signing in again continues at the
    next unrated image. No sessions, no going back."""
    username, role = current_username() or "", current_role() or "rater"
    if role == ADMIN:
        ui.navigate.to("/admin")
        return
    if role == "reviewer" and getattr(pool, "folder", None) is None:
        # Pool mode: Stage 1 reviewer accounts do not rate (doc 12 §4). In folder mode the folder named
        # after the account is its assignment, so the Stage 1 accounts (reviewer1–4) rate their own
        # folder with their existing credentials (decision of 31 Oct 2026, doc 12 "As set up").
        _topbar("Not a rater account", role, has_practice=False)
        ui.label("This account is for the Stage 1 dataset check, not the realism study."
                 ).classes("main-wrap text-base")
        return
    plan = get_plan(pool, username, RATER if role == "reviewer" else role)
    order = [sid for _, _, sid in flat(plan)]
    store = store_for(username)

    @ui.refreshable
    def content() -> None:
        if not same_account(username):
            return
        done = store.latest()
        remaining = [sid for sid in order if sid not in done]
        n_done = len(order) - len(remaining)
        _topbar(f"{n_done} / {len(order)} done · saved automatically", role, has_practice)
        if not remaining:
            _finished_card()
            return
        if n_done == 0 and has_practice:
            with ui.row().classes("notice items-center gap-2"):
                ui.label("First time here? Try the practice round first — it is not scored "
                         "and shows the answer after each page.")
                ui.button("Practice", icon="school",
                          on_click=lambda: ui.navigate.to("/practice")).props(
                    "unelevated no-caps dense")
        study_id = remaining[0]
        ui.label(f"Image {n_done + 1} of {len(order)}").classes("chip")
        rating_widget(
            f"/study-image/{study_id}",
            pool.items[study_id].get("width"),
            pool.max_clicks,
            pool.zoom_steps,
            lambda answer: _save(study_id, answer),
        )

    def _save(study_id: str, answer: Dict[str, Any]) -> None:
        if not require_active_session() or not same_account(username):
            return
        done = store.latest()
        nxt = next((sid for sid in order if sid not in done), None)
        if study_id in done or study_id != nxt:   # no going back, no double submit
            content.refresh()
            return
        # Folder mode scores the clicks against the image's mask here (never shown to the
        # rater); the blinded pool mode scores them later from the key.
        store.append({
            "rater": username, "role": role, "study_id": study_id, **answer,
            **pool.localize(study_id, answer["clicks"]),
            "position": order.index(study_id) + 1,
            "timestamp": now_iso(), "pool_version": pool.version,
        })
        content.refresh()

    with ui.column().classes("main-wrap w-full gap-3"):
        content()


def _finished_card() -> None:
    with ui.column().classes("card gap-2").style("max-width: 640px; margin: 24px auto"):
        ui.label("All done — thank you!").classes("title")
        ui.label("Every image in your list has been rated and saved. You can close this tab.")


# --------------------------------------------------------------------------- #
# practice (training, not scored, not saved)
# --------------------------------------------------------------------------- #


def save_pdf_button(on_save: Callable[[], Tuple[Optional[Path], List[int]]]) -> None:
    """Admin: "Save reference PDF" + where it was written. on_save() -> (path, missing page numbers)."""
    with ui.row().classes("items-center gap-3"):
        async def click() -> None:
            from nicegui import run
            try:
                path, missing = await run.io_bound(on_save)
            except Exception as exc:  # noqa: BLE001 - shown to the admin
                ui.notify(f"The PDF was not written: {exc}", type="negative", multi_line=True)
                return
            if path is None:
                ui.notify("Not saved yet: rate practice page(s) " + ", ".join(map(str, missing)) + " first.",
                          type="warning", multi_line=True)
                return
            label.set_text(f"Saved: {path}")
            ui.notify(f"Reference PDF saved: {path}", type="positive", multi_line=True)
            ui.download(path)

        ui.button("Save reference PDF", icon="picture_as_pdf", on_click=click).props("unelevated no-caps")
        label = ui.label("").classes("muted text-sm")


def render_practice_page(items: List[PracticeItem], image_url: Callable[[int], str],
                         max_clicks: int, zoom_steps: List[int],
                         margin_fraction: float,
                         on_answer: Callable[[int, PracticeItem, Dict[str, Any], Dict[str, Any]], None]
                         | None = None,
                         on_save: Callable[[], Tuple[Optional[Path], List[int]]] | None = None,
                         saved: Callable[[], Dict[str, Dict[str, Any]]] | None = None,
                         start: int = 0) -> None:
    """Admin only (all None for raters, whose practice answers are never saved):
    on_answer(index, item, answer, localization) saves the answer as a reference example;
    on_save() writes the reference PDF -> (path, missing page numbers); saved() -> the admin's saved
    answers by practice image. `start`: the page to begin with (0-based; the admin page links to each)."""
    admin = on_answer is not None
    state = {"index": max(0, min(start, len(items))), "answer": None}

    def go(index: int) -> None:
        state.update(index=index, answer=None)
        content.refresh()

    def submitted(a: Dict[str, Any]) -> None:
        if admin:
            item = items[state["index"]]
            result = localize(a["clicks"], mask_path=item.mask, boxes=item.boxes,
                              margin_fraction=margin_fraction)
            on_answer(state["index"], item, a, result)
        state.update(answer=a)
        content.refresh()

    @ui.refreshable
    def content() -> None:
        _topbar("Reference examples — your answers are saved" if admin else "Practice — not scored",
                current_role() or "rater", has_practice=False)
        answers = saved() if saved else {}
        if state["index"] >= len(items):
            with ui.column().classes("card gap-2").style("max-width: 680px; margin: 24px auto"):
                if admin:
                    ui.label(f"{len(answers)} of {len(items)} pages rated").classes("title")
                    ui.label("Save the reference PDF for the reviewers (results/stage2_reference/), or go "
                             "back to the admin page to re-rate a single page.").classes("text-sm")
                    if on_save is not None:
                        save_pdf_button(on_save)
                    with ui.row().classes("gap-2"):
                        ui.button("Back to admin", icon="arrow_back",
                                  on_click=lambda: ui.navigate.to("/admin")).props("outline no-caps")
                        ui.button("Rate again from page 1", on_click=lambda: go(0)).props("outline no-caps")
                    return
                ui.label("Practice finished").classes("title")
                ui.label("In the study itself there is no feedback after each page.")
                with ui.row().classes("gap-2"):
                    ui.button("Practise again", on_click=lambda: go(0)).props("outline no-caps")
                    ui.button("Go to the study", on_click=lambda: ui.navigate.to("/")).props(
                        "unelevated no-caps")
            return
        item = items[state["index"]]
        with ui.row().classes("items-center gap-2"):
            ui.label(f"{'Page' if admin else 'Practice image'} {state['index'] + 1} of {len(items)}").classes(
                "chip")
            if admin:
                ui.label(f"{len(answers)} of {len(items)} rated").classes("chip")
        if admin:
            ui.label("Rate each page as a reviewer would. Your answer is saved as the reference example; "
                     "rating a page again replaces it. When all pages are rated, press \"Save reference PDF\" "
                     "(here at the end, or on the admin page).").classes("muted text-sm")
            prev = answers.get(item.path.name)
            if prev is not None and state["answer"] is None:
                text = ("No edit on this page (5)" if prev.get("no_traces") else
                        f"{len(prev.get('clicks') or [])} place(s) marked, score {prev.get('score')}")
                with ui.row().classes("notice items-center gap-3"):
                    ui.label(f"Already saved: {text} ({_local(prev.get('timestamp'))}). "
                             "Rate it again to replace it, or keep it.")
                    ui.button("Keep it, next page", icon="arrow_forward",
                              on_click=lambda: go(state["index"] + 1)).props("outline no-caps")
        if state["answer"] is None:
            rating_widget(image_url(state["index"]), item.width, max_clicks, zoom_steps,
                          submitted, submit_label="Save my answer" if admin else "Check my answer")
            return
        if admin:
            ui.label("Saved.").classes("chip")
        _practice_feedback(item, state["answer"], image_url(state["index"]), margin_fraction,
                           show_reference=not admin)
        ui.button("Next page" if admin else "Next practice image", icon="arrow_forward",
                  on_click=lambda: go(state["index"] + 1)).props("unelevated no-caps")

    with ui.column().classes("main-wrap w-full gap-3"):
        content()


def _practice_feedback(item: PracticeItem, answer: Dict[str, Any], url: str,
                       margin_fraction: float, show_reference: bool = False) -> None:
    """What the answer measured (study/outcome.py), the reference answer (raters, once the admin has saved
    it), the note on what was edited, and the page with the real edits and the clicks."""
    result = localize(answer["clicks"], mask_path=item.mask, boxes=item.boxes,
                      margin_fraction=margin_fraction)
    clicks = result.get("clicks", answer["clicks"])
    edited = item.label == "tampered"
    n_edits = result.get("regions_total") or len(item.boxes)
    mine = page_measures(item.label, answer["score"], clicks, result.get("regions_found"),
                         result.get("regions_total"))
    ref = item.reference if show_reference else None
    with ui.column().classes("card w-full gap-1"):
        if edited:
            ui.label(f"This page was edited ({n_edits} edited place"
                     f"{'s' if n_edits != 1 else ''}).").classes("title")
        else:
            ui.label("This page is genuine — nothing was edited.").classes("title")
        ui.label("Your answer").classes("text-base font-bold")
        for line in describe(item.label, mine):
            ui.label(line).classes("text-sm")
        if ref:
            ui.label("Reference answer (study author)").classes("text-base font-bold")
            for line in describe(item.label, measures_of(item.label, ref)):
                ui.label(line).classes("text-sm")
        if item.note:
            ui.label("Note: " + item.note).classes(
                "muted text-sm")
        ui.label(("Red boxes: the real edits. " if edited else "")
                 + "Your clicks: green = on an edit, red = no edit there."
                 + (" Blue: the reference clicks." if ref and ref.get("clicks") else "")).classes("muted text-sm")
    radius = max(8.0, (item.width or 1000) * 0.012)
    markers = [{**c, "color": HIT if c.get("hit") else MISS} for c in clicks]
    overlay = box_overlay(item.boxes) + click_markers(markers, radius)
    if ref and ref.get("clicks"):
        overlay += click_markers([{**c, "color": REF} for c in ref["clicks"]], radius * 0.8)
    zoomable_image(url, zoom=100, overlay=overlay)


# --------------------------------------------------------------------------- #
# admin: reference examples + progress (counts only, never the raters' answers)
# --------------------------------------------------------------------------- #


def render_admin_page(pool_for, raters: List[str],
                      practice: List[PracticeItem] | None = None,
                      saved: Callable[[], Dict[str, Dict[str, Any]]] | None = None,
                      on_save: Callable[[], Tuple[Optional[Path], List[int]]] | None = None) -> None:
    """Two tabs. Reference examples: the admin's own answers on the practice pages (rate, re-rate one page,
    save the PDF). Reviewer progress: per account, how many pages are rated — counts only, never the
    answers — from data/stage2/outputs/<user>/ (on this machine, or collected from the reviewers' machines).
    `raters`: the accounts with a folder (folder mode); others appear once they have a plan."""
    practice = practice or []
    _topbar("Admin", ADMIN, has_practice=False)
    with ui.column().classes("main-wrap w-full gap-3"):
        with ui.tabs().classes("w-full") as tabs:
            t_ref = ui.tab("Reference examples", icon="fact_check")
            t_prog = ui.tab("Reviewer progress", icon="groups")
        with ui.tab_panels(tabs, value=t_ref).classes("w-full"):
            with ui.tab_panel(t_ref):
                _reference_panel(practice, saved, on_save)
            with ui.tab_panel(t_prog):
                _progress_panel(pool_for, raters)


def _reference_panel(practice: List[PracticeItem], saved, on_save) -> None:
    if not practice:
        ui.label("No practice pages: run python scripts/make_training.py (data/stage2/practice/).")
        return

    @ui.refreshable
    def body() -> None:
        answers = saved() if saved else {}
        missing = [n for n, it in enumerate(practice) if it.path.name not in answers]
        ui.label(f"{len(answers)} of {len(practice)} pages rated").classes("title")
        ui.label("Rate the pages as a reviewer would. There is no expected score: your answers are the "
                 "reference for the reviewers (the PDF, and shown after each of their practice pages). Each "
                 "answer is described by its measures: score, clicks on / off the edit, outcome, edit visibility. "
                 "Click a row to (re-)rate that page.").classes("muted")
        with ui.row().classes("gap-2 items-center"):
            first = missing[0] if missing else 0
            ui.button(("Start rating" if len(answers) == 0 else "Continue with page "
                       f"{first + 1}") if missing else "Rate again from page 1", icon="play_arrow",
                      on_click=lambda: ui.navigate.to(f"/practice?start={first + 1}")).props(
                "unelevated no-caps")
            if on_save is not None:
                save_pdf_button(on_save)
            ui.button("Refresh", icon="refresh", on_click=body.refresh).props("flat no-caps")
        rows = []
        for n, it in enumerate(practice, 1):
            a = answers.get(it.path.name)
            m = measures_of(it.label, a) if a else None
            rows.append({
                "n": n, "page": it.short or it.label,
                "score": "—" if m is None else m["score"],
                "clicks": "—" if m is None else (f"{m['hits']} / {m['misses']}" if m["clicks"] else "none"),
                "outcome": "—" if m is None else OUTCOMES[m["outcome"]][0],
                "visibility": "—" if m is None or m["edit_visibility"] is None else m["edit_visibility"],
                "saved": _local((a or {}).get("timestamp"))})
        columns = [{"name": k, "label": lab, "field": k, "align": "left"} for k, lab in (
            ("n", "#"), ("page", "Page"), ("score", "Score"), ("clicks", "Clicks on / off the edit"),
            ("outcome", "Outcome"), ("visibility", "Edit visibility"), ("saved", "Saved"))]
        table = ui.table(columns=columns, rows=rows, row_key="n").classes("w-full").props(
            "dense flat :rows-per-page-options=[0]")
        table.on("rowClick", lambda e: ui.navigate.to(f"/practice?start={e.args[1]['n']}"))

    body()


def _progress_panel(pool_for, raters: List[str]) -> None:
    @ui.refreshable
    def body() -> None:
        users = list(raters)
        if PLANS_DIR.is_dir():
            users += [p.stem for p in sorted(PLANS_DIR.glob("*.json"))
                      if p.stem.count(".") == 0 and p.stem not in users]
        rows = []
        for user in users:
            pool = pool_for(user)
            if pool is None:
                continue
            plan = read_json(PLANS_DIR / f"{user}.json") or {}
            if plan.get("pool_version") != pool.version:
                plan = {}
            total = len(flat(plan)) if plan else len(pool.items)
            kinds = {k: sum(1 for i in pool.items.values() if i["kind"] == k) for k in ("main", "repeat",
                                                                                        "control")}
            latest = store_for(user).latest() if (RATINGS_DIR / user).is_dir() else {}
            done = [r for r in latest.values()
                    if r.get("pool_version") == pool.version and r.get("study_id") in pool.items]
            by_kind = {k: sum(1 for r in done if pool.items[r["study_id"]]["kind"] == k) for k in kinds}
            history = RATINGS_DIR / user / "ratings.jsonl"
            updated = (_dt.datetime.fromtimestamp(history.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                       if history.is_file() else "—")
            status = ("no results yet" if not done else "complete" if len(done) >= total else "in progress")
            rows.append({
                "user": user, "role": plan.get("role", "—"), "status": status,
                "done": f"{len(done)} / {total}", "percent": f"{100 * len(done) / max(1, total):.0f} %",
                "main": f"{by_kind['main']} / {kinds['main']}",
                "repeats": f"{by_kind['repeat']} / {kinds['repeat']}",
                "controls": f"{by_kind['control']} / {kinds['control']}",
                "last": _local(max((r.get("timestamp", "") for r in done), default="")),
                "file": updated,
            })
        ui.label("Counts only, never the answers. Each reviewer rates on their own machine: copy their "
                 "data/stage2/outputs/<reviewer>/ and data/stage2/plans/<reviewer>.json into this folder "
                 "(docs/stage2_reviewer_machines_TODO.md, step C), then press Refresh.").classes("muted")
        ui.button("Refresh", icon="refresh", on_click=body.refresh).props("flat no-caps")
        columns = [{"name": k, "label": lab, "field": k, "align": "left"} for k, lab in (
            ("user", "Account"), ("status", "Status"), ("done", "Rated"), ("percent", "%"),
            ("main", "Main pages"), ("repeats", "Repeats"), ("controls", "Controls"),
            ("last", "Last answer"), ("file", "Results file updated"), ("role", "Plan role"))]
        ui.table(columns=columns, rows=rows, row_key="user").classes("w-full").props(
            "dense flat :rows-per-page-options=[0]")
        ui.label(f"Pilot accounts ({PILOT}) are listed but never analysed.").classes("muted")

    body()


# --------------------------------------------------------------------------- #


def _topbar(status: str, role: str, has_practice: bool) -> None:
    with ui.element("div").classes("topbar"):
        ui.label("Document Realism Study").classes("title")
        ui.label(status).classes("chip")
        if role == PILOT:
            ui.label("PILOT").classes("chip chip-warn")
        ui.space()
        if has_practice:
            ui.button("Practice", icon="school", on_click=lambda: ui.navigate.to("/practice")
                      ).props("flat no-caps")
        if role == ADMIN:
            ui.button("Admin", icon="dashboard", on_click=lambda: ui.navigate.to("/admin")
                      ).props("flat no-caps")
        ui.button("Instructions", icon="help_outline", on_click=show_instructions).props(
            "flat no-caps")
        ui.label(current_username() or "").classes("chip")
        ui.button("Sign out", icon="logout",
                  on_click=lambda: (logout_session(), ui.navigate.to("/login"))).props(
            "flat no-caps")


def show_instructions() -> None:
    with ui.dialog() as dialog, ui.card().classes("card").style("max-width: 820px"):
        ui.label("Instructions").classes("title")
        ui.markdown(
            "Some documents are genuine and some were edited in one or two places. "
            "The proportion is not given. The documents are **confidential**: do not copy, "
            "photograph or share them.\n\n"
            "For each page:\n\n"
            "1. Look at the whole page. You may zoom up to 200 %.\n"
            "2. If nothing looks edited, press **No edit on this page**.\n"
            "3. Otherwise **click on every place you think was edited** (up to 3), **the place "
            "you are most sure about first** — each click means \"an edit is here\". The bin icon "
            "next to a place removes it.\n"
            "4. Give **one score for the whole page** — not one per place — from 5 (looks "
            "genuine) to 1 (clearly edited). With several places marked, judge by the strongest "
            "sign of editing you found.\n\n"
            "Every answer is saved at once. You can stop at any time and sign in again "
            "later: you continue where you left off. Take as long as you need; there is no "
            "feedback and no going back."
        )
        columns = [{"name": "score", "label": "Score", "field": "score"},
                   {"name": "label", "label": "Label", "field": "label", "align": "left"},
                   {"name": "meaning", "label": "Meaning", "field": "meaning", "align": "left"}]
        ui.table(columns=columns, rows=[{"score": s, "label": lab, "meaning": m}
                                        for s, lab, m in SCALE], row_key="score")
        ui.button("Close", on_click=dialog.close).props("flat no-caps")
    dialog.open()
