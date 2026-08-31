"""Append-only write-ahead log with fsync-per-write durability."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import re

from record import encode
from sys_platform import acquire_lock, release_lock, sync_directory


SEGMENT_PATTERN = re.compile(r"segment-(\d+)\.log\Z")
TEMP_SEGMENT_PATTERN = re.compile(r"segment-(\d+)\.log\.tmp\Z")


@dataclass(frozen=True)
class AppendResult:
    segment_id: int
    offset: int
    length: int


def _write_all(fd: int, data: bytes) -> None:
    """Write all bytes, handling short os.write calls explicitly."""
    view = memoryview(data)
    written = 0
    while written < len(view):
        count = os.write(fd, view[written:])
        if count <= 0:
            raise OSError("os.write returned no progress")
        written += count


class WAL:
    """Own the active segment and the cooperative writer lock."""

    def __init__(self, root: str | os.PathLike[str], *, sync_per_write: bool = True,
                 segment_max_bytes: int = 64 * 1024 * 1024) -> None:
        if segment_max_bytes <= 0:
            raise ValueError("segment_max_bytes must be positive")
        self.root = Path(root)
        self.data_dir = self.root / "data"
        self.lock_path = self.root / "LOCK"
        self.sync_per_write = sync_per_write
        self.segment_max_bytes = segment_max_bytes
        self._active_fd: int | None = None
        self._active_segment_id: int | None = None

    def open(self) -> "WAL":
        self.root.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        segments = self._segment_ids()
        segment_id = segments[-1] if segments else 1
        self._open_segment(segment_id)
        return self

    def close(self) -> None:
        if self._active_fd is not None:
            self.sync()
            os.close(self._active_fd)
            self._active_fd = None
        self._active_segment_id = None

    def sync(self) -> None:
        """Flush the active segment when batched syncing is enabled."""
        if self._active_fd is None:
            raise RuntimeError("WAL is not open")
        os.fsync(self._active_fd)

    def _segment_ids(self) -> list[int]:
        ids = []
        for path in self.data_dir.iterdir() if self.data_dir.exists() else ():
            match = SEGMENT_PATTERN.fullmatch(path.name)
            if match and path.is_file():
                ids.append(int(match.group(1)))
        return sorted(ids)

    def segment_paths(self) -> list[Path]:
        return [self.data_dir / f"segment-{segment_id:020d}.log"
                for segment_id in self._segment_ids()]

    def temporary_segment_paths(self) -> list[Path]:
        paths = []
        for path in self.data_dir.iterdir() if self.data_dir.exists() else ():
            if TEMP_SEGMENT_PATTERN.fullmatch(path.name) and path.is_file():
                paths.append(path)
        return sorted(paths)

    def _open_segment(self, segment_id: int) -> None:
        if self._active_fd is not None:
            os.close(self._active_fd)
        path = self.data_dir / f"segment-{segment_id:020d}.log"
        self._active_fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_APPEND, 0o644)
        self._active_segment_id = segment_id

    def _rotate(self) -> None:
        assert self._active_segment_id is not None
        # Rotation closes the current descriptor. Sync it first so batched
        # writes remain durable after the segment changes.
        self.sync()
        self._open_segment(self._active_segment_id + 1)

    @property
    def active_segment_id(self) -> int:
        if self._active_segment_id is None:
            raise RuntimeError("WAL is not open")
        return self._active_segment_id

    def install_segment(self, temp_path: str | os.PathLike[str],
                        segment_id: int) -> Path:
        """Atomically install a compacted segment as the active segment."""
        if self._active_fd is None:
            raise RuntimeError("WAL is not open")
        target = self.data_dir / f"segment-{segment_id:020d}.log"
        old_segment_id = self.active_segment_id
        os.close(self._active_fd)
        self._active_fd = None
        try:
            os.replace(temp_path, target)
            self._open_segment(segment_id)
        except Exception:
            if self._active_fd is None:
                self._open_segment(segment_id if target.exists() else old_segment_id)
            raise
        return target

    @contextmanager
    def writer_lock(self):
        lock_fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            acquire_lock(lock_fd, exclusive=True)
            yield
        finally:
            try:
                release_lock(lock_fd)
            finally:
                os.close(lock_fd)

    @contextmanager
    def reader_lock(self):
        lock_fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            acquire_lock(lock_fd, exclusive=False)
            yield
        finally:
            try:
                release_lock(lock_fd)
            finally:
                os.close(lock_fd)

    def append(self, sequence: int, key: bytes, value: bytes | None) -> AppendResult:
        if self._active_fd is None or self._active_segment_id is None:
            raise RuntimeError("WAL is not open")
        encoded = encode(sequence, key, value)
        current_size = os.fstat(self._active_fd).st_size
        if current_size and current_size + len(encoded) > self.segment_max_bytes:
            self._rotate()
            current_size = os.fstat(self._active_fd).st_size
        offset = current_size
        _write_all(self._active_fd, encoded)
        if self.sync_per_write:
            os.fsync(self._active_fd)
        return AppendResult(self._active_segment_id, offset, len(encoded))
