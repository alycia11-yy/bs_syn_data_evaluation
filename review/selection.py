"""Stage 1 decisions -> selected / rejected / pending, per set. Pure logic, no UI.

A decision record (one line of data/stage1/decisions/decisions.jsonl):

    {"id": "set1/r0001_tamp1_copymove.png", "decision": "reject",
     "reason": "damaged_page", "scope": "image", "note": "", "reviewer": "jiayu",
     "timestamp": "2026-10-06T08:00:00Z"}

Resolution order for one image:
1. a `scope: "document"` reject of any image of the same (side, doc_id) rejects it —
   the base page is shared by every version of that document, in both paired sets —
   unless the image has its own decision made later than that reject;
2. otherwise its own decision, or that of the identical file it duplicates;
3. otherwise, with `review_only_tampered`, an authentic image is kept (it is never shown
   in Stage 1: its mask is all black, there is no edit to check);
4. otherwise it is pending.
"""
from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

KEEP = "keep"
AUTO_KEEP_REASON = "authentic (not reviewed)"
REJECT = "reject"
PENDING = "pending"

# (key, short label, description shown to the reviewer). From doc 12 §1.2.
REJECT_REASONS: Tuple[Tuple[str, str, str], ...] = (
    ("wrong_text", "Wrong / garbled text",
     "New text is not what the label says, unreadable, or mixes characters."),
    ("damaged_page", "Damaged page",
     "The edit left a smear, overlapping or cut letters, or a visible patch."),
    ("label_mismatch", "Label does not cover edit",
     "The label box or mask does not cover the edit."),
    ("privacy_leak", "Privacy leak",
     "A synthetic page shows a real name, number or bank identity."),
    ("broken_base", "Broken base document",
     "The base page itself is broken — rejects every version of this document."),
    ("other", "Other", "Anything else that makes the image unusable; say what in the note."),
)
REASON_KEYS = tuple(key for key, _, _ in REJECT_REASONS)
DOCUMENT_SCOPE_REASONS = {"broken_base"}


def doc_key(item: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    return (item["side"], item["doc_id"]) if item.get("doc_id") else None


def canonical_id(item: Dict[str, Any]) -> str:
    return item.get("duplicate_of") or item["id"]


def resolve_status(
    item: Dict[str, Any],
    decisions: Dict[str, Dict[str, Any]],
    doc_rejects: Dict[Tuple[str, str], Dict[str, Any]],
    review_only_tampered: bool = False,
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Return (status, the decision record that produced it).

    A whole-document reject covers every image of the document, except one whose own
    decision was made *after* it: pressing Keep on one version afterwards must stick.
    """
    record = decisions.get(item["id"]) or decisions.get(canonical_id(item))
    key = doc_key(item)
    doc_reject = doc_rejects.get(key) if key else None
    if doc_reject is not None and not _later(record, doc_reject):
        return REJECT, doc_reject
    if record is None:
        if review_only_tampered and not needs_review(item, True):
            return KEEP, None
        return PENDING, None
    return record["decision"], record


def needs_review(item: Dict[str, Any], review_only_tampered: bool) -> bool:
    """Is this image shown in Stage 1? Unknown labels are shown, to be safe."""
    return not review_only_tampered or item.get("label") != "authentic"


def document_rejects(
    items_by_id: Dict[str, Dict[str, Any]], decisions: Dict[str, Dict[str, Any]]
) -> Dict[Tuple[str, str], Dict[str, Any]]:
    out = {}
    for record in decisions.values():
        if record.get("decision") != REJECT or record.get("scope") != "document":
            continue
        item = items_by_id.get(record["id"])
        key = doc_key(item) if item else None
        if key and (key not in out or _later(record, out[key])):
            out[key] = record                     # the newest whole-document reject
    return out


def _later(record: Optional[Dict[str, Any]], other: Dict[str, Any]) -> bool:
    """Was `record` made after `other`? The same record is not later than itself."""
    if record is None or record is other:
        return False
    return str(record.get("timestamp", "")) > str(other.get("timestamp", ""))


def compute_selection(
    items: List[Dict[str, Any]],
    decisions: Dict[str, Dict[str, Any]],
    *,
    manifest_sha256: str = "",
    review_only_tampered: bool = False,
) -> Dict[str, Any]:
    by_id = {item["id"]: item for item in items}
    doc_rejects = document_rejects(by_id, decisions)
    auto_kept: Dict[str, int] = {}
    sets: Dict[str, Dict[str, List[str]]] = {}
    rejected_details: List[Dict[str, Any]] = []
    for item in items:
        status, record = resolve_status(item, decisions, doc_rejects, review_only_tampered)
        bucket = sets.setdefault(item["set"], {"selected": [], "rejected": [], "pending": []})
        name = item["name"]
        if status == KEEP:
            bucket["selected"].append(name)
            if record is None:
                auto_kept[item["set"]] = auto_kept.get(item["set"], 0) + 1
        elif status == REJECT:
            bucket["rejected"].append(name)
            rejected_details.append(_reject_detail(item, record))
        else:
            bucket["pending"].append(name)
    counts = {
        s: {k: len(v) for k, v in b.items()}
        | {"total": sum(len(v) for v in b.values()), "authentic_auto_kept": auto_kept.get(s, 0)}
        for s, b in sets.items()
    }
    return {
        "generated_at": now_iso(),
        "manifest_sha256": manifest_sha256,
        "complete": all(c["pending"] == 0 for c in counts.values()),
        "review_only_tampered": review_only_tampered,
        "counts": counts,
        "sets": sets,
        "rejected_details": rejected_details,
    }


def selected_ids(selection: Dict[str, Any]) -> List[str]:
    """Global ids (``<set>/<file>``) of every selected image."""
    return [f"{s}/{name}" for s, b in selection["sets"].items() for name in b["selected"]]


def recheck_sample(
    items: Iterable[Dict[str, Any]], decisions: Dict[str, Dict[str, Any]],
    fraction: float, seed: int,
) -> List[str]:
    """A fixed random share of the already-decided images, for the blind self re-check."""
    decided = sorted(
        item["id"] for item in items
        if not item.get("duplicate_of") and item["id"] in decisions
    )
    if not decided:
        return []
    n = max(1, round(len(decided) * fraction))
    return sorted(random.Random(seed).sample(decided, min(n, len(decided))))


def cohen_kappa(first: List[str], second: List[str]) -> Optional[float]:
    if not first or len(first) != len(second):
        return None
    labels = sorted(set(first) | set(second))
    n = len(first)
    observed = sum(a == b for a, b in zip(first, second)) / n
    expected = sum((first.count(lab) / n) * (second.count(lab) / n) for lab in labels)
    if expected == 1:
        return 1.0
    return (observed - expected) / (1 - expected)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _reject_detail(item: Dict[str, Any], record: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    record = record or {}
    via = record.get("id")
    return {
        "id": item["id"],
        "set": item["set"],
        "file": item["name"],
        "doc_id": item.get("doc_id"),
        "reason": record.get("reason"),
        "scope": record.get("scope", "image"),
        "decided_on": via if via != item["id"] else None,
        "note": record.get("note", ""),
    }
