"""Stage 2 — realism study website. Login with per-rater credentials (config/users.json).

    python scripts/build_study_pool.py   # once, after Stage 1 is complete
    python create_user.py --role rater   # one account per rater (R1..R4)
    python study_app.py                  # http://127.0.0.1:8080

Routes: /  the rater's study · /practice  training round · /admin  progress (role admin)
"""
from __future__ import annotations

import logging
import os
import re

from nicegui import app, ui
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

from auth import credentials_available, current_role, is_authenticated
from auth.login_view import render_login_page
from auth.middleware import AuthMiddleware, SecurityHeadersMiddleware, install_idle_watchdog
from core.images import image_response
from core.jsonstore import read_json
from core.settings import POOL_IMAGES_DIR, POOL_ITEMS_PATH, STAGE2_DIR, is_development
from core.ui import inject_styles
from study.page import render_admin_page, render_practice_page, render_study_page
from study.plan import ADMIN, load_pool

logger = logging.getLogger("study_app")

# Must be module level: NiceGUI builds its middleware stack before ui.run().
app.add_middleware(AuthMiddleware)
app.add_middleware(SecurityHeadersMiddleware)

POOL = load_pool()
PRACTICE_DIR = STAGE2_DIR / "practice"
PRACTICE = read_json(PRACTICE_DIR / "practice.json", {"items": []}) or {"items": []}
_STUDY_ID = re.compile(r"S\d{4,6}")
_PRACTICE_FILE = re.compile(r"[A-Za-z0-9._-]+")
inject_styles()


@app.get("/healthz")
def healthz() -> PlainTextResponse:
    ok = POOL is not None and credentials_available()
    return PlainTextResponse("OK" if ok else "Service unavailable", status_code=200 if ok else 503)


@app.get("/study-image/{study_id}")
def study_image(study_id: str) -> Response:
    # Protected by AuthMiddleware; the URL carries only the study id, never the file name.
    if POOL is None or not _STUDY_ID.fullmatch(study_id) or study_id not in POOL.items:
        return Response(status_code=404)
    return image_response(POOL.image_path(study_id), POOL_IMAGES_DIR)


@app.get("/practice-image/{name}")
def practice_image(name: str) -> Response:
    if not _PRACTICE_FILE.fullmatch(name):
        return Response(status_code=404)
    return image_response(PRACTICE_DIR / name, PRACTICE_DIR)


@ui.page("/login")
def login_page(request: Request) -> None:
    if is_authenticated():
        ui.navigate.to("/")
        return
    render_login_page(
        session_expired=request.query_params.get("reason") == "session_expired",
        redirect_to=_safe_redirect(request.query_params.get("redirect_to", "/")),
    )


@ui.page("/")
def study_page() -> None:
    install_idle_watchdog()
    if POOL is None:
        _no_pool()
        return
    render_study_page(POOL)


@ui.page("/practice")
def practice_page() -> None:
    install_idle_watchdog()
    if not PRACTICE["items"]:
        ui.label("No practice images configured (data/stage2/practice/practice.json).")
        return
    render_practice_page(PRACTICE, lambda name: f"/practice-image/{name}",
                         POOL.max_clicks if POOL else 3,
                         POOL.zoom_steps if POOL else [100, 125, 150, 175, 200])


@ui.page("/admin")
def admin_page() -> None:
    install_idle_watchdog()
    if current_role() != ADMIN:
        ui.navigate.to("/")
        return
    if POOL is None:
        _no_pool()
        return
    render_admin_page(POOL)


def _no_pool() -> None:
    with ui.column().classes("main-wrap"):
        ui.label("The study has not been set up yet.").classes("title")
        ui.markdown(f"Expected `{POOL_ITEMS_PATH}`. Build it with "
                    "`python scripts/build_study_pool.py`.")


def _safe_redirect(value: str | None) -> str:
    if not value or not value.startswith("/") or value.startswith("//") or any(
            c in value for c in "\\\r\n"):
        return "/"
    return value


def _storage_secret() -> str:
    default = "realism-study-dev-secret"
    secret = os.environ.get("NICEGUI_STORAGE_SECRET", default)
    if secret == default and not is_development():
        raise RuntimeError("Set NICEGUI_STORAGE_SECRET to a strong random value in production: "
                           "python -c 'import secrets; print(secrets.token_hex(32))'")
    return secret


def _https_only() -> bool:
    """Session cookie over HTTPS only (production default). HTTPS_ONLY=0 for a plain-HTTP
    internal network — then passwords travel unencrypted, so prefer a TLS proxy."""
    return not is_development() and os.getenv("HTTPS_ONLY", "1") != "0"


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if POOL is None:
        logger.warning("No pool at %s — run scripts/build_study_pool.py", POOL_ITEMS_PATH)
    else:
        logger.info("Pool %s: %d entries", POOL.version, len(POOL.items))
    ui.run(
        title="Document Realism Study",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8080")),
        reload=False,
        show=False,
        storage_secret=_storage_secret(),
        session_middleware_kwargs={"https_only": _https_only()},
    )


if __name__ in {"__main__", "__mp_main__"}:
    main()
