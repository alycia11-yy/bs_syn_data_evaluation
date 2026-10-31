#!/usr/bin/env python3
"""Put together the folder to upload to the Stage 2 server (study_data/).

    python scripts/package_stage2.py                    # -> study_data/
    python scripts/package_stage2.py --archive          # + study_data.tar.gz to upload
    python scripts/package_stage2.py --out /some/where/study_data --force

Run it after scripts/split_stage2.py and after creating the Stage 2 accounts
(`python create_user.py R1 R2 R3 R4 --role rater --generate`, `… jiayu --role admin`).

    study_data/
      config/users.json        Stage 2 accounts only (rater, pilot, admin) — Stage 1
                               reviewer accounts are left out
      config/study.json        the study settings, frozen with the data
      stage2_split/R1 … R4/    the rater folders, copied from data/stage2_split/
      stage2/practice/         practice images + practice.json (if made)
      stage2/plans/            empty — the app writes each rater's order here
      stage2/outputs/          empty — the app writes <rater>/ratings.jsonl here
      controls/                crude / clean control pages (empty if none)
      PACKAGE.json             what went in, with SHA-256 of every file

Never packaged: data/stage2/key/, data/stage2/pool/, data/stage1/, the full datasets.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.settings import (  # noqa: E402
    DATA_DIR,
    STAGE2_DIR,
    STAGE2_SPLIT_DIR,
    STUDY_CONFIG_PATH,
    USERS_FILE,
)

STAGE2_ROLES = {"rater", "pilot", "admin"}


def main() -> None:
    args = _parse_args()
    out: Path = args.out
    if out.exists() and any(out.iterdir()):
        if not args.force:
            sys.exit(f"{out} is not empty. Pass --force to delete and redo it.")
        if (out / "stage2" / "outputs").is_dir() and any((out / "stage2" / "outputs").rglob(
                "*.jsonl")):
            sys.exit(f"{out}/stage2/outputs holds ratings — move them away first, this "
                     "script never deletes ratings.")
        shutil.rmtree(out)

    raters = sorted(p.name for p in args.split.iterdir()
                    if p.is_dir() and not p.name.startswith(".")) if args.split.is_dir() else []
    if not raters:
        sys.exit(f"No rater folders in {args.split}: run scripts/split_stage2.py first.")
    users = _stage2_users(args.users, raters)

    (out / "config").mkdir(parents=True)
    (out / "config" / "users.json").write_text(json.dumps(users, indent=2) + "\n",
                                               encoding="utf-8")
    shutil.copy2(args.config, out / "config" / "study.json")
    shutil.copytree(args.split, out / "stage2_split")
    practice = STAGE2_DIR / "practice"
    if practice.is_dir():
        shutil.copytree(practice, out / "stage2" / "practice")
    else:
        (out / "stage2" / "practice").mkdir(parents=True)
        print("NOTE no data/stage2/practice — the website will pick a practice image at "
              "random from the rater folders (split_stage2.py --practice N makes them).")
    for name in ("plans", "outputs"):
        (out / "stage2" / name).mkdir(parents=True, exist_ok=True)
    controls = DATA_DIR / "controls"
    if controls.is_dir():
        shutil.copytree(controls, out / "controls")
    else:
        (out / "controls").mkdir()

    files = {str(p.relative_to(out)): _sha256(p) for p in sorted(out.rglob("*"))
             if p.is_file()}
    (out / "PACKAGE.json").write_text(json.dumps({
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "raters": raters,
        "accounts": {u: v["role"] for u, v in sorted(users.items())},
        "files": len(files),
        "sha256": files,
    }, indent=2) + "\n", encoding="utf-8")
    size = sum((out / f).stat().st_size for f in files)
    print(f"Done -> {out}  ({len(files)} files, {size / 1e6:.1f} MB)")
    print(f"  rater folders: {', '.join(raters)}")
    accounts = ", ".join(f"{u} ({v['role']})" for u, v in sorted(users.items()))
    print(f"  accounts: {accounts}")

    if args.archive:
        archive = out.parent / f"{out.name}.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(out, arcname=out.name)
        print(f"Archive -> {archive}  (on the server: tar xzf {archive.name})")


def _stage2_users(path: Path, raters) -> dict:
    if not path.is_file():
        sys.exit(f"{path} not found: create the accounts first (create_user.py).")
    users = json.loads(path.read_text(encoding="utf-8"))
    kept = {u: v for u, v in users.items() if v.get("role") in STAGE2_ROLES}
    dropped = sorted(set(users) - set(kept))
    if dropped:
        print(f"Left out (not Stage 2 accounts): {', '.join(dropped)}")
    if not any(v["role"] == "admin" for v in kept.values()):
        sys.exit("No admin account: python create_user.py <you> --role admin --generate")
    missing = [r for r in raters if r not in kept]
    if missing:
        sys.exit(f"No account for rater folder(s) {', '.join(missing)}: "
                 f"python create_user.py {' '.join(missing)} --role rater --generate")
    no_folder = sorted(u for u, v in kept.items() if v["role"] != "admin" and u not in raters)
    if no_folder:
        print(f"NOTE no folder for {', '.join(no_folder)} — they will see 'no images'.")
    return kept


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=Path, default=Path("study_data"))
    parser.add_argument("--split", type=Path, default=STAGE2_SPLIT_DIR)
    parser.add_argument("--users", type=Path, default=USERS_FILE)
    parser.add_argument("--config", type=Path, default=STUDY_CONFIG_PATH)
    parser.add_argument("--archive", action="store_true", help="also write <out>.tar.gz")
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    main()
