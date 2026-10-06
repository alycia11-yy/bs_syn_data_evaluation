"""The rating widget: image with up to N clicks, each 'edit' or 'looks odd', and a 1–5 score.

Shared by the main study and the practice round. The caller gets one answer dict:
    {"no_traces": bool, "clicks": [{"x", "y", "kind"}], "score": int,
     "time_ms": int, "max_zoom": int}
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from nicegui import ui

from core.ui import click_markers
from study.ratings import CLICK_KINDS, EDIT, ODD, SCALE, allowed_scores, validate


@dataclass
class Answer:
    clicks: List[Dict[str, Any]] = field(default_factory=list)
    score: Optional[int] = None
    zoom: int = 100
    max_zoom: int = 100
    started: float = field(default_factory=time.monotonic)


def rating_widget(
    image_url: str,
    width: Optional[int],
    max_clicks: int,
    zoom_steps: List[int],
    on_submit: Callable[[Dict[str, Any]], None],
) -> None:
    answer = Answer(zoom=zoom_steps[0], max_zoom=zoom_steps[0])
    radius = max(8.0, (width or 1000) * 0.012)
    submitted = {"done": False}

    def finish(no_traces: bool) -> None:
        if submitted["done"]:          # double click / Enter + click
            return
        clicks = [] if no_traces else answer.clicks
        score = 5 if no_traces else answer.score
        problem = validate(clicks, score, max_clicks)
        if problem:
            ui.notify(problem, type="warning")
            return
        submitted["done"] = True
        on_submit({
            "no_traces": no_traces,
            "clicks": [{"x": round(c["x"], 1), "y": round(c["y"], 1), "kind": c["kind"]}
                       for c in clicks],
            "score": score,
            "time_ms": int((time.monotonic() - answer.started) * 1000),
            "max_zoom": answer.max_zoom,
        })

    def on_click(e) -> None:
        if len(answer.clicks) >= max_clicks:
            ui.notify(f"At most {max_clicks} places. Remove one to choose another.",
                      type="warning")
            return
        answer.clicks.append({"x": e.image_x, "y": e.image_y, "kind": None})
        _sync()

    def set_kind(index: int, kind: str) -> None:
        answer.clicks[index]["kind"] = kind
        _sync()

    def remove(index: int) -> None:
        answer.clicks.pop(index)
        _sync()

    def set_score(value: int) -> None:
        answer.score = value
        side.refresh()

    def set_zoom(value: int) -> None:
        answer.zoom = value
        answer.max_zoom = max(answer.max_zoom, value)
        image.style(f"width: {value}%; display: block;")

    def _sync() -> None:
        if answer.score not in allowed_scores([c for c in answer.clicks if c["kind"]]) \
                or any(c["kind"] is None for c in answer.clicks):
            answer.score = None
        image.content = click_markers(answer.clicks, radius)
        side.refresh()

    @ui.refreshable
    def side() -> None:
        clicks = answer.clicks
        if not clicks:
            ui.label("Look at the whole page. If nothing looks edited:").classes("muted")
            ui.button("I saw no edit traces", icon="verified",
                      on_click=lambda: finish(True)).props(
                "unelevated no-caps color=positive").classes("big-btn")
            ui.label(f"Otherwise click on the image where something looks wrong "
                     f"(up to {max_clicks} places).").classes("muted")
            return
        for n, click in enumerate(clicks):
            with ui.column().classes("w-full gap-1"):
                with ui.row().classes("items-center w-full gap-2"):
                    ui.label(f"Place {n + 1}").classes("section-title")
                    ui.space()
                    ui.button(icon="delete", on_click=lambda i=n: remove(i)).props(
                        "flat dense round size=sm").tooltip("Remove this place")
                with ui.row().classes("w-full gap-2 no-wrap"):
                    for kind, colour in ((EDIT, "red"), (ODD, "amber-9")):
                        selected = click["kind"] == kind
                        ui.button(CLICK_KINDS[kind], on_click=lambda i=n, k=kind: set_kind(i, k)
                                  ).props(f"{'unelevated' if selected else 'outline'} no-caps "
                                          f"color={colour} dense").classes("grow")
        if any(c["kind"] is None for c in clicks):
            ui.label("Choose 'edit' or 'looks odd' for each place.").classes("notice")
            return
        ui.separator()
        ui.label("How genuine does the document look?").classes("section-title")
        allowed = allowed_scores(clicks)
        for score, label, meaning in SCALE:
            if score not in allowed:
                continue
            ui.button(f"{score} · {label}", on_click=lambda s=score: set_score(s)).props(
                f"{'unelevated' if answer.score == score else 'outline'} no-caps").classes(
                "score-btn")
            ui.label(meaning).classes("muted text-xs").style("margin: -4px 0 4px 8px")
        ui.button("Submit and next", icon="arrow_forward",
                  on_click=lambda: finish(False)).props(
            f"unelevated no-caps {'disable' if answer.score is None else ''}").classes(
            "big-btn mt-2")

    with ui.element("div").classes("layout"):
        with ui.element("div").classes("layout-main"):
            with ui.row().classes("items-center gap-3 mb-1"):
                ui.label("Zoom").classes("muted")
                ui.toggle({z: f"{z}%" for z in zoom_steps}, value=answer.zoom,
                          on_change=lambda e: set_zoom(e.value)).props("dense no-caps")
                ui.label("Click on the image to mark a place.").classes("muted text-sm")
            with ui.element("div").classes("img-scroll"):
                image = ui.interactive_image(
                    image_url, on_mouse=on_click, events=["click"], cross="#0ea5e9",
                ).style(f"width: {answer.zoom}%; display: block; cursor: crosshair;")
        with ui.column().classes("layout-side card gap-2"):
            side()
