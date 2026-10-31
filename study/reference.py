"""The reference PDF for the raters: the admin's own answers on the Practice pages.

The admin (jiayu) signs in, opens Practice and rates every page like a rater would; each
answer is saved ($STAGE2_DIR/reference/<admin>/, study/practice.py). The "Save reference
PDF" button at the end of Practice (or `python scripts/reference_pdf.py`) then writes

    results/stage2_reference/reference_examples_<admin>_<YYYY-MM-DD>.pdf

- a cover page: what one answer measures (study/outcome.py), the outcomes, the scale, and a
  table of my answers (score, clicks on / off the edit, outcome, edit visibility);
- one page per example: the original page (before the edit) and the edited page as the
  raters see it, with my clicks (numbered circles: green = on the edit, orange = not on
  an edit) and the real edit (red box); close-ups of the same spot before and after;
  my answer with its measures, and a note on what was edited. A genuine page has no "before".
  There is no expected score: my answer is the reference.

It also writes $STAGE2_DIR/practice/reference.json (my answers with their measures), which the
Practice page shows to raters after each page.

Only synthetic pages from documents that are not in the study pool (scripts/make_training.py
checks this), so the PDF can be given to every rater.
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont

from core.jsonstore import read_json, write_json_atomic
from core.settings import RESULTS_DIR, STAGE2_DIR
from study.practice import reference_store
from study.outcome import OUTCOMES, page_measures
from study.ratings import SCALE

PAGE = (1240, 1754)                      # A4 portrait at 150 dpi
MARGIN = 70
INNER = PAGE[0] - 2 * MARGIN
RED, GREEN, ORANGE = (210, 30, 30), (20, 150, 60), (235, 130, 0)
INK, MUTE, LINE = (25, 25, 25), (105, 105, 105), (200, 200, 200)
LABEL = {s: label for s, label, _ in SCALE}
_FONTS = ["/System/Library/Fonts/Supplemental/Arial.ttf", "/Library/Fonts/Arial.ttf",
          "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "C:/Windows/Fonts/arial.ttf"]
_BOLD = {"Arial.ttf": "Arial Bold.ttf", "DejaVuSans.ttf": "DejaVuSans-Bold.ttf",
         "arial.ttf": "arialbd.ttf"}
_font_cache: Dict[Tuple[int, bool], ImageFont.ImageFont] = {}


def reference_pdf_path(user: str, day: Optional[str] = None) -> Path:
    return RESULTS_DIR / "stage2_reference" / f"reference_examples_{user}_{day or _dt.date.today().isoformat()}.pdf"


def saved_answers(user: str, practice_dir: Optional[Path] = None) -> Dict[str, dict]:
    """The admin's saved answers by practice image (only answers given on the current pages)."""
    return _answers(user, _items(practice_dir or STAGE2_DIR / "practice"))


def missing_pages(user: str, practice_dir: Optional[Path] = None) -> List[int]:
    """Numbers (1-based) of the practice pages the admin has not rated yet."""
    items = _items(practice_dir or STAGE2_DIR / "practice")
    answers = _answers(user, items)
    return [n for n, it in enumerate(items, 1) if it["image"] not in answers]


def build_reference_pdf(user: str, practice_dir: Optional[Path] = None, out: Optional[Path] = None,
                        allow_missing: bool = False) -> Tuple[Optional[Path], List[int]]:
    """Write the PDF. Returns (path, missing page numbers); path is None when pages are missing
    and allow_missing is False."""
    practice_dir = practice_dir or STAGE2_DIR / "practice"
    items = _items(practice_dir)
    if not items:
        raise FileNotFoundError(f"No {practice_dir}/practice.json: run scripts/make_training.py first.")
    answers = _answers(user, items)
    missing = [n for n, it in enumerate(items, 1) if it["image"] not in answers]
    if missing and not allow_missing:
        return None, missing
    saved = max((a.get("timestamp", "") for a in answers.values()), default="")[:10] or None
    pages = [_cover(items, answers, user, saved)]
    pages += [_example(n, len(items), it, answers.get(it["image"]), practice_dir, user)
              for n, it in enumerate(items, 1)]
    out = Path(out) if out else reference_pdf_path(user)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp.pdf")
    pages[0].save(tmp, save_all=True, append_images=pages[1:], resolution=150)
    tmp.replace(out)
    write_json_atomic(practice_dir / "reference.json", {
        "rater": user, "saved_at": saved, "pdf": out.name,
        "answers": {img: {"source_id": a.get("source_id"), "label": a.get("label"), "score": a.get("score"),
                          "no_traces": a.get("no_traces"),
                          "clicks": [{"x": c["x"], "y": c["y"], "hit": bool(c.get("hit"))}
                                     for c in a.get("clicks") or []],
                          "measures": _measures({"label": a.get("label")}, a)}
                    for img, a in answers.items()}})
    return out, missing


# --------------------------------------------------------------------------- #


def _items(practice_dir: Path) -> List[dict]:
    return (read_json(practice_dir / "practice.json") or {}).get("items") or []


def _answers(user: str, items: List[dict]) -> Dict[str, dict]:
    """Saved answers by practice image, only where they were given on the same source page (an answer
    saved before the practice pages were rebuilt in another order does not count)."""
    source = {it["image"]: it.get("source_id") for it in items}
    return {img: a for img, a in reference_store(user).latest().items()
            if img in source and a.get("source_id") == source[img]}


def _font(size: int, bold: bool = False):
    key = (size, bold)
    if key not in _font_cache:
        names = []
        for f in _FONTS:
            if bold:
                for a, b in _BOLD.items():
                    if f.endswith(a):
                        names.append(f[: -len(a)] + b)
            names.append(f)
        for f in names:
            try:
                _font_cache[key] = ImageFont.truetype(f, size)
                break
            except OSError:
                continue
        else:
            try:
                _font_cache[key] = ImageFont.load_default(size=size)
            except TypeError:
                _font_cache[key] = ImageFont.load_default()
    return _font_cache[key]


def _text(d: ImageDraw.ImageDraw, x: int, y: int, text: str, size: int = 24, bold: bool = False,
          fill=INK, width: int = INNER, gap: int = 7) -> int:
    """Draw word-wrapped text (by pixel width); returns the y below it."""
    f = _font(size, bold)
    for para in text.split("\n"):
        line = ""
        for word in para.split(" "):
            trial = f"{line} {word}".strip()
            if line and d.textlength(trial, font=f) > width:
                d.text((x, y), line, fill=fill, font=f)
                y += size + gap
                line = word
            else:
                line = trial
        d.text((x, y), line, fill=fill, font=f)
        y += size + gap
    return y


def _fit(im: Image.Image, w: int, h: int) -> Image.Image:
    s = min(w / im.width, h / im.height)
    return im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.Resampling.LANCZOS)


def _paste(canvas: Image.Image, im: Image.Image, x: int, y: int) -> None:
    canvas.paste(im, (x, y))
    ImageDraw.Draw(canvas).rectangle([x - 1, y - 1, x + im.width, y + im.height], outline=LINE)


def _mark(im: Image.Image, boxes: Sequence, clicks: Sequence) -> Image.Image:
    im = im.convert("RGB").copy()
    d = ImageDraw.Draw(im)
    w = max(3, im.width // 450)
    for b in boxes or []:
        d.rectangle([b[0] - w, b[1] - w, b[2] + w, b[3] + w], outline=RED, width=w)
    r = max(14, im.width // 90)
    for n, c in enumerate(clicks or [], 1):
        col = GREEN if c.get("hit") else ORANGE
        x, y = c["x"], c["y"]
        d.ellipse([x - r, y - r, x + r, y + r], outline=col, width=w)
        d.text((x + r + 4, y - r), str(n), fill=col, font=_font(int(r * 1.6), True))
    return im


def _focus(boxes: Sequence, clicks: Sequence, size: Tuple[int, int]) -> Optional[Tuple[int, int, int, int]]:
    """The close-up region: around the edit(s), else around the first click."""
    W, H = size
    if boxes:
        x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
        x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
        if (x1 - x0) * (y1 - y0) > 0.25 * W * H:            # edits far apart: the first one
            x0, y0, x1, y1 = boxes[0]
        tall = min(b[3] - b[1] for b in boxes)
    elif clicks:
        x0 = x1 = clicks[0]["x"]
        y0 = y1 = clicks[0]["y"]
        tall = max(20, H // 40)
    else:
        return None
    pad = max(50, 2.5 * tall)
    return (int(max(0, x0 - 5 * pad)), int(max(0, y0 - pad)), int(min(W, x1 + 5 * pad)), int(min(H, y1 + pad)))


def _measures(it: dict, ans: Optional[dict]) -> Optional[dict]:
    if ans is None:
        return None
    return ans.get("measures") or page_measures(it["label"], ans.get("score", 5), ans.get("clicks") or [],
                                                ans.get("regions_found"), ans.get("regions_total"))


def _answer_lines(it: dict, ans: Optional[dict]) -> List[Tuple[str, bool]]:
    """My answer as (text, bold) lines: the score, the clicks, the outcome and the edit visibility."""
    m = _measures(it, ans)
    if m is None:
        return [("Not rated yet", True)]
    s = m["score"]
    lines = [(f"Realism score (whole page): {s} ({LABEL.get(s, '')})" + ("  —  pressed \"No edit on this page\""
                                                          if ans.get("no_traces") else ""), True)]
    if m["clicks"]:
        where = (f"{m['hits']} on the edit, {m['misses']} elsewhere" if it["label"] == "tampered"
                 else f"all {m['clicks']} on an unedited page")
        if it["label"] == "tampered" and m["clicks"] > 1 and m.get("first_click_hit") is not None:
            where += f"; first click {'on' if m['first_click_hit'] else 'not on'} the edit"
        lines.append((f"Clicks: {m['clicks']} ({where})" + (f"  ·  edited places found: {m['edits_found']} of "
                      f"{m['edits_total']}" if it["label"] == "tampered" else "")
                      + f"  ·  false-alarm clicks: {m['false_alarm_clicks']}", False))
    else:
        lines.append(("Clicks: none", False))
    label, meaning = OUTCOMES[m["outcome"]]
    lines.append((f"Outcome: {label} — {meaning}", False))
    if m["edit_visibility"] is not None:
        lines.append((f"Edit visibility: {m['edit_visibility']} (the score credited to the edit itself: the score "
                      "if a click is on the edit, else 5)", False))
    return lines


def _short(it: dict) -> str:
    return it.get("short") or ("Edited page" if it["label"] == "tampered" else "Genuine page")


def _cover(items: List[dict], answers: Dict[str, dict], user: str, saved: Optional[str]) -> Image.Image:
    c = Image.new("RGB", PAGE, "white")
    d = ImageDraw.Draw(c)
    y = _text(d, MARGIN, MARGIN, "Realism study · reference examples", 44, True)
    y = _text(d, MARGIN, y + 4, f"{len(items)} pages rated by {user} on the study website"
              + (f" (saved {saved})" if saved else "") + ". All are synthetic statements from documents not in "
              "the study. These are one careful reader's answers, not the only right ones: each page is "
              "described by several measures, not by one score.", 21, fill=MUTE)
    y = _text(d, MARGIN, y + 14, "What one answer measures", 26, True)
    for head, text in [
        ("Realism score", "ONE score for the whole page, 1–5 (5 = \"No edit on this page\"), not one per place; "
                          "with several places marked it follows the strongest sign of editing. Kept as given."),
        ("Clicks", "up to 3, the place you are most sure about first; each checked against the real edit: on the "
                   "edit (hit) or elsewhere (miss). Kept as given."),
        ("Edit found", "edited page with at least one click on the edit (and how many edited places got one)."),
        ("False-alarm clicks", "clicks not on an edit; on a genuine page every click is one."),
        ("Outcome", "the case, from score and clicks together (below)."),
        ("Edit visibility", "edited pages: the score if a click is on the edit, otherwise 5. A low score "
                            "given for another reason does not count against the edit."),
    ]:
        _text(d, MARGIN + 20, y, head, 19, True)
        y = _text(d, MARGIN + 250, y, text, 19, width=INNER - 250, gap=5)
    y = _text(d, MARGIN, y + 10, "Outcomes", 26, True)
    for code in OUTCOMES:
        label, meaning = OUTCOMES[code]
        _text(d, MARGIN + 20, y, label, 19, True)
        y = _text(d, MARGIN + 330, y, meaning, 19, width=INNER - 330, gap=5)
    y = _text(d, MARGIN, y + 10, "Scale", 26, True)
    y = _text(d, MARGIN + 20, y, "  ·  ".join(f"{s} {lab}" for s, lab, _ in SCALE), 19)
    y = _text(d, MARGIN, y + 10, "My answers", 26, True)
    cols = [MARGIN, MARGIN + 44, MARGIN + 390, MARGIN + 470, MARGIN + 640, MARGIN + 1010]
    for x, h in zip(cols, ["#", "Page", "Score", "Clicks (on / off)", "Outcome", "Edit vis."]):
        d.text((x, y), h, fill=MUTE, font=_font(18, True))
    y += 26
    d.line([MARGIN, y, MARGIN + INNER, y], fill=LINE, width=2)
    y += 6
    for n, it in enumerate(items, 1):
        m = _measures(it, answers.get(it["image"]))
        row = [str(n), _short(it)] + (["—", "—", "Not rated yet", "—"] if m is None else [
            str(m["score"]), f"{m['hits']} / {m['misses']}" if m["clicks"] else "none",
            OUTCOMES[m["outcome"]][0], "—" if m["edit_visibility"] is None else str(m["edit_visibility"])])
        for x, v in zip(cols, row):
            d.text((x, y), v, fill=INK if m else RED, font=_font(18))
        y += 27
    _text(d, MARGIN, max(y + 8, PAGE[1] - MARGIN - 30), "Pictures: red box = the real edit; circles = my clicks "
          "in order (green = on the edit, orange = not on an edit).", 18, fill=MUTE)
    return c


def _example(n: int, total: int, it: dict, ans: Optional[dict], practice_dir: Path, user: str) -> Image.Image:
    c = Image.new("RGB", PAGE, "white")
    d = ImageDraw.Draw(c)
    n_edit = len(it.get("boxes") or [])
    kind = (f"edited page ({n_edit} edited place{'s' if n_edit != 1 else ''})" if it["label"] == "tampered"
            else "genuine page")
    y = _text(d, MARGIN, MARGIN, f"Example {n} of {total} · {kind}", 36, True)
    y = _text(d, MARGIN, y, it.get("about") or _short(it), 21, fill=MUTE)
    y = _text(d, MARGIN, y, f"Image {it.get('source_id', '')}  ·  practice page {n}  ·  rated by {user}", 19,
              fill=MUTE)
    y += 10
    after_src = Image.open(practice_dir / it["image"]).convert("RGB")
    clicks = (ans or {}).get("clicks") or []
    boxes = it.get("boxes") or []
    after = _mark(after_src, boxes, clicks)
    before = None
    if it.get("original") and (practice_dir / it["original"]).is_file():
        before = Image.open(practice_dir / it["original"]).convert("RGB")
        if before.size != after_src.size:                    # resized capture: same page, other size
            before = before.resize(after_src.size, Image.Resampling.LANCZOS)
        before = _mark(before, boxes, [])
    focus = _focus(boxes, clicks, after_src.size)
    text_h = 400
    close_h = 0 if focus is None else (200 if before is not None else 240)
    n_close = 0 if focus is None else (2 if before is not None else 1)
    free = PAGE[1] - MARGIN - y - text_h - n_close * (close_h + 40)

    if before is None:
        full = _fit(after, INNER, free - 40)
        y = _text(d, MARGIN, y, "The page as the raters see it (genuine, nothing was edited)"
                  if it["label"] != "tampered" else "The page as the raters see it", 21, True)
        _paste(c, full, MARGIN, y)
        y += full.height + 18
    else:
        # side by side for tall pages, one above the other for wide ones: whichever is larger
        side = _fit(after, (INNER - 24) // 2, free - 40)
        stack = _fit(after, INNER, (free - 80) // 2)
        if side.width * side.height >= stack.width * stack.height:
            b = _fit(before, side.width, side.height)
            _text(d, MARGIN, y, "Before the edit (original)", 21, True)
            _text(d, MARGIN + side.width + 24, y, "After the edit (what the raters see)", 21, True)
            y += 32
            _paste(c, b, MARGIN, y)
            _paste(c, side, MARGIN + side.width + 24, y)
            y += side.height + 18
        else:
            b = _fit(before, stack.width, stack.height)
            y = _text(d, MARGIN, y, "Before the edit (original)", 21, True)
            _paste(c, b, MARGIN, y)
            y += b.height + 12
            y = _text(d, MARGIN, y, "After the edit (what the raters see)", 21, True)
            _paste(c, stack, MARGIN, y)
            y += stack.height + 18

    if focus is not None:
        # close-ups take what is left above the text (wide pages leave more room)
        close_h = int(min(380, max(close_h, (PAGE[1] - MARGIN - y - text_h) / n_close - 40)))
        if before is not None:
            for title, im in (("Close-up, before", before), ("Close-up, after", after)):
                y = _text(d, MARGIN, y, title, 20, True, fill=MUTE)
                crop = _fit(im.crop(focus), INNER, close_h)
                _paste(c, crop, MARGIN, y)
                y += crop.height + 12
        else:
            y = _text(d, MARGIN, y, "Close-up around my first click", 20, True, fill=MUTE)
            crop = _fit(after.crop(focus), INNER, close_h)
            _paste(c, crop, MARGIN, y)
            y += crop.height + 12
        y += 6

    y = _text(d, MARGIN, y, "My answer", 25, True, fill=INK if ans else RED)
    for line, bold in _answer_lines(it, ans):
        y = _text(d, MARGIN + 20, y, line, 21, bold, fill=INK if ans else RED, width=INNER - 20, gap=5)
    if ans is not None:
        y = _text(d, MARGIN + 20, y, f"Time on the page {ans.get('time_ms', 0) / 1000:.0f} s  ·  largest zoom "
                  f"{ans.get('max_zoom', 100)} %", 18, fill=MUTE)
    note = it.get("note", "")
    if note.startswith("Expected score") and ":" in note:                 # notes written before 31 Oct
        note = note.split(":", 1)[1].strip()
    y = _text(d, MARGIN, y + 8, "Note: " + note, 22)
    _text(d, MARGIN, max(y + 8, PAGE[1] - MARGIN - 30), "Red box = the real edit.  Circles = my clicks in "
          "order (green = on the edit, orange = not on an edit).", 18, fill=MUTE)
    return c
