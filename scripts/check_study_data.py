#!/usr/bin/env python3
"""Preflight for the Stage 2 server folder — Python 3 standard library only, so it runs
on the server without installing anything.

    python3 scripts/check_study_data.py study_data
    python3 scripts/check_study_data.py study_data --no-checksums     # quicker

Checks: accounts (bcrypt hashes, roles, an admin, a folder per rater), every rater folder
(manifest, images and masks present, checksums.sha256), practice.json, PACKAGE.json, and
that stage2/plans and stage2/outputs are writable by the container user (UID 10001).
Exit code 1 if anything blocks the start.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

CONTAINER_UID = 10001
ERRORS, NOTES = [], []


def main() -> None:
    args = _parse_args()
    root: Path = args.root
    if not root.is_dir():
        sys.exit(f"{root} is not a folder")

    users = _check_users(root / "config" / "users.json")
    if not (root / "config" / "study.json").is_file():
        ERRORS.append("config/study.json missing")
    raters = _check_folders(root / "stage2_split", args.checksums)
    for user, entry in users.items():
        if entry.get("role") in ("rater", "pilot") and user not in raters:
            NOTES.append(f"account {user} has no folder stage2_split/{user}/ (sees 'no images')")
    for rater in raters:
        if rater not in users:
            ERRORS.append(f"stage2_split/{rater}/ has no account in users.json")
    _check_practice(root / "stage2" / "practice")
    _check_readable(root)
    for name in ("plans", "outputs"):
        _check_writable(root / "stage2" / name)
    if (root / "stage2" / "key").exists() or (root / "stage2" / "pool").exists():
        NOTES.append("stage2/key or stage2/pool found — not needed on the server")
    if args.checksums:
        _check_package(root)

    for note in NOTES:
        print(f"NOTE  {note}")
    for error in ERRORS:
        print(f"ERROR {error}")
    print("OK — ready for `docker compose up -d`" if not ERRORS else
          f"{len(ERRORS)} problem(s) — fix them before starting")
    sys.exit(1 if ERRORS else 0)


def _check_users(path: Path) -> dict:
    if not path.is_file():
        ERRORS.append("config/users.json missing")
        return {}
    try:
        users = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        ERRORS.append(f"config/users.json is not valid JSON: {exc}")
        return {}
    for user, entry in users.items():
        if not str(entry.get("password", "")).startswith("$2"):
            ERRORS.append(f"user {user}: password is not a bcrypt hash (use create_user.py)")
        if entry.get("role") not in ("rater", "pilot", "admin"):
            ERRORS.append(f"user {user}: role {entry.get('role')!r} is not a Stage 2 role")
    if not any(e.get("role") == "admin" for e in users.values()):
        ERRORS.append("no admin account in config/users.json")
    print("accounts: " + ", ".join(f"{u} ({e.get('role')})" for u, e in sorted(users.items())))
    return users


def _check_folders(split: Path, checksums: bool) -> list:
    if not split.is_dir():
        ERRORS.append("stage2_split/ missing")
        return []
    raters = sorted(p.name for p in split.iterdir() if p.is_dir() and not p.name.startswith("."))
    if not raters:
        ERRORS.append("stage2_split/ has no rater folders")
    for rater in raters:
        folder = split / rater
        manifest = folder / "manifest.jsonl"
        if not manifest.is_file():
            ERRORS.append(f"stage2_split/{rater}/manifest.jsonl missing")
            continue
        rows = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines()
                if line.strip()]
        missing_img = [r["file"] for r in rows if not (folder / r["file"]).is_file()]
        missing_mask = [r["views"]["mask"] for r in rows
                        if (r.get("views") or {}).get("mask")
                        and not (folder / r["views"]["mask"]).is_file()]
        no_mask = sum(1 for r in rows if not (r.get("views") or {}).get("mask"))
        for kind, missing in (("images", missing_img), ("masks", missing_mask)):
            if missing:
                ERRORS.append(f"stage2_split/{rater}: {len(missing)} {kind} missing, "
                              f"e.g. {missing[0]}")
        if no_mask:
            NOTES.append(f"stage2_split/{rater}: {no_mask} images without a mask "
                         "(scored against the label boxes instead)")
        bad = _verify_checksums(folder) if checksums else None
        status = "" if bad is None else (" checksums OK" if not bad else
                                         f" {len(bad)} CHECKSUM MISMATCH(ES)")
        if bad:
            ERRORS.append(f"stage2_split/{rater}: checksum mismatch, e.g. {bad[0]}")
        labels = sum(1 for r in rows if r.get("label") == "tampered")
        print(f"stage2_split/{rater}: {len(rows)} images ({labels} tampered){status}")
    return raters


def _verify_checksums(folder: Path):
    listing = folder / "checksums.sha256"
    if not listing.is_file():
        NOTES.append(f"{folder.name}: no checksums.sha256")
        return None
    bad = []
    for line in listing.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, rel = line.split(None, 1)
        path = folder / rel.strip().lstrip("*")
        if not path.is_file() or _sha256(path) != digest:
            bad.append(rel.strip())
    return bad


def _check_practice(practice: Path) -> None:
    listing = practice / "practice.json"
    if not listing.is_file():
        NOTES.append("no stage2/practice/practice.json — practice image picked at random "
                     "from the rater folders (test setting)")
        return
    items = json.loads(listing.read_text(encoding="utf-8")).get("items", [])
    missing = [i["image"] for i in items if not (practice / i["image"]).is_file()]
    if missing:
        ERRORS.append(f"stage2/practice: {len(missing)} practice images missing")
    print(f"practice: {len(items)} image(s)")


def _check_readable(root: Path) -> None:
    """The container user must be able to read everything it mounts."""
    if os.name != "posix":
        return
    blocked = []
    for path in [root, *root.rglob("*")]:
        st = path.stat()
        need = 0o005 if path.is_dir() else 0o004
        if st.st_uid != CONTAINER_UID and (st.st_mode & need) != need:
            blocked.append(str(path.relative_to(root)) or ".")
    if blocked:
        ERRORS.append(f"{len(blocked)} file(s) the container cannot read, e.g. {blocked[0]}: "
                      f"sudo chown -R {CONTAINER_UID}:{CONTAINER_UID} {root}")


def _check_writable(path: Path) -> None:
    if not path.is_dir():
        ERRORS.append(f"{path.name}/ missing: mkdir -p {path}")
        return
    owners = {p.stat().st_uid for p in [path, *path.rglob("*")]}
    if os.name == "posix" and owners != {CONTAINER_UID}:
        ERRORS.append(f"stage2/{path.name} is not owned by UID {CONTAINER_UID} (the app "
                      f"cannot write): sudo chown -R {CONTAINER_UID}:{CONTAINER_UID} "
                      f"{path.parents[1]}")


def _check_package(root: Path) -> None:
    package = root / "PACKAGE.json"
    if not package.is_file():
        NOTES.append("no PACKAGE.json (not made by package_stage2.py)")
        return
    expected = json.loads(package.read_text(encoding="utf-8")).get("sha256", {})
    changed = [rel for rel, digest in expected.items()
               if not rel.startswith("stage2/outputs/") and not rel.startswith("stage2/plans/")
               and (not (root / rel).is_file() or _sha256(root / rel) != digest)]
    if changed:
        ERRORS.append(f"{len(changed)} file(s) differ from PACKAGE.json, e.g. {changed[0]} "
                      "(incomplete upload?)")
    else:
        print(f"upload complete: {len(expected)} files match PACKAGE.json")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("root", type=Path, nargs="?", default=Path("study_data"))
    parser.add_argument("--no-checksums", dest="checksums", action="store_false")
    return parser.parse_args()


if __name__ == "__main__":
    main()
