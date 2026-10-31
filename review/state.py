"""Stage 1 review state: the manifest, the decision store and each reviewer's position.

One `ReviewData` is shared by every browser tab; each tab has its own `ReviewView`, which
also carries who is looking (`user`, `role`). With reviewer folders
(`scripts/split_stage1.py`) a `reviewer` sees only the images in their own folder; an `admin` —
and the local single-user mode — sees everything.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.jsonstore import RecordStore, write_json_atomic
from core.manifest import manifest_sha256
from core.settings import DECISIONS_DIR, RECHECK_DIR, SELECTION_PATH
from review.assign import Split
from review.selection import (
    DOCUMENT_SCOPE_REASONS,
    KEEP,
    PENDING,
    REJECT,
    compute_selection,
    document_rejects,
    needs_review,
    now_iso,
    recheck_sample,
    resolve_status,
)

logger = logging.getLogger(__name__)

ALL = "all"
ADMIN = "admin"
REVIEWER = "reviewer"
ZOOM_STEPS = (50, 75, 100, 125, 150, 200, 300)


@dataclass
class ReviewData:
    """Everything shared by all browser tabs of the review app."""

    items: List[Dict[str, Any]]
    recheck: bool = False
    recheck_fraction: float = 0.05
    recheck_seed: int = 7
    review_only_tampered: bool = True
    # Where images are read from without a split (config root), and the reviewer folders.
    dataset_root: Optional[Path] = None
    split: Split = field(default_factory=Split)
    # --only: review just these image ids (e.g. results/stage1_carryover/to_check.txt)
    only: Optional[set] = None
    decisions: RecordStore = field(init=False)
    rechecks: RecordStore = field(init=False)
    by_id: Dict[str, Dict[str, Any]] = field(init=False)
    assignee: Dict[str, str] = field(init=False)
    _manifest_sha: str = field(init=False, default="")
    _caches: Dict[Any, Any] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        self.decisions = RecordStore(DECISIONS_DIR, "decisions", key=lambda r: r["id"])
        # A re-check is stored per (image, re-checker): the author can audit an image
        # that its reviewer has also re-checked.
        self.rechecks = RecordStore(RECHECK_DIR, "recheck",
                                    key=lambda r: f"{r['id']}|{r['reviewer']}")
        self.by_id = {item["id"]: item for item in self.items}
        self.assignee = {i: user for user, ids in self.assignments.items() for i in ids}
        self._manifest_sha = manifest_sha256()
        self._known_files = {item["file"] for item in self.items} | {
            v for item in self.items for v in (item.get("views") or {}).values()}

    @property
    def assignments(self) -> Dict[str, set]:
        """reviewer -> image ids in their folder. Empty: no split, all see everything."""
        return self.split.assignments

    # ---- who sees what -------------------------------------------------------- #

    def restricted(self, user: str, role: str) -> bool:
        return bool(self.assignments) and role != ADMIN

    def queue(self, user: str, role: str) -> List[Dict[str, Any]]:
        """The images this person reviews; duplicates are decided through their original."""
        decisions = self.decisions.latest()
        key = ("queue", user, role)
        cached = self._caches.get(key)
        if cached is not None and cached[0] is decisions:
            return cached[1]
        if self.recheck:
            queue = [self.by_id[i] for i in self._recheck_ids(user, role, decisions)]
        else:
            if self.restricted(user, role):
                mine = self.assignments.get(user, set())
            elif self.split.active:      # admin: everything that is in some reviewer folder
                mine = set(self.assignee)
            else:
                mine = None
            queue = [
                item for item in self.items
                if not item.get("duplicate_of")
                and needs_review(item, self.review_only_tampered)
                and (mine is None or item["id"] in mine)
                and (self.only is None or item["id"] in self.only)
            ]
        self._caches[key] = (decisions, queue)
        return queue

    def _recheck_ids(self, user: str, role: str, decisions: Dict[str, Dict]) -> List[str]:
        """A reviewer re-checks a share of their own decisions; the admin (with a split)
        audits the same share of every other reviewer's decisions."""
        audit = bool(self.assignments) and role == ADMIN
        by_reviewer: Dict[str, Dict[str, Dict]] = {}
        for item_id, record in decisions.items():
            who = record.get("reviewer", "")
            if (who != user) if audit else (who == user):
                by_reviewer.setdefault(who, {})[item_id] = record
        ids: List[str] = []
        for who in sorted(by_reviewer):
            ids += recheck_sample(self.items, by_reviewer[who], self.recheck_fraction,
                                  self.recheck_seed)
        return ids

    def image_path(self, rel_path: str, user: str, role: str) -> Optional[Path]:
        """The file behind /dataset-image/<rel_path> for this person, or None.

        Only images and masks of the manifest are served. With a split they are read from
        the reviewer folder that holds them, and a reviewer gets only their own folder."""
        if rel_path not in self._known_files:
            return None
        if not self.split.active:
            return self.dataset_root / rel_path if self.dataset_root else None
        owner = self.split.file_owner.get(rel_path)
        if owner is None or (self.restricted(user, role) and owner != user):
            return None
        return self.split.path_of(rel_path)

    # ---- status ------------------------------------------------------------- #

    def status(self, item: Dict[str, Any], user: str) -> tuple[str, Optional[Dict[str, Any]]]:
        if self.recheck:
            record = self.rechecks.latest().get(f"{item['id']}|{user}")
            return (record["decision"], record) if record else (PENDING, None)
        return self.status_map().get(item["id"], (PENDING, None))

    def status_map(self) -> Dict[str, tuple[str, Optional[Dict[str, Any]]]]:
        """id -> (status, record) over the manifest, recomputed only after a write."""
        latest = self.decisions.latest()
        cached = self._caches.get("status")
        if cached is not None and cached[0] is latest:
            return cached[1]
        doc_rejects = document_rejects(self.by_id, latest)
        result = {
            i: resolve_status(it, latest, doc_rejects, self.review_only_tampered)
            for i, it in self.by_id.items()
        }
        self._caches["status"] = (latest, result)
        return result

    def counts(self, user: str, role: str) -> Dict[str, Dict[str, int]]:
        out: Dict[str, Dict[str, int]] = {}
        for item in self.queue(user, role):
            status, _ = self.status(item, user)
            c = out.setdefault(item["set"], {KEEP: 0, REJECT: 0, PENDING: 0})
            c[status] += 1
        return out

    def progress_by_reviewer(self) -> Dict[str, tuple[int, int]]:
        """reviewer -> (decided, assigned) for the admin's overview."""
        statuses = self.status_map()
        out = {}
        for user, ids in sorted(self.assignments.items()):
            queue = [i for i in ids if i in self.by_id and not self.by_id[i].get("duplicate_of")
                     and needs_review(self.by_id[i], self.review_only_tampered)]
            done = sum(1 for i in queue if statuses.get(i, (PENDING,))[0] != PENDING)
            out[user] = (done, len(queue))
        return out

    def duplicates_of(self, item_id: str) -> List[str]:
        return [i["id"] for i in self.items if i.get("duplicate_of") == item_id]

    def siblings(self, item: Dict[str, Any]) -> List[str]:
        """Other images of the same document (both paired sets), for the info panel."""
        if not item.get("doc_id"):
            return []
        return [
            i["id"] for i in self.items
            if i["id"] != item["id"] and i["side"] == item["side"]
            and i.get("doc_id") == item["doc_id"]
        ]

    # ---- writing ------------------------------------------------------------- #

    def decide(
        self, item: Dict[str, Any], decision: str, *, reviewer: str, reason: str = "",
        scope: str = "image", note: str = "",
    ) -> None:
        if decision not in (KEEP, REJECT):
            raise ValueError(decision)
        if decision == REJECT and reason in DOCUMENT_SCOPE_REASONS:
            scope = "document"
        record = {
            "id": item["id"],
            "set": item["set"],
            "file": item["name"],
            "doc_id": item.get("doc_id"),
            "decision": decision,
            "reason": reason if decision == REJECT else "",
            "scope": scope if decision == REJECT and item.get("doc_id") else "image",
            "note": note.strip(),
            "reviewer": reviewer,
            "timestamp": now_iso(),
        }
        if self.recheck:
            original = self.decisions.latest().get(item["id"]) or {}
            record["rechecks"] = original.get("reviewer", "")
            self.rechecks.append(record)
            return
        self.decisions.append(record)
        self.write_selection()

    def write_selection(self) -> Dict[str, Any]:
        selection = compute_selection(
            self.items, self.decisions.latest(), manifest_sha256=self._manifest_sha,
            review_only_tampered=self.review_only_tampered,
        )
        selection["reviewers"] = sorted({r.get("reviewer", "") for r in
                                         self.decisions.latest().values()})
        write_json_atomic(SELECTION_PATH, selection)
        return selection


@dataclass
class ReviewView:
    """Who is looking and where they are: filters, position, zoom. One per browser tab."""

    user: str = "local"
    role: str = ADMIN
    set_filter: str = ALL
    status_filter: str = PENDING
    label_filter: str = ALL
    assignee_filter: str = ALL       # admin only, with a split
    index: int = 0
    zoom: int = 100
    show_boxes: bool = True
    show_references: bool = False   # authentic page + mask under the tampered image
    pending_reject: bool = False
    reject_scope_document: bool = False
    note: str = ""

    def filtered(self, data: ReviewData) -> List[Dict[str, Any]]:
        out = []
        for item in data.queue(self.user, self.role):
            if self.set_filter != ALL and item["set"] != self.set_filter:
                continue
            if self.label_filter != ALL and item["label"] != self.label_filter:
                continue
            if self.assignee_filter != ALL and data.assignee.get(item["id"]) != \
                    self.assignee_filter:
                continue
            if self.status_filter != ALL and \
                    data.status(item, self.user)[0] != self.status_filter:
                continue
            out.append(item)
        return out

    def clamp(self, length: int) -> None:
        self.index = max(0, min(self.index, max(length - 1, 0)))

    def reset_decision_ui(self) -> None:
        self.pending_reject = False
        self.reject_scope_document = False
        self.note = ""
