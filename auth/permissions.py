"""Credentials for the realism study: bcrypt hashes in config/users.json (gitignored).

    {"R1": {"password": "$2b$12$...", "role": "rater"},
     "pilot1": {"password": "...", "role": "pilot"},
     "helper1": {"password": "...", "role": "reviewer"},
     "jiayu": {"password": "...", "role": "admin"}}
"""
from __future__ import annotations

import json
import logging
import re

import bcrypt

from core.settings import USERS_FILE

logger = logging.getLogger(__name__)

# rater / pilot: Stage 2 realism study · reviewer: Stage 1 dataset check · admin: both
ROLES = ("rater", "pilot", "reviewer", "admin")

# Constant-time answer for unknown users, so timing does not reveal valid usernames.
_DUMMY_HASH = b"$2b$12$6ZsnG/i92Ka6vfuIInowkeOzrUuEydb.V6cg4TZJV.lUa6oPwk3I2"
_BCRYPT_HASH = re.compile(r"\$2[aby]\$(?:0[4-9]|[12][0-9]|3[01])\$[./A-Za-z0-9]{53}")
_SAFE_USERNAME = re.compile(r"(?!\.?\.?$)[A-Za-z0-9._-]+")


def is_safe_username(username: object) -> bool:
    return isinstance(username, str) and bool(_SAFE_USERNAME.fullmatch(username))


def load_users() -> dict:
    if not USERS_FILE.exists():
        logger.warning("%s not found — all logins will fail.", USERS_FILE)
        return {}
    try:
        users = json.loads(USERS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.exception("Failed to read %s", USERS_FILE)
        return {}
    return users if isinstance(users, dict) else {}


def credentials_available() -> bool:
    users = load_users()
    return bool(users) and all(
        is_safe_username(name) and isinstance(user, dict)
        and isinstance(user.get("password"), str) and bool(_BCRYPT_HASH.fullmatch(user["password"]))
        for name, user in users.items()
    )


def verify_credentials(username: str, password: str) -> bool:
    users = load_users()
    user = users.get(username) if isinstance(username, str) else None
    password_bytes = password.encode("utf-8") if isinstance(password, str) else b""
    stored = user.get("password") if isinstance(user, dict) else None
    if not is_safe_username(username) or not isinstance(stored, str) \
            or not _BCRYPT_HASH.fullmatch(stored):
        _dummy_check(password_bytes)
        logger.warning("Login failed — unknown or malformed user: %r", username)
        return False
    try:
        ok = bcrypt.checkpw(password_bytes[:72], stored.encode("utf-8"))
    except ValueError:
        logger.exception("Invalid bcrypt hash for user=%s", username)
        return False
    logger.info("Login %s for user %s", "successful" if ok else "failed", username)
    return ok


def get_user_role(username: str) -> str:
    user = load_users().get(username)
    role = user.get("role") if isinstance(user, dict) else None
    return role if role in ROLES else "rater"


def _dummy_check(password: bytes) -> None:
    bcrypt.checkpw(password[:72], _DUMMY_HASH)
