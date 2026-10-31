#!/usr/bin/env python3
"""Write the reference PDF from the admin's saved Practice answers, without the website.

The usual way is the "Save reference PDF" button at the end of Practice (signed in as the admin);
this does the same from the command line (study/reference.py):

    python scripts/reference_pdf.py                       # --user jiayu
    python scripts/reference_pdf.py --allow-missing       # draft: unrated pages marked "Not rated yet"
    -> results/stage2_reference/reference_examples_<user>_<YYYY-MM-DD>.pdf
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from study.reference import build_reference_pdf  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--user", default="jiayu")
    ap.add_argument("--out", default=None, help="output file (default: results/stage2_reference/…)")
    ap.add_argument("--allow-missing", action="store_true", help="write it even if some pages are not rated")
    a = ap.parse_args()
    try:
        out, missing = build_reference_pdf(a.user, out=Path(a.out) if a.out else None,
                                           allow_missing=a.allow_missing)
    except FileNotFoundError as e:
        print(e)
        return 1
    if out is None:
        print(f"Not written: practice page(s) {', '.join(map(str, missing))} not rated yet by {a.user} "
              "(or use --allow-missing for a draft).")
        return 1
    print(f"{out}" + (f"  (not rated yet: pages {', '.join(map(str, missing))})" if missing else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
