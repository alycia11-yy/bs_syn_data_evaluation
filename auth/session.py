from __future__ import annotations

import logging
from datetime import datetime, timezone

from nicegui import app

from auth.permissions import get_user_role, is_safe_username

logger = logging.getLogger(__name__)


def is_authenticated() -> bool:
    return bool(app.storage.user.get("authenticated"))


def current_username() -> str | None:
    return app.storage.user.get("username")


def current_role() -> str | None:
    return app.storage.user.get("role")


def login_session(username: str) -> None:
    if not is_safe_username(username):
        raise ValueError("Unsafe username.")
    role = get_user_role(username)
    app.storage.user.update({
        "authenticated": True,
        "username": username,
        "role": role,
        "last_active": datetime.now(timezone.utc).isoformat(),
    })
    logger.info("Session established for user=%s role=%s", username, role)


def logout_session() -> None:
    logger.info("User signed out: %s", current_username())
    app.storage.user.clear()
