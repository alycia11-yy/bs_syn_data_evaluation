"""Stage 2 pages: the rating flow (session gate -> images -> done), practice and admin."""
from __future__ import annotations

from typing import Any, Dict, List

from nicegui import app, ui

from auth import current_role, current_username, logout_session
from auth.middleware import require_active_session
from core.jsonstore import read_json
from core.settings import PLANS_DIR, RATINGS_DIR
from core.ui import box_overlay, click_markers, zoomable_image
from review.selection import now_iso
from study.plan import ADMIN, PILOT, Pool, flat, get_plan
from study.ratings import CLICK_KINDS, SCALE, store_for
from study.widget import rating_widget

MINUTES_PER_IMAGE = 10 / 60  # doc 12 §3: about 8-12 s per image


# --------------------------------------------------------------------------- #
# main study
# --------------------------------------------------------------------------- #


def render_study_page(pool: Pool) -> None:
    username, role = current_username() or "", current_role() or "rater"
    if role == ADMIN:
        ui.navigate.to("/admin")
        return
    if role == "reviewer":
        # Stage 1 reviewers have seen the edits and masks: they must not rate (doc 12 §4).
        _topbar("Not a rater account", role)
        ui.label("This account is for the Stage 1 dataset check, not the realism study."
                 ).classes("main-wrap text-base")
        return
    plan = get_plan(pool, username, role)
    steps = flat(plan)
    store = store_for(username)
    n_sessions = len(plan["sessions"])

    @ui.refreshable
    def content() -> None:
        done = store.latest()
        remaining = [(s, p, sid) for s, p, sid in steps if sid not in done]
        _topbar(f"{len(steps) - len(remaining)} / {len(steps)} done", role)
        if not remaining:
            _finished_card()
            return
        session, position, study_id = remaining[0]
        session_key = f"{pool.version}:{session}"
        if app.storage.user.get("active_session") != session_key:
            _session_gate(session, n_sessions, plan["sessions"][session], position,
                          lambda: _start_session(session_key))
            return
        with ui.row().classes("items-center gap-2"):
            ui.label(f"Session {session + 1} of {n_sessions}").classes("chip")
            ui.label(f"Image {position + 1} of {len(plan['sessions'][session])}").classes("chip")
        item = pool.items[study_id]
        rating_widget(
            f"/study-image/{study_id}",
            item.get("width"),
            pool.max_clicks,
            pool.zoom_steps,
            lambda answer: _save(study_id, session, position, answer),
        )

    def _start_session(key: str) -> None:
        if require_active_session():
            app.storage.user["active_session"] = key
            content.refresh()

    def _save(study_id: str, session: int, position: int, answer: Dict[str, Any]) -> None:
        if not require_active_session():
            return
        done = store.latest()
        nxt = next((sid for _, _, sid in steps if sid not in done), None)
        if study_id in done or study_id != nxt:   # no going back, no double submit
            content.refresh()
            return
        store.append({
            "rater": username, "role": role, "study_id": study_id, **answer,
            "session": session + 1, "position": position + 1,
            "timestamp": now_iso(), "pool_version": pool.version,
        })
        if position + 1 == len(plan["sessions"][session]):
            app.storage.user["active_session"] = None   # end of session -> break screen
        content.refresh()

    with ui.column().classes("main-wrap w-full gap-3"):
        content()


def _session_gate(session: int, n_sessions: int, ids: List[str], position: int,
                  start) -> None:
    with ui.column().classes("card gap-3").style("max-width: 760px; margin: 24px auto"):
        resumed = position > 0
        ui.label(f"{'Continue' if resumed else 'Start'} session {session + 1} of {n_sessions}"
                 ).classes("title")
        left = len(ids) - position
        ui.label(f"{left} images left in this session, about "
                 f"{max(1, round(left * MINUTES_PER_IMAGE))} minutes.").classes("text-base")
        ui.markdown(
            "- Take as long as you need for each page. Zoom up to 200 % is allowed.\n"
            "- There is no going back to an earlier page and no feedback during the study.\n"
            "- Please do one session at a time and take a break between sessions "
            "(ideally on different days).\n"
            "- Browser zoom at 100 %, a large screen or a laptop at full brightness."
        )
        if session == 0 and not resumed:
            ui.link("First time? Do the practice round (not scored).", "/practice")
        ui.button(f"{'Continue' if resumed else 'Start'} session", icon="play_arrow",
                  on_click=start).props("unelevated no-caps").classes("big-btn")


def _finished_card() -> None:
    with ui.column().classes("card gap-2").style("max-width: 640px; margin: 24px auto"):
        ui.label("All done — thank you!").classes("title")
        ui.label("Every image in your list has been rated and saved. You can close this tab.")


# --------------------------------------------------------------------------- #
# practice (training, not scored)
# --------------------------------------------------------------------------- #


def render_practice_page(practice: Dict[str, Any], image_url, max_clicks: int,
                         zoom_steps: List[int]) -> None:
    items = practice.get("items", [])
    state = {"index": 0, "answer": None}

    @ui.refreshable
    def content() -> None:
        _topbar("Practice — not scored", current_role() or "rater")
        if state["index"] >= len(items):
            with ui.column().classes("card gap-2").style("max-width: 640px; margin: 24px auto"):
                ui.label("Practice finished").classes("title")
                ui.label("The real study gives no feedback after each page.")
                ui.button("Go to the study", on_click=lambda: ui.navigate.to("/")).props(
                    "unelevated no-caps")
            return
        item = items[state["index"]]
        ui.label(f"Practice image {state['index'] + 1} of {len(items)}").classes("chip")
        if state["answer"] is None:
            rating_widget(image_url(item["image"]), item.get("width"), max_clicks, zoom_steps,
                          lambda a: (state.update(answer=a), content.refresh()))
            return
        _practice_feedback(item, state["answer"], image_url(item["image"]))
        ui.button("Next practice image", icon="arrow_forward",
                  on_click=lambda: (state.update(index=state["index"] + 1, answer=None),
                                    content.refresh())).props("unelevated no-caps")

    with ui.column().classes("main-wrap w-full gap-3"):
        content()


def _practice_feedback(item: Dict[str, Any], answer: Dict[str, Any], url: str) -> None:
    edited = item.get("label") == "tampered"
    with ui.column().classes("card w-full gap-1"):
        ui.label("This page was edited." if edited else "This page is genuine.").classes(
            "title")
        places = ", ".join(CLICK_KINDS[c["kind"]] for c in answer["clicks"]) or "no clicks"
        ui.label(f"Your score: {answer['score']} · {places}").classes("muted")
        if item.get("note"):
            ui.label(item["note"])
        ui.label("Red boxes: the edits. Numbered circles: your clicks.").classes("muted text-sm")
    radius = max(8.0, (item.get("width") or 1000) * 0.012)
    zoomable_image(url, zoom=100,
                   overlay=box_overlay(item.get("boxes") or []) + click_markers(
                       answer["clicks"], radius))


# --------------------------------------------------------------------------- #
# admin: progress only, never the answers
# --------------------------------------------------------------------------- #


def render_admin_page(pool: Pool) -> None:
    _topbar("Admin · progress", ADMIN)
    rows = []
    for path in sorted(PLANS_DIR.glob("*.json")) if PLANS_DIR.is_dir() else []:
        plan = read_json(path) or {}
        if plan.get("pool_version") != pool.version:
            continue
        user = plan["username"]
        steps = flat(plan)
        latest = store_for(user).latest() if (RATINGS_DIR / user).is_dir() else {}
        done = [r for r in latest.values() if r.get("pool_version") == pool.version]
        last = max((r["timestamp"] for r in done), default="—")
        next_step = next(((s, p) for s, p, sid in steps if sid not in latest), None)
        rows.append({
            "user": user, "role": plan["role"], "done": len(done), "total": len(steps),
            "percent": f"{100 * len(done) / max(1, len(steps)):.0f} %",
            "session": f"{next_step[0] + 1} / {len(plan['sessions'])}" if next_step else "done",
            "last": last,
        })
    with ui.column().classes("main-wrap w-full gap-3"):
        ui.label(f"Pool {pool.version}: {len(pool.items)} entries "
                 f"(main + repeats + controls). Plans appear after a user's first sign-in."
                 ).classes("muted")
        columns = [{"name": k, "label": k.title(), "field": k, "align": "left"}
                   for k in ("user", "role", "done", "total", "percent", "session", "last")]
        ui.table(columns=columns, rows=rows, row_key="user").classes("w-full")
        ui.label(f"Pilot users ({PILOT}) are listed but never analysed.").classes("muted")


# --------------------------------------------------------------------------- #


def _topbar(status: str, role: str) -> None:
    with ui.element("div").classes("topbar"):
        ui.label("Document Realism Study").classes("title")
        ui.label(status).classes("chip")
        if role == PILOT:
            ui.label("PILOT").classes("chip chip-warn")
        ui.space()
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
            "1. Look at the page. You may zoom up to 200 %.\n"
            "2. If you see nothing that looks edited, press **I saw no edit traces**.\n"
            "3. Otherwise click on the image where something looks wrong (up to 3 places), "
            "and for each place choose **I saw an edit here** or **It just looks odd here**.\n"
            "4. Give the score. With an 'edit' place the score is 1–2; with only "
            "'looks odd' places it is 3–4.\n\n"
            "Take as long as you need. There is no feedback and no going back."
        )
        columns = [{"name": "score", "label": "Score", "field": "score"},
                   {"name": "label", "label": "Label", "field": "label", "align": "left"},
                   {"name": "meaning", "label": "Meaning", "field": "meaning", "align": "left"}]
        ui.table(columns=columns, rows=[{"score": s, "label": lab, "meaning": m}
                                        for s, lab, m in SCALE], row_key="score")
        ui.button("Close", on_click=dialog.close).props("flat no-caps")
    dialog.open()
