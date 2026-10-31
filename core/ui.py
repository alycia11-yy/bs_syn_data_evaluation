"""Shared look and the zoomable image used by both stages."""
from __future__ import annotations

from typing import Callable, List, Optional, Sequence

from nicegui import ui

_CSS = """
body {
  background: linear-gradient(160deg, #fff6e8 0%, #d7f7f4 45%, #ffffff 100%);
  color: #1f2937; font-size: 16px; line-height: 1.5;
}
.main-wrap { max-width: 1680px; margin: 0 auto; padding: 14px 20px 40px 20px; }
.login-wrap { width: min(92vw, 24rem); }
.card {
  background: rgba(255,255,255,0.78); color: #111827;
  border: 1px solid rgba(15,23,42,0.16); border-radius: 10px;
  box-shadow: 0 10px 28px rgba(15,23,42,0.07); padding: 14px 16px;
}
.topbar { display: flex; flex-wrap: wrap; gap: 8px 12px; align-items: center; width: 100%; }
.title { font-size: 22px; font-weight: 700; }
.chip {
  background: rgba(255,255,255,0.7); border: 1px solid rgba(17,24,39,0.16);
  border-radius: 999px; padding: 3px 11px; font-size: 14px; white-space: nowrap;
}
.chip-keep { background: #dcfce7; border-color: #86efac; color: #166534; }
.chip-reject { background: #fee2e2; border-color: #fca5a5; color: #991b1b; }
.chip-pending { background: #f1f5f9; color: #334155; }
.chip-warn { background: #ffedd5; border-color: #fb923c; color: #7c2d12; font-weight: 700; }
.muted { color: #475569; }
.section-title { color: #334155; font-size: 13px; font-weight: 700;
  text-transform: uppercase; letter-spacing: 0.05em; }
.layout { display: flex; gap: 16px; align-items: flex-start; width: 100%; }
.layout-main { flex: 1 1 auto; min-width: 0; }
.layout-side { flex: 0 0 380px; width: 380px; position: sticky; top: 12px; }
.img-scroll {
  overflow: auto; max-height: calc(100vh - 170px); width: 100%;
  background: #f8fafc; border: 1px solid rgba(15,23,42,0.15); border-radius: 8px;
}
.img-scroll .q-img, .img-scroll img { max-width: none !important; }
/* statement crops are wide: authentic / this image / mask stack, so they line up */
.view-grid { display: grid; gap: 10px; width: 100%; grid-template-columns: 1fr; }
.view-label { font-size: 13px; font-weight: 700; color: #0f766e; text-transform: uppercase; }
.kv { display: grid; grid-template-columns: 110px 1fr; gap: 2px 10px; font-size: 14px; }
.kv .k { color: #64748b; }
.kv .v { font-family: ui-monospace, Menlo, Consolas, monospace; overflow-wrap: anywhere; }
.big-btn { width: 100%; font-size: 17px !important; padding: 10px !important; }
.reason-btn { width: 100%; justify-content: flex-start !important; text-align: left; }
.kbd { font-family: ui-monospace, Menlo, monospace; background: #e2e8f0;
  border-radius: 4px; padding: 0 5px; font-size: 12px; }
.score-btn { width: 100%; justify-content: flex-start !important; text-align: left; }
.score-btn-selected { outline: 3px solid #0284c7; }
.notice { background: rgba(255,237,213,0.92); border: 1px solid #fb923c;
  border-radius: 8px; padding: 8px 12px; }
.ok-notice { background: rgba(220,252,231,0.85); border: 1px solid #86efac;
  border-radius: 8px; padding: 8px 12px; }
"""


def inject_styles() -> None:
    ui.add_css(_CSS, shared=True)


def zoomable_image(
    source: str,
    *,
    zoom: int,
    overlay: str = "",
    on_click: Optional[Callable] = None,
    cursor: bool = False,
) -> ui.interactive_image:
    """An image at `zoom` % of the container width, scrolled inside a fixed frame.

    Click coordinates from `on_click` are in the image's own pixels, whatever the zoom.
    """
    with ui.element("div").classes("img-scroll"):
        image = ui.interactive_image(
            source,
            content=overlay,
            on_mouse=on_click,
            events=["click"] if on_click else [],
            cross=cursor,
        ).style(f"width: {zoom}%; display: block;")
    return image


def box_overlay(boxes: Sequence[Sequence[float]], colour: str = "#ef4444") -> str:
    return "".join(
        f'<rect x="{x0}" y="{y0}" width="{x1 - x0}" height="{y1 - y0}" fill="none" '
        f'stroke="{colour}" stroke-width="2" vector-effect="non-scaling-stroke" />'
        for x0, y0, x1, y1 in boxes
    )


def click_markers(clicks: List[dict], radius: float) -> str:
    """Numbered circles for the realism study (red by default; `color` per click)."""
    parts = []
    for n, click in enumerate(clicks, start=1):
        colour = click.get("color") or ("#f59e0b" if click.get("kind") == "odd"
                                        else "#dc2626")
        x, y = click["x"], click["y"]
        parts.append(
            f'<circle cx="{x}" cy="{y}" r="{radius}" fill="{colour}" fill-opacity="0.18" '
            f'stroke="{colour}" stroke-width="3" vector-effect="non-scaling-stroke" />'
            f'<text x="{x + radius * 1.1}" y="{y - radius * 0.9}" fill="{colour}" '
            f'font-size="{radius * 1.4}" font-weight="700">{n}</text>'
        )
    return "".join(parts)
