"""Stage 1 page: look at one image, press Keep (Y) or Reject (N + reason)."""
from __future__ import annotations

from typing import Any, Dict, List
from urllib.parse import quote

from nicegui import ui

from core.ui import box_overlay, zoomable_image
from review.selection import KEEP, PENDING, REJECT, REJECT_REASONS
from review.state import ADMIN, ALL, ZOOM_STEPS, ReviewData, ReviewView

_STATUS_CHIP = {KEEP: "chip-keep", REJECT: "chip-reject", PENDING: "chip-pending"}
_STATUS_TEXT = {KEEP: "Kept", REJECT: "Rejected", PENDING: "Not decided"}


def image_url(item_file: str, version: str = "") -> str:
    return f"/dataset-image/{quote(item_file)}" + (f"?v={version}" if version else "")


def render_review_page(data: ReviewData, view: ReviewView, *, multi_user: bool = False) -> None:
    @ui.refreshable
    def content() -> None:
        items = view.filtered(data)
        view.clamp(len(items))
        _render_topbar(data, view, items, refresh, multi_user)
        if data.restricted(view.user, view.role) and not data.assignments.get(view.user):
            with ui.card().classes("card w-full"):
                ui.label(f"No images for {view.user} on this machine.").classes("text-lg")
                ui.label(f"Expected a folder named {view.user} in data/stage1_split/. "
                         "Ask the study owner for your folder.").classes("muted")
            return
        if not items:
            with ui.card().classes("card w-full"):
                ui.label("Nothing left in this filter.").classes("text-lg")
                ui.label("Change the filters above, or the status filter to 'all'.").classes(
                    "muted")
            return
        item = items[view.index]
        with ui.element("div").classes("layout"):
            with ui.element("div").classes("layout-main"):
                _render_views(item, view)
            with ui.element("div").classes("layout-side"):
                _render_side_panel(data, view, item, refresh, advance)

    def refresh() -> None:
        content.refresh()

    def advance(next_id: str | None) -> None:
        # A decided image may leave the filtered list (e.g. 'pending'), so follow the
        # image that came after it rather than an index.
        view.reset_decision_ui()
        if next_id is not None:
            ids = [i["id"] for i in view.filtered(data)]
            view.index = ids.index(next_id) if next_id in ids else view.index
        else:
            view.index += 1
        refresh()

    def on_key(e: Any) -> None:
        if not e.action.keydown or e.action.repeat:
            return
        items = view.filtered(data)
        if not items:
            return
        view.clamp(len(items))
        item = items[view.index]
        key = str(e.key.name).lower() if e.key.name else ""
        if key in {"y", "k"}:
            _decide(data, view, item, KEEP, "", advance)
        elif key in {"n", "r"}:
            view.pending_reject = True
            refresh()
        elif view.pending_reject and key.isdigit() and 1 <= int(key) <= len(REJECT_REASONS):
            _decide(data, view, item, REJECT, REJECT_REASONS[int(key) - 1][0], advance)
        elif key == "escape":
            view.reset_decision_ui()
            refresh()
        elif key == "d" and view.pending_reject and item.get("doc_id"):
            view.reject_scope_document = not view.reject_scope_document
            refresh()
        elif e.key.arrow_left:
            view.index -= 1
            view.reset_decision_ui()
            refresh()
        elif e.key.arrow_right:
            view.index += 1
            view.reset_decision_ui()
            refresh()
        elif key == "b":
            view.show_boxes = not view.show_boxes
            refresh()
        elif key == "a":
            view.show_references = not view.show_references
            refresh()
        elif key in {"+", "="}:
            view.zoom = _step_zoom(view.zoom, +1)
            refresh()
        elif key in {"-", "_"}:
            view.zoom = _step_zoom(view.zoom, -1)
            refresh()

    ui.keyboard(on_key=on_key, ignore=["input", "textarea", "select"])
    with ui.column().classes("main-wrap w-full gap-3"):
        content()


# --------------------------------------------------------------------------- #
# top bar: progress + filters
# --------------------------------------------------------------------------- #


def _render_topbar(data: ReviewData, view: ReviewView, items, refresh, multi_user) -> None:
    counts = data.counts(view.user, view.role)
    with ui.element("div").classes("topbar"):
        ui.label("Stage 1 · Dataset check").classes("title")
        if data.restricted(view.user, view.role):
            ui.label("your share of the images").classes("chip")
        if data.recheck:
            ui.label("RE-CHECK MODE — earlier decisions hidden").classes("chip chip-warn")
        total = sum(sum(c.values()) for c in counts.values())
        done = sum(c[KEEP] + c[REJECT] for c in counts.values())
        ui.label(f"{done} / {total} decided").classes("chip")
        for set_name, c in sorted(counts.items()):
            ui.label(
                f"{set_name}: {c[KEEP]} kept · {c[REJECT]} rejected · {c[PENDING]} left"
            ).classes("chip")
        ui.space()
        ui.button("Shortcuts", icon="keyboard", on_click=_show_help).props(
            "flat no-caps dense")
        if multi_user:
            ui.label(f"{view.user} ({view.role})").classes("chip")
            ui.button("Sign out", icon="logout", on_click=_sign_out).props("flat no-caps dense")

    if data.assignments and view.role == ADMIN and not data.recheck:
        with ui.element("div").classes("topbar"):
            ui.label("Reviewers").classes("section-title")
            for user, (done, total) in data.progress_by_reviewer().items():
                ui.label(f"{user}: {done} / {total}").classes("chip")

    with ui.element("div").classes("topbar"):
        set_names = sorted({item["set"] for item in data.items})
        _select("Set", [ALL, *set_names], view, "set_filter", refresh)
        _select("Status", [PENDING, KEEP, REJECT, ALL], view, "status_filter", refresh)
        _select("Label", [ALL, "authentic", "tampered", "unknown"], view, "label_filter", refresh)
        if data.assignments and view.role == ADMIN:
            _select("Assigned to", [ALL, *sorted(data.assignments)], view, "assignee_filter",
                    refresh)

        def go(delta: int) -> None:
            view.index += delta
            view.reset_decision_ui()
            refresh()

        ui.button(icon="chevron_left", on_click=lambda: go(-1)).props("flat dense").tooltip("←")
        ui.label(f"{min(view.index + 1, len(items))} / {len(items)}").classes("chip")
        ui.button(icon="chevron_right", on_click=lambda: go(+1)).props("flat dense").tooltip("→")

        def jump(e) -> None:
            try:
                view.index = int(e.sender.value) - 1
            except (TypeError, ValueError):
                return
            view.reset_decision_ui()
            refresh()

        ui.number("Go to #", min=1, format="%d").props("dense outlined").classes("w-28").on(
            "keydown.enter", jump)
        ui.label("Zoom").classes("muted")
        ui.toggle([z for z in ZOOM_STEPS], value=view.zoom,
                  on_change=lambda e: (setattr(view, "zoom", e.value), refresh())).props(
            "dense no-caps")
        ui.switch("Label boxes (B)", value=view.show_boxes,
                  on_change=lambda e: (setattr(view, "show_boxes", e.value), refresh()))
        ui.switch("Authentic page + mask (A)", value=view.show_references,
                  on_change=lambda e: (setattr(view, "show_references", e.value), refresh()))


def _select(label: str, options, view: ReviewView, attr: str, refresh) -> None:
    def changed(e) -> None:
        setattr(view, attr, e.value)
        view.index = 0
        view.reset_decision_ui()
        refresh()

    ui.select(options, value=getattr(view, attr), label=label, on_change=changed).props(
        "dense outlined").classes("w-36")


# --------------------------------------------------------------------------- #
# image(s)
# --------------------------------------------------------------------------- #


def _render_views(item: Dict[str, Any], view: ReviewView) -> None:
    """The tampered image first; its authentic page and mask only on request (A)."""
    overlay = box_overlay(item.get("boxes") or []) if view.show_boxes else ""
    views = item.get("views") or {}
    panels = [("tampered image" if views else "", item["file"], overlay)]
    if view.show_references:
        panels += [(name, path, "") for name, path in views.items()]
    with ui.element("div").classes("view-grid" if len(panels) > 1 else "w-full"):
        for name, path, svg in panels:
            with ui.column().classes("w-full gap-1"):
                if name:
                    ui.label(name).classes("view-label")
                zoomable_image(image_url(path), zoom=view.zoom, overlay=svg)


# --------------------------------------------------------------------------- #
# side panel: metadata + decision
# --------------------------------------------------------------------------- #


def _render_side_panel(data: ReviewData, view: ReviewView, item, refresh, advance) -> None:
    status, record = data.status(item, view.user)
    with ui.column().classes("card w-full gap-2"):
        with ui.row().classes("items-center gap-2"):
            ui.label("Current").classes("section-title")
            ui.label(_STATUS_TEXT[status]).classes(f"chip {_STATUS_CHIP[status]}")
            if record and record.get("reason"):
                ui.label(record["reason"]).classes("chip")
        if record and record.get("id") != item["id"] and status != PENDING:
            ui.label(f"Decided on {record['id']} ({record.get('scope', 'image')})").classes(
                "muted text-sm")

        ui.button("Keep  (Y)", icon="check", color="positive",
                  on_click=lambda: _decide(data, view, item, KEEP, "", advance)
                  ).props("unelevated no-caps").classes("big-btn")
        ui.button("Reject  (N)", icon="close", color="negative",
                  on_click=lambda: (setattr(view, "pending_reject", True), refresh())
                  ).props("unelevated no-caps" if not view.pending_reject else "outline no-caps"
                          ).classes("big-btn")

        if view.pending_reject:
            ui.label("Why? (press 1–6)").classes("section-title")
            for n, (key, label, help_text) in enumerate(REJECT_REASONS, start=1):
                ui.button(f"{n}  {label}", on_click=lambda k=key: _decide(data, view, item, REJECT,
                                                                       k, advance)
                          ).props("outline no-caps color=negative").classes(
                    "reason-btn").tooltip(help_text)
            if item.get("doc_id"):
                ui.checkbox(
                    "Reject the whole document (D) — every version, both paired sets",
                    value=view.reject_scope_document,
                    on_change=lambda e: setattr(view, "reject_scope_document", e.value),
                )
            ui.button("Cancel (Esc)", on_click=lambda: (view.reset_decision_ui(), refresh())
                      ).props("flat no-caps")

        ui.input("Note (optional)", value=view.note,
                 on_change=lambda e: setattr(view, "note", e.value)).props(
            "dense outlined").classes("w-full")
        ui.label("Reject obvious errors only. A merely noticeable edit is kept — "
                 "Stage 2 measures how noticeable.").classes("muted text-xs")

    edits = _edit_lines(item)
    if edits:
        with ui.column().classes("card w-full gap-1 mt-2"):
            ui.label("What was changed").classes("section-title")
            for line in edits:
                ui.label(line).classes("text-sm").style(
                    "font-family: ui-monospace, Menlo, Consolas, monospace")

    with ui.column().classes("card w-full gap-1 mt-2"):
        ui.label("Image").classes("section-title")
        with ui.element("div").classes("kv"):
            for key, value in _info_rows(data, item):
                ui.label(key).classes("k")
                ui.label(str(value)).classes("v")


def _decide(data: ReviewData, view: ReviewView, item, decision: str, reason: str,
            advance) -> None:
    items = view.filtered(data)
    position = next((n for n, it in enumerate(items) if it["id"] == item["id"]), view.index)
    next_id = items[position + 1]["id"] if position + 1 < len(items) else None
    scope = "document" if view.reject_scope_document else "image"
    data.decide(item, decision, reviewer=view.user, reason=reason, scope=scope, note=view.note)
    advance(next_id)


def _info_rows(data: ReviewData, item: Dict[str, Any]):
    rows = [
        ("id", item["id"]),
        ("set", f"{item['set']} · {item['side']} · {item['family']}"),
        ("label", item["label"]),
    ]
    for key in ("doc_id", "method", "model", "edit_type", "layout", "capture"):
        if item.get(key):
            rows.append((key, item[key]))
    if item.get("width"):
        rows.append(("size", f"{item['width']} × {item['height']} px"))
    if item.get("boxes"):
        rows.append(("boxes", len(item["boxes"])))
    if data.assignee.get(item["id"]):
        rows.append(("assigned to", data.assignee[item["id"]]))
    duplicates = data.duplicates_of(item["id"])
    if duplicates:
        rows.append(("same file", ", ".join(duplicates)))
    siblings = data.siblings(item)
    if siblings:
        rows.append(("same doc", f"{len(siblings)} other image(s)"))
    extra = item.get("extra") or {}
    for key in ("tier", "n_edits", "tampered_area_fraction", "split", "heldout_layout",
                "fallback"):
        if extra.get(key) not in (None, "", []):
            rows.append((key, extra[key]))
    return rows


def _edit_lines(item: Dict[str, Any]) -> List[str]:
    """One line per edit: field, old text -> new text (from labels.jsonl)."""
    extra = item.get("extra") or {}
    lines = []
    for n, edit in enumerate(extra.get("edits") or extra.get("box_info") or [], start=1):
        if not isinstance(edit, dict):
            continue
        before = edit.get("text_before") or edit.get("words_before") or ""
        after = edit.get("text_after") or edit.get("words_after") or ""
        field = edit.get("category") or edit.get("op") or "edit"
        lines.append(f"{n}. {field}: '{before}' → '{after}'")
    return lines


def _sign_out() -> None:
    from auth import logout_session

    logout_session()
    ui.navigate.to("/login")


def _step_zoom(current: int, direction: int) -> int:
    steps = list(ZOOM_STEPS)
    i = steps.index(current) if current in steps else steps.index(100)
    return steps[max(0, min(len(steps) - 1, i + direction))]


def _show_help() -> None:
    with ui.dialog() as dialog, ui.card().classes("card"):
        ui.label("Keyboard shortcuts").classes("text-lg")
        for keys, what in (
            ("Y / K", "keep"),
            ("N / R", "reject — then 1–6 for the reason"),
            ("D", "while rejecting: toggle whole-document reject"),
            ("Esc", "cancel a reject"),
            ("← / →", "previous / next image"),
            ("B", "show / hide label boxes"),
            ("A", "show / hide the authentic page and the mask"),
            ("+ / −", "zoom in / out"),
        ):
            with ui.row().classes("items-center gap-3"):
                ui.label(keys).classes("kbd")
                ui.label(what)
        ui.markdown(
            "Every decision is saved immediately to `data/stage1/decisions/` and "
            "`data/stage1/selection.json` is rewritten. Re-deciding an image replaces "
            "its earlier decision; the history keeps both."
        )
        ui.button("Close", on_click=dialog.close).props("flat no-caps")
    dialog.open()
