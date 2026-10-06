"""Read `config/datasets.json` and scan set1..set4 into one manifest of images.

The folder layout is deliberately pluggable, because each set's file naming is not
fixed yet. Every image found directly in a set folder becomes one item; its metadata
(document id, authentic/tampered, method, layout, label boxes, companion views such as
the authentic page or the mask) comes from, in increasing priority:

1. the set defaults in the config (`side`, `family`);
2. `filename_pattern` — a regex with named groups matched against the file stem,
   e.g. ``^(?P<doc_id>\\d+)_(?P<label>auth|tamp)(?:_(?P<method>\\w+))?$``;
3. `metadata` — a per-set CSV / JSON / JSONL file with one row per image, e.g.
   ``{"file": "labels.jsonl", "key": "file", "fields": {"doc_id": "document_id"}}``.

`view_templates` adds companion images shown in the Stage 1 panel, formatted from the
item's fields, e.g. ``{"mask": "masks/{stem}.png", "authentic": "{doc_id}_auth.png"}``.
Any of these keys may also be set per set to override the global value.
"""
from __future__ import annotations

import csv
import fnmatch
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from core.settings import DATASETS_CONFIG_PATH, PROJECT_ROOT

logger = logging.getLogger(__name__)

AUTHENTIC = "authentic"
TAMPERED = "tampered"
UNKNOWN = "unknown"

# Fields the rest of the app understands. Anything else from the metadata is kept in
# `extra` so nothing the dataset provides is silently lost.
KNOWN_FIELDS = (
    "doc_id", "label", "method", "model", "edit_type", "layout", "capture", "image_id", "boxes",
)

_OVERRIDABLE = (
    "filename_pattern", "metadata", "view_templates", "exclude_patterns", "boxes_format",
)


@dataclass
class SetConfig:
    name: str
    folder: Path
    side: str
    family: str
    filename_pattern: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    view_templates: Dict[str, str] = field(default_factory=dict)
    exclude_patterns: List[str] = field(default_factory=list)
    boxes_format: str = "xyxy"


@dataclass
class DatasetConfig:
    root: Path
    sets: List[SetConfig]
    image_extensions: List[str]
    recursive: bool = False
    dedupe_identical_files: bool = True
    label_values: Dict[str, List[str]] = field(default_factory=dict)
    # Stage 1 shows only tampered images; authentic ones are kept unless their whole
    # document is rejected (their masks are all black, there is no edit to check).
    review_only_tampered: bool = True
    # Show each tampered image's authentic page (same set, same doc_id) beside it.
    link_authentic: bool = True

    def set_by_name(self, name: str) -> SetConfig:
        return next(s for s in self.sets if s.name == name)


def load_dataset_config(path: Path = DATASETS_CONFIG_PATH) -> DatasetConfig:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    root = _resolve(raw.get("root", "data"))
    sets = []
    for name, spec in raw["sets"].items():
        merged = {key: spec.get(key, raw.get(key)) for key in _OVERRIDABLE}
        sets.append(
            SetConfig(
                name=name,
                folder=root / spec.get("folder", name),
                side=spec["side"],
                family=spec.get("family", ""),
                filename_pattern=merged["filename_pattern"],
                metadata=merged["metadata"],
                view_templates=merged["view_templates"] or {},
                exclude_patterns=merged["exclude_patterns"] or [],
                boxes_format=merged["boxes_format"] or "xyxy",
            )
        )
    return DatasetConfig(
        root=root,
        sets=sets,
        image_extensions=[e.lower() for e in raw.get("image_extensions", [".png", ".jpg"])],
        recursive=bool(raw.get("recursive", False)),
        dedupe_identical_files=bool(raw.get("dedupe_identical_files", True)),
        label_values=raw.get("label_values", {}),
        review_only_tampered=bool(raw.get("review_only_tampered", True)),
        link_authentic=bool(raw.get("link_authentic", True)),
    )


# --------------------------------------------------------------------------- #
# scanning
# --------------------------------------------------------------------------- #


def scan_datasets(config: DatasetConfig, *, with_hash: bool = True) -> tuple[List[Dict], List[str]]:
    """Return (items, warnings). Items are sorted by set, then file name."""
    items: List[Dict[str, Any]] = []
    warnings: List[str] = []
    for set_cfg in config.sets:
        if not set_cfg.folder.is_dir():
            warnings.append(f"{set_cfg.name}: folder {set_cfg.folder} not found — skipped")
            continue
        files = _image_files(set_cfg, config)
        metadata = _load_metadata(set_cfg, warnings)
        pattern = re.compile(set_cfg.filename_pattern) if set_cfg.filename_pattern else None
        unmatched = 0
        for path in files:
            item, matched = _build_item(path, set_cfg, config, pattern, metadata)
            unmatched += 0 if matched else 1
            if with_hash:
                item["sha256"] = _sha256(path)
            item["width"], item["height"] = _image_size(path)
            items.append(item)
        if pattern and unmatched:
            warnings.append(f"{set_cfg.name}: {unmatched} file(s) did not match filename_pattern")
        logger.info("%s: %d images", set_cfg.name, len(files))
    _mark_duplicates(items, config.dedupe_identical_files)
    if config.link_authentic:
        _link_authentic(items)
    return items, warnings


def _image_files(set_cfg: SetConfig, config: DatasetConfig) -> List[Path]:
    iterator: Iterable[Path] = (
        set_cfg.folder.rglob("*") if config.recursive else set_cfg.folder.iterdir()
    )
    files = []
    for path in iterator:
        if not path.is_file() or path.suffix.lower() not in config.image_extensions:
            continue
        rel = path.relative_to(set_cfg.folder).as_posix()
        if any(fnmatch.fnmatch(path.name, pat) or fnmatch.fnmatch(rel, pat)
               for pat in set_cfg.exclude_patterns):
            continue
        files.append(path)
    return sorted(files, key=lambda p: p.relative_to(set_cfg.folder).as_posix())


def _build_item(
    path: Path,
    set_cfg: SetConfig,
    config: DatasetConfig,
    pattern: Optional[re.Pattern],
    metadata: Dict[str, Dict[str, Any]],
) -> tuple[Dict[str, Any], bool]:
    rel_in_set = path.relative_to(set_cfg.folder).as_posix()
    fields: Dict[str, Any] = {}
    matched = True
    if pattern:
        match = pattern.search(path.stem)
        matched = match is not None
        if match:
            fields.update({k: v for k, v in match.groupdict().items() if v is not None})
    row = metadata.get(rel_in_set) or metadata.get(path.name) or metadata.get(path.stem)
    if row:
        fields.update(row)

    item: Dict[str, Any] = {
        "id": f"{set_cfg.name}/{rel_in_set}",
        "set": set_cfg.name,
        "name": rel_in_set,
        "stem": path.stem,
        "file": path.relative_to(config.root).as_posix(),
        "side": set_cfg.side,
        "family": set_cfg.family,
    }
    for key in KNOWN_FIELDS:
        item[key] = fields.pop(key, None)
    item["label"] = normalise_label(item["label"], config.label_values)
    item["boxes"] = normalise_boxes(item["boxes"], set_cfg.boxes_format)
    if item["doc_id"] is not None:
        item["doc_id"] = str(item["doc_id"])
    if isinstance(item["capture"], dict):          # e.g. {"condition": "jpeg", "quality": [56]}
        fields["capture_detail"] = item["capture"]
        item["capture"] = item["capture"].get("condition")
    item["extra"] = {k: v for k, v in fields.items() if _json_safe(v)}
    item["views"] = _resolve_views(item, fields, set_cfg, config.root)
    return item, matched


def _resolve_views(item: Dict, fields: Dict, set_cfg: SetConfig, root: Path) -> Dict[str, str]:
    values = {**fields, **{k: v for k, v in item.items() if isinstance(v, (str, int))}}
    views = {}
    for name, template in set_cfg.view_templates.items():
        try:
            rel = template.format(**values)
        except (KeyError, IndexError, ValueError):
            continue
        candidate = (set_cfg.folder / rel).resolve()
        own = (set_cfg.folder / item["name"]).resolve()
        if candidate != own and candidate.is_file() and _is_within(candidate, root.resolve()):
            views[name] = candidate.relative_to(root.resolve()).as_posix()
    return views


def _link_authentic(items: List[Dict[str, Any]]) -> None:
    authentic: Dict[tuple, str] = {}
    for item in items:
        if item["label"] == AUTHENTIC and item.get("doc_id"):
            authentic.setdefault((item["set"], item["doc_id"]), item["file"])
    for item in items:
        if item["label"] == AUTHENTIC or "authentic" in item["views"]:
            continue
        linked = authentic.get((item["set"], item.get("doc_id")))
        if linked:
            item["views"] = {"authentic": linked, **item["views"]}


def _mark_duplicates(items: List[Dict[str, Any]], enabled: bool) -> None:
    """Identical files (e.g. the authentic page copied into Set 1 and Set 2) are reviewed
    once: later copies point at the first one with `duplicate_of`."""
    # Keyed on the label too: a "tampered" file identical to its authentic page (an edit
    # that changed nothing) must still be shown in Stage 1, not hidden as a copy.
    first_by_hash: Dict[tuple, str] = {}
    for item in items:
        item["duplicate_of"] = None
        digest = item.get("sha256")
        if not enabled or not digest:
            continue
        key = (digest, item["label"])
        if key in first_by_hash:
            item["duplicate_of"] = first_by_hash[key]
        else:
            first_by_hash[key] = item["id"]


# --------------------------------------------------------------------------- #
# metadata files
# --------------------------------------------------------------------------- #


def _load_metadata(set_cfg: SetConfig, warnings: List[str]) -> Dict[str, Dict[str, Any]]:
    spec = set_cfg.metadata
    if not spec:
        return {}
    path = Path(spec["file"])
    path = path if path.is_absolute() else set_cfg.folder / path
    if not path.is_file():
        warnings.append(f"{set_cfg.name}: metadata file {path} not found — filenames only")
        return {}
    key = spec.get("key", "file")
    rename = {src: dst for dst, src in (spec.get("fields") or {}).items()}
    rows = _read_rows(path)
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        file_key = row.get(key)
        if file_key in (None, ""):
            continue
        mapped = {rename.get(k, k): v for k, v in row.items() if k != key}
        file_key = str(file_key)
        out[file_key] = mapped
        out.setdefault(Path(file_key).name, mapped)
        out.setdefault(Path(file_key).stem, mapped)
    return out


def _read_rows(path: Path) -> List[Dict[str, Any]]:
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8")
    if suffix == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    if suffix == ".json":
        data = json.loads(text)
        if isinstance(data, dict):  # {"file": {...}} form
            return [{"file": k, **v} for k, v in data.items() if isinstance(v, dict)]
        return list(data)
    return list(csv.DictReader(text.splitlines()))


# --------------------------------------------------------------------------- #
# normalisation helpers
# --------------------------------------------------------------------------- #


def normalise_label(value: Any, label_values: Dict[str, List[str]]) -> str:
    if value is None or value == "":
        return UNKNOWN
    text = str(value).strip().lower()
    for canonical, aliases in label_values.items():
        if text == canonical or text in {str(a).lower() for a in aliases}:
            return canonical
    return UNKNOWN


def normalise_boxes(value: Any, fmt: str = "xyxy") -> List[List[float]]:
    """Accept a JSON string, a list of boxes, a single box or dicts with 'bbox'/'box'."""
    if value in (None, "", []):
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    if isinstance(value, dict):
        value = [value]
    if value and isinstance(value[0], (int, float)):
        value = [value]
    boxes = []
    for entry in value:
        if isinstance(entry, dict):
            entry = entry.get("bbox") or entry.get("box") or entry.get("xyxy")
        if not isinstance(entry, (list, tuple)) or len(entry) != 4:
            continue
        x0, y0, a, b = (float(v) for v in entry)
        boxes.append([x0, y0, x0 + a, y0 + b] if fmt == "xywh" else [x0, y0, a, b])
    return boxes


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _image_size(path: Path) -> tuple[Optional[int], Optional[int]]:
    try:
        from PIL import Image

        with Image.open(path) as img:
            return img.size
    except Exception:  # unreadable image: keep it in the manifest, the reviewer will see it
        logger.warning("Could not read image size of %s", path)
        return None, None


def _resolve(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _json_safe(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool, list, dict)) or value is None
