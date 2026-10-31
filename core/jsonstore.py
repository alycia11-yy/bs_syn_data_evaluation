"""Crash-safe JSON storage: an append-only history plus a latest-per-key map.

    <dir>/<name>.jsonl        every write, append-only (canonical)
    <dir>/<name>_latest.json  newest record per key (by timestamp; rebuilt from history)

Same design as the memo annotation tool: a per-directory advisory lock keeps
read / merge / replace atomic across tabs and workers, the latest map is published
with an atomic rename, and a half-published map is repaired from the history.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List

if os.name == "nt":
    import msvcrt
else:
    import fcntl

logger = logging.getLogger(__name__)

Record = Dict[str, Any]


class RecordStore:
    def __init__(self, directory: Path, name: str, key: Callable[[Record], str]) -> None:
        self.directory = Path(directory)
        self.history_path = self.directory / f"{name}.jsonl"
        self.latest_path = self.directory / f"{name}_latest.json"
        self._key = key
        self._cache: tuple[Any, Dict[str, Record]] | None = None

    def latest(self) -> Dict[str, Record]:
        stamp = self._stamp()
        if self._cache is not None and self._cache[0] == stamp:
            return self._cache[1]
        if not self.directory.is_dir():
            # Cached like any other read: callers memoise on the identity of this map.
            self._cache = (stamp, {})
            return self._cache[1]
        try:
            with self._lock():
                latest = self._reconcile()
                stamp = self._stamp()
        except OSError:
            logger.exception("Could not reconcile %s", self.history_path)
            latest = {**_read_json(self.latest_path, {}), **self._read_history()}
        self._cache = (stamp, latest)
        return latest

    def append(self, record: Record) -> Record:
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            with self._lock():
                # The cached map is authoritative while neither file changed since this
                # process last read or wrote them; otherwise re-read (another tab/worker).
                if self._cache is not None and self._cache[0] == self._stamp():
                    latest = dict(self._cache[1])
                else:
                    latest = self._reconcile()
                with self.history_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                    fh.flush()
                    os.fsync(fh.fileno())
                key = self._key(record)
                if _is_newer(record, latest.get(key)):
                    latest[key] = record
                write_json_atomic(self.latest_path, latest)
                self._cache = (self._stamp(), latest)
        except BaseException:
            self._cache = None
            raise
        return record

    def extend(self, records: List[Record]) -> int:
        """Append many records under one lock with one fsync and one rewrite of the
        latest map (append() per record rewrites the map every time: slow for merges)."""
        if not records:
            return 0
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            with self._lock():
                latest = self._reconcile()
                with self.history_path.open("a", encoding="utf-8") as fh:
                    for record in records:
                        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                    fh.flush()
                    os.fsync(fh.fileno())
                for record in records:
                    key = self._key(record)
                    if _is_newer(record, latest.get(key)):
                        latest[key] = record
                write_json_atomic(self.latest_path, latest)
                self._cache = (self._stamp(), latest)
        except BaseException:
            self._cache = None
            raise
        return len(records)

    def history(self) -> List[Record]:
        return list(_iter_history(self.history_path))

    # ----------------------------------------------------------------------- #

    def _reconcile(self) -> Dict[str, Record]:
        latest = _read_json(self.latest_path, {})
        history = self._read_history()
        merged = {**latest, **history}
        if history and merged != latest:
            write_json_atomic(self.latest_path, merged)
        return merged

    def _read_history(self) -> Dict[str, Record]:
        out: Dict[str, Record] = {}
        for record in _iter_history(self.history_path):
            key = self._key(record)
            if _is_newer(record, out.get(key)):
                out[key] = record
        return out

    def _stamp(self) -> tuple:
        def one(path: Path):
            try:
                st = path.stat()
            except OSError:
                return None
            return st.st_mtime_ns, st.st_size

        return one(self.latest_path), one(self.history_path)

    @contextmanager
    def _lock(self) -> Iterator[None]:
        with (self.directory / ".lock").open("a+", encoding="utf-8") as fh:
            if os.name == "nt":
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
            else:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "nt":
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _is_newer(record: Record, current: Record | None) -> bool:
    """Latest = newest `timestamp`; ties (and records without one) go to the later write.
    Live writes always win; this matters when older records are merged in afterwards."""
    if current is None:
        return True
    new, old = record.get("timestamp"), current.get("timestamp")
    return not (isinstance(new, str) and isinstance(old, str) and new < old)


def _current_umask() -> int:
    mask = os.umask(0)
    os.umask(mask)
    return mask


_UMASK = _current_umask()


def write_json_atomic(path: Path, data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        # mkstemp makes the file 0600; give it the normal permissions (0644 under the
        # usual umask) so a container running as another user can still read it.
        os.chmod(tmp, 0o666 & ~_UMASK)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def read_json(path: Path, default: Any = None) -> Any:
    return _read_json(Path(path), default)


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (json.JSONDecodeError, OSError):
        logger.exception("Could not read %s", path)
        return default


def _iter_history(path: Path) -> Iterator[Record]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return
    for number, line in enumerate(lines, start=1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("Skipping invalid line %d in %s", number, path)
            continue
        if isinstance(record, dict):
            yield record
