"""Stage 1 — dataset check (keep / reject every tampered image).

    python review_app.py                  # http://127.0.0.1:8081

The mode follows what is on disk:

- reviewer folders in data/stage1_split/ (scripts/split_stage1.py) + config/users.json
  -> login; account reviewerN sees only folder reviewerN/, an admin sees everything;
- no reviewer folders -> you alone, no login, every image of data/set1..set4.

    python review_app.py --recheck            # 5 % blind re-check (own decisions; admin audits)
    python review_app.py --host 0.0.0.0       # listen on the network (server / Docker)
    python review_app.py --no-login           # force the no-login mode (your machine only)
    python review_app.py --rebuild-manifest   # rescan data/set1..set4 first

Images and their metadata come from data/stage1/manifest.jsonl, or — on a machine that
only has the reviewer folders — from each folder's own manifest.jsonl.
Decisions: data/stage1/decisions/decisions.jsonl (append-only) and decisions_latest.json.
Selection: data/stage1/selection.json — selected / rejected / pending file names per set.
"""
from __future__ import annotations

import argparse
import getpass
import logging
import os

from nicegui import app, ui
from starlette.requests import Request
from starlette.responses import Response

from core.datasets import load_dataset_config
from core.images import image_response
from core.manifest import build_manifest, load_manifest
from core.settings import MANIFEST_PATH, STAGE1_SPLIT_DIR, USERS_FILE, is_development
from core.ui import inject_styles
from review.assign import load_split, load_split_manifests
from review.page import render_review_page
from review.state import ADMIN, REVIEWER, ReviewData, ReviewView

logger = logging.getLogger("review_app")
STAGE1_ROLES = (REVIEWER, ADMIN)


def main() -> None:
    args = _parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = load_dataset_config()

    items = _load_items(config, args.rebuild_manifest)
    split = load_split(STAGE1_SPLIT_DIR, items)
    for warning in split.warnings:
        logger.warning(warning)
    args.users = _login_mode(args, split.active)
    data = ReviewData(items, recheck=args.recheck, recheck_fraction=args.recheck_fraction,
                      review_only_tampered=config.review_only_tampered,
                      dataset_root=config.root, split=split)
    if not args.recheck:
        data.write_selection()
    views: dict[tuple, ReviewView] = {}

    if args.users:
        _install_login(views)

    def who() -> tuple[str, str]:
        if not args.users:
            return args.reviewer, ADMIN
        return app.storage.user.get("username", ""), app.storage.user.get("role", "")

    @app.get("/dataset-image/{rel_path:path}")
    def dataset_image(rel_path: str) -> Response:
        user, role = who()
        path = data.image_path(rel_path, user, role)
        if path is None or (args.users and role not in STAGE1_ROLES):
            return Response(status_code=404)
        return image_response(path, split.root if split.active else config.root)

    @ui.page("/")
    def index() -> None:
        user, role = who()
        if args.users:
            from auth.middleware import install_idle_watchdog

            install_idle_watchdog()
            if role not in STAGE1_ROLES:
                _wrong_role(role)
                return
        # one remembered position per person and browser, so a reload returns to it
        key = (user, app.storage.browser.get("id", "local"))
        view = views.setdefault(key, ReviewView(user=user, role=role))
        render_review_page(data, view, multi_user=args.users)

    inject_styles()
    host = args.host if args.users else "127.0.0.1"
    logger.info("%d images (%s%s). Open http://%s:%d", len(items),
                "login" if args.users else f"no login, decisions saved as '{args.reviewer}'",
                f", folders of {', '.join(split.assignments)}" if split.active else "",
                host, args.port)
    ui.run(
        title="Stage 1 · Dataset check",
        host=host,
        port=args.port,
        reload=False,
        show=args.open_browser,
        storage_secret=_storage_secret(args.users),
        session_middleware_kwargs={"https_only": args.users and _https_only()},
    )


def _load_items(config, rebuild: bool) -> list:
    """Full manifest if there is one; else the reviewer folders' manifests; else scan."""
    if MANIFEST_PATH.is_file() and not rebuild:
        return load_manifest()
    if not rebuild:
        items = load_split_manifests(STAGE1_SPLIT_DIR)
        if items:
            logger.info("Using the manifests inside %s (%d images)", STAGE1_SPLIT_DIR, len(items))
            return items
    logger.info("Scanning %s ...", config.root)
    items, warnings = build_manifest(config)
    for warning in warnings:
        logger.warning(warning)
    if not items:
        raise SystemExit(f"No images found: neither {MANIFEST_PATH}, nor reviewer folders in "
                         f"{STAGE1_SPLIT_DIR}, nor images under {config.root}.")
    return items


def _login_mode(args: argparse.Namespace, split_active: bool) -> bool:
    """Login whenever there are reviewer folders, unless --no-login is given."""
    from auth.permissions import credentials_available

    if args.no_login:
        if split_active:
            logger.warning("--no-login: you see every reviewer folder, decisions are saved "
                           "as '%s'.", args.reviewer)
        return False
    if args.users or split_active:
        if not credentials_available():
            raise SystemExit(
                f"Reviewer folders found in {STAGE1_SPLIT_DIR}, but {USERS_FILE} is missing "
                "or invalid. Put the accounts file there (python create_user.py reviewer1 "
                "--role reviewer --generate), or run with --no-login.")
        return True
    return False


def _install_login(views: dict) -> None:
    """Accounts from config/users.json; Stage 1 accepts the roles `reviewer` and `admin`."""
    from auth import is_authenticated
    from auth.login_view import render_login_page
    from auth.middleware import AuthMiddleware, SecurityHeadersMiddleware
    app.add_middleware(AuthMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)

    @ui.page("/login")
    def login_page(request: Request) -> None:
        if is_authenticated():
            ui.navigate.to("/")
            return
        render_login_page(
            session_expired=request.query_params.get("reason") == "session_expired",
            title="Stage 1 · Dataset check",
        )


def _wrong_role(role: str) -> None:
    from auth import logout_session

    with ui.column().classes("main-wrap gap-2"):
        ui.label("This account is not a Stage 1 reviewer account.").classes("title")
        ui.label(f"Role '{role}'. Ask the study owner for a reviewer account.").classes("muted")
        ui.button("Sign out", on_click=lambda: (logout_session(), ui.navigate.to("/login")))


def _storage_secret(multi_user: bool) -> str:
    default = "stage1-local-only"
    secret = os.environ.get("NICEGUI_STORAGE_SECRET", default)
    if multi_user and secret == default and not is_development():
        raise SystemExit("Set NICEGUI_STORAGE_SECRET to a strong random value: "
                         "python -c 'import secrets; print(secrets.token_hex(32))'")
    return secret


def _https_only() -> bool:
    """Session cookie over HTTPS only (production default). HTTPS_ONLY=0 for a plain-HTTP
    internal network — then passwords travel unencrypted, so prefer a TLS proxy."""
    return not is_development() and os.getenv("HTTPS_ONLY", "1") != "0"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--users", action="store_true",
                        help="login even without reviewer folders (everyone sees everything)")
    parser.add_argument("--no-login", action="store_true",
                        help="no login even with reviewer folders (your own machine only)")
    parser.add_argument("--host", default="127.0.0.1",
                        help="address to listen on (with login), e.g. 0.0.0.0 on a server")
    parser.add_argument("--reviewer", default=getpass.getuser(),
                        help="no-login mode: name written into every decision")
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--recheck", action="store_true",
                        help="blind re-check of a random share of decided images")
    parser.add_argument("--recheck-fraction", type=float, default=0.05)
    parser.add_argument("--rebuild-manifest", action="store_true")
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()
    if args.users and args.no_login:
        parser.error("--users and --no-login exclude each other")
    return args


if __name__ == "__main__":
    main()
