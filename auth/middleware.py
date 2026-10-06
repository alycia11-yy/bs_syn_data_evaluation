"""Login redirect, idle timeout, CSRF origin check and security headers (from the memo tool)."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from nicegui import app, ui
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from core.settings import is_development

logger = logging.getLogger(__name__)

_PUBLIC_PATHS = frozenset({"/healthz", "/login"})
_PUBLIC_PREFIXES = ("/_nicegui", "/static")
SESSION_IDLE_TIMEOUT = timedelta(minutes=30)
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if request.method not in _SAFE_METHODS and not _origin_ok(request):
            return Response(status_code=403)
        if path in _PUBLIC_PATHS or path.startswith(_PUBLIC_PREFIXES):
            return await call_next(request)
        if not app.storage.user.get("authenticated", False):
            return RedirectResponse(f"/login?redirect_to={path}")
        if _expire_if_idle():
            return RedirectResponse("/login?reason=session_expired")
        _touch()
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        if not is_development():
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


def require_active_session() -> bool:
    """Call at the top of every event handler that writes data."""
    if not app.storage.user.get("authenticated", False):
        ui.navigate.to("/login")
        return False
    if _expire_if_idle():
        ui.navigate.to("/login?reason=session_expired")
        return False
    _touch()
    return True


def install_idle_watchdog() -> None:
    """Refresh activity on user input; send an idle tab back to the login page."""
    for event in ("click", "keydown", "pointerdown", "wheel"):
        ui.on(event, _touch, args=[], throttle=30.0, trailing_events=False)

    def check() -> None:
        if _expire_if_idle():
            ui.navigate.to("/login?reason=session_expired")

    ui.timer(60.0, check, immediate=False)


def _origin_ok(request: Request) -> bool:
    origin, host = request.headers.get("origin"), request.headers.get("host", "")
    if origin is None or not host:
        return True
    ok = origin in {f"http://{host}", f"https://{host}"}
    if not ok:
        logger.warning("CSRF: blocked origin=%r host=%r", origin, host)
    return ok


def _expire_if_idle() -> bool:
    stamp = app.storage.user.get("last_active")
    try:
        last = datetime.fromisoformat(stamp) if stamp else None
    except (TypeError, ValueError):
        last = None
    now = datetime.now(timezone.utc)
    if last is None or last.tzinfo is None or now - last > SESSION_IDLE_TIMEOUT:
        if app.storage.user.get("authenticated"):
            logger.info("Idle timeout for %s", app.storage.user.get("username"))
        app.storage.user.clear()
        return True
    return False


def _touch() -> None:
    app.storage.user["last_active"] = datetime.now(timezone.utc).isoformat()
