"""Paths and environment switches shared by both stages. Single source of truth."""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _path(env: str, default: Path) -> Path:
    """An env override, relative paths taken from the repository root."""
    value = os.getenv(env)
    if not value:
        return default
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


CONFIG_DIR = _path("CONFIG_DIR", PROJECT_ROOT / "config")
DATA_DIR = _path("DATA_DIR", PROJECT_ROOT / "data")
RESULTS_DIR = _path("RESULTS_DIR", PROJECT_ROOT / "results")

DATASETS_CONFIG_PATH = _path("DATASETS_CONFIG", CONFIG_DIR / "datasets.json")
STUDY_CONFIG_PATH = _path("STUDY_CONFIG", CONFIG_DIR / "study.json")
USERS_FILE = _path("USERS_FILE", CONFIG_DIR / "users.json")

# ---- Stage 1: author check (local only) ------------------------------------ #
STAGE1_DIR = DATA_DIR / "stage1"
MANIFEST_PATH = STAGE1_DIR / "manifest.jsonl"
DECISIONS_DIR = STAGE1_DIR / "decisions"      # decisions.jsonl + decisions_latest.json
RECHECK_DIR = STAGE1_DIR / "recheck"          # the blind 5 % self re-check
SELECTION_PATH = STAGE1_DIR / "selection.json"
# One folder per reviewer, made by scripts/split_stage1.py. Absent = no split.
STAGE1_SPLIT_DIR = _path("STAGE1_SPLIT_DIR", DATA_DIR / "stage1_split")

# ---- Stage 2: realism study ------------------------------------------------- #
STAGE2_DIR = _path("STAGE2_DIR", DATA_DIR / "stage2")
# One folder per rater (scripts/split_stage2.py), same layout as the Stage 1 folders.
# When it has rater folders, the study website uses them instead of pool/.
STAGE2_SPLIT_DIR = _path("STAGE2_SPLIT_DIR", DATA_DIR / "stage2_split")
# What the study server needs: the blinded item list and the renamed images.
POOL_DIR = _path("POOL_DIR", STAGE2_DIR / "pool")
POOL_ITEMS_PATH = POOL_DIR / "study_items.json"
POOL_IMAGES_DIR = POOL_DIR / "images"
# What only the analyst keeps: study id -> set, label, method, boxes. Never deploy.
KEY_DIR = STAGE2_DIR / "key"
STUDY_KEY_PATH = KEY_DIR / "study_key.json"
# Per-rater orders (created on first sign-in) and per-rater ratings.
PLANS_DIR = _path("PLANS_DIR", STAGE2_DIR / "plans")
RATINGS_DIR = _path("RATINGS_DIR", STAGE2_DIR / "outputs")


def is_development() -> bool:
    return os.getenv("APP_ENV", "dev").strip().lower() in {"dev", "development"}
