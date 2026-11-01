"""Stage 2 — realism study website. Login with per-rater credentials (config/users.json).

Two ways to give raters their images, picked from what is on disk:

- folder mode — data/stage2_split/<rater>/ (scripts/split_stage2.py), the same layout as
  the Stage 1 reviewer folders: each account rates the images in the folder of its name,
  and every click is scored against that image's mask when it is saved;
- pool mode — data/stage2/pool/ (scripts/build_study_pool.py): one blinded pool for all.

    python create_user.py R1 R2 R3 R4 --role rater --generate
    python study_app.py                  # http://127.0.0.1:8080

Routes: /  the rater's study · /practice  training round · /admin  (role admin) two tabs:
"Reference examples" (rate the practice pages yourself -> "Save reference PDF") and
"Reviewer progress" (counts per account from data/stage2/outputs/, also when collected from
the reviewers' machines; never the answers)
"""
from __future__ import annotations

import logging
import os
import re
from collections import Counter

from nicegui import app, ui
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

from auth import credentials_available, current_role
from auth.login_view import render_login_page
from auth.middleware import (
    AuthMiddleware,
    SecurityHeadersMiddleware,
    active_username,
    install_idle_watchdog,
    same_account,
)
from core.images import image_response
from core.jsonstore import read_json
from core.settings import (
    POOL_ITEMS_PATH,
    STAGE2_DIR,
    STAGE2_SPLIT_DIR,
    STUDY_CONFIG_PATH,
    is_development,
)
from core.ui import inject_styles
from study.folders import load_folder_pools
from study.page import render_admin_page, render_practice_page, render_study_page
from study.plan import ADMIN, Pool, load_pool
from study.practice import load_practice, save_reference
from study.reference import build_reference_pdf, saved_answers

logger = logging.getLogger("study_app")

# Must be module level: NiceGUI builds its middleware stack before ui.run().
app.add_middleware(AuthMiddleware)
app.add_middleware(SecurityHeadersMiddleware)

FOLDER_POOLS, FOLDER_WARNINGS = load_folder_pools(STAGE2_SPLIT_DIR)
POOL = None if FOLDER_POOLS else load_pool()
STUDY_CONFIG = read_json(STUDY_CONFIG_PATH, {}) or {}
PRACTICE_DIR = STAGE2_DIR / "practice"
PRACTICE = load_practice(PRACTICE_DIR, FOLDER_POOLS, int(STUDY_CONFIG.get("practice_size", 1)),
                         int(STUDY_CONFIG.get("seed", 0)))
_STUDY_ID = re.compile(r"S[0-9a-f]{4,12}")
inject_styles()


@app.get("/healthz")
def healthz() -> PlainTextResponse:
    ok = (POOL is not None or bool(FOLDER_POOLS)) and credentials_available()
    return PlainTextResponse("OK" if ok else "Service unavailable", status_code=200 if ok else 503)


@app.get("/study-image/{study_id}")
def study_image(study_id: str) -> Response:
    # Protected by AuthMiddleware; the URL carries only the study id, never the file name,
    # and a rater can load only the images of their own pool.
    pool = pool_for(app.storage.user.get("username", ""))
    if pool is None or not _STUDY_ID.fullmatch(study_id) or study_id not in pool.items:
        return Response(status_code=404)
    path = pool.image_path(study_id)
    return image_response(path, path.parent)


@app.get("/practice-image/{index}")
def practice_image(index: int) -> Response:
    # by position in the practice list, so no file name reaches the browser
    if not 0 <= index < len(PRACTICE):
        return Response(status_code=404)
    path = PRACTICE[index].path
    return image_response(path, path.parent)


@ui.page("/login")
def login_page(request: Request) -> None:
    # Always show the form: on a shared computer the next person must be able to sign in with their
    # own account; before, an active session sent everyone straight to the last account's pages.
    render_login_page(
        session_expired=request.query_params.get("reason") == "session_expired",
        redirect_to=_safe_redirect(request.query_params.get("redirect_to", "/")),
        signed_in_as=active_username(),
    )


def pool_for(username: str) -> Pool | None:
    if FOLDER_POOLS:
        return FOLDER_POOLS.get(username)
    return POOL


@ui.page("/")
def study_page() -> None:
    install_idle_watchdog()
    if current_role() == ADMIN:
        ui.navigate.to("/admin")
        return
    pool = pool_for(app.storage.user.get("username", ""))
    if pool is None:
        _no_pool()
        return
    render_study_page(pool, has_practice=bool(PRACTICE))


@ui.page("/practice")
def practice_page(start: int = 1) -> None:
    install_idle_watchdog()
    if not PRACTICE:
        ui.label("No practice images: neither data/stage2/practice/practice.json nor rater "
                 "folders to pick from.").classes("main-wrap")
        return
    user = app.storage.user.get("username", "")

    def save(index, item, answer, result, user=user):
        if same_account(user):
            save_reference(user, index, item, answer, result)

    # the admin's practice answers = the reference examples (saved, then "Save reference PDF");
    # raters' practice answers are not saved
    admin = current_role() == ADMIN
    on_answer = save if admin else None
    on_save = (lambda user=user: build_reference_pdf(user, PRACTICE_DIR)) if admin else None
    saved = (lambda user=user: saved_answers(user, PRACTICE_DIR)) if admin else None
    render_practice_page(PRACTICE, lambda i: f"/practice-image/{i}",
                         int(STUDY_CONFIG.get("max_clicks", 3)),
                         list(STUDY_CONFIG.get("zoom_steps", [100, 125, 150, 175, 200])),
                         float(STUDY_CONFIG.get("click_margin_fraction", 0.5)), on_answer, on_save, saved,
                         start=(start - 1) if admin else 0)


@ui.page("/admin")
def admin_page() -> None:
    install_idle_watchdog()
    if current_role() != ADMIN:
        ui.navigate.to("/")
        return
    user = app.storage.user.get("username", "")
    render_admin_page(pool_for, sorted(FOLDER_POOLS), PRACTICE,
                      saved=lambda: saved_answers(user, PRACTICE_DIR),
                      on_save=lambda: build_reference_pdf(user, PRACTICE_DIR))


def _no_pool() -> None:
    from auth import logout_session

    with ui.column().classes("main-wrap gap-2"):
        if FOLDER_POOLS:
            name = app.storage.user.get("username", "")
            ui.label(f"There are no images for {name}.").classes("title")
            ui.markdown(f"Expected a folder `{name}/` in `{STAGE2_SPLIT_DIR}`.")
        else:
            ui.label("The study has not been set up yet.").classes("title")
            ui.markdown(f"Expected rater folders in `{STAGE2_SPLIT_DIR}` "
                        f"(`scripts/split_stage2.py`) or `{POOL_ITEMS_PATH}` "
                        "(`scripts/build_study_pool.py`).")
        ui.button("Sign out", on_click=lambda: (logout_session(), ui.navigate.to("/login")))


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
    for warning in FOLDER_WARNINGS:
        logger.warning(warning)
    if FOLDER_POOLS:
        for rater, pool in FOLDER_POOLS.items():
            kinds = Counter(i["kind"] for i in pool.items.values())
            logger.info("Folder mode: %s -> %d images (+%d repeats, %d controls) from %s",
                        rater, kinds["main"], kinds["repeat"], kinds["control"], pool.folder)
    elif POOL is None:
        logger.warning("No rater folders in %s and no pool at %s", STAGE2_SPLIT_DIR,
                       POOL_ITEMS_PATH)
    else:
        logger.info("Pool %s: %d entries", POOL.version, len(POOL.items))
    source = "practice.json" if (PRACTICE_DIR / "practice.json").is_file() else \
        "random pick from the rater folders (test)"
    logger.info("Practice: %d image(s) from %s", len(PRACTICE), source)
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
