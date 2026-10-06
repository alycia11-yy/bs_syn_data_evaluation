#!/usr/bin/env python3
"""Add (or reset) accounts in config/users.json — one file for both websites.

    python create_user.py reviewer1 reviewer2 reviewer3 reviewer4 --role reviewer --generate
    python create_user.py R1 R2 R3 R4 --role rater --generate     # Stage 2 raters
    python create_user.py pilot1 --role pilot --generate
    python create_user.py jiayu --role admin                      # asks for the password

Roles: reviewer = Stage 1 dataset check · rater / pilot = Stage 2 realism study ·
admin = both (progress pages, audit). --generate prints each new password once; hand
them out yourself — they are stored only as bcrypt hashes.
"""
from __future__ import annotations

import argparse
import getpass
import json
import secrets
import sys

import bcrypt

from auth.permissions import ROLES, is_safe_username
from core.settings import USERS_FILE


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("usernames", nargs="+")
    parser.add_argument("--role", choices=ROLES, required=True)
    parser.add_argument("--generate", action="store_true", help="random passwords")
    args = parser.parse_args()
    for name in args.usernames:
        if not is_safe_username(name):
            sys.exit(f"'{name}': only letters, digits, '.', '_' and '-' are allowed.")
    if len(args.usernames) > 1 and not args.generate:
        sys.exit("Several accounts at once need --generate.")

    users = json.loads(USERS_FILE.read_text(encoding="utf-8")) if USERS_FILE.exists() else {}
    shown = []
    for name in args.usernames:
        if args.generate:
            password = secrets.token_urlsafe(12)
            shown.append((name, password))
        else:
            password = getpass.getpass(f"Password for {name}: ")
            if not password or password != getpass.getpass("Confirm password: "):
                sys.exit("Passwords are empty or do not match.")
        users[name] = {
            "password": bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode(),
            "role": args.role,
        }
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    USERS_FILE.write_text(json.dumps(users, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {', '.join(args.usernames)} (role {args.role}) to {USERS_FILE}")
    for name, password in shown:
        print(f"  {name:14s} {password}    (shown once)")


if __name__ == "__main__":
    main()
