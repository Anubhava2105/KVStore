"""Public API for the embedded key-value store."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from index import Index, IndexEntry
from record import HEADER_SIZE, Record, RecordError, decode, encode, record_length_from_header
from wal import WAL, _write_all, sync_directory


LOGGER = logging.getLogger(__name__)


def _key_bytes(key: bytes | bytearray | memoryview) -> bytes:
    if not isinstance(key, (bytes, bytearray, memoryview)):
        raise TypeError("key must be bytes-like")
    result = bytes(key)
    if not result:
        raise ValueError("key must not be empty")
    return result


def _value_bytes(value: bytes | bytearray | memoryview) -> bytes:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise TypeError("value must be bytes-like")
    return bytes(value)


def _read_exact(fd: int, length: int) -> bytes:
    chunks = []
    remaining = length
    while remaining:
        chunk = os.read(fd, remaining)
        if not chunk:
            raise OSError("unexpected end of segment while reading a value")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


class KVStore:
    """A bytes-oriented, single-writer/multi-reader embedded KV store.

    ``put`` and ``delete`` are fsync-per-operation by default. Set
    ``sync_per_write=False`` to batch durability and call ``flush`` explicitly.
    Set ``auto_compact_segments`` to opt into synchronous compaction after the
    log reaches that many segments.
    Opening a store replays valid records and truncates any torn or corrupt
    suffix from each segment.
    """

    def __init__(self, wal: WAL, *, auto_compact_segments: int | None = None) -> None:
        if auto_compact_segments is not None and auto_compact_segments < 2:
            raise ValueError("auto_compact_segments must be at least 2")
        self._wal = wal
        self._index = Index()
        self._next_sequence = 1
        self._replayed_offsets: dict[int, int] = {}
        self._known_segment_ids: set[int] = set()
        self._auto_compact_segments = auto_compact_segments
        self._closed = False

    @classmethod
    def open(cls, path: str | os.PathLike[str], *, sync_per_write: bool = True,
             segment_max_bytes: int = 64 * 1024 * 1024,
             auto_compact_segments: int | None = None) -> "KVStore":
        wal = WAL(path, sync_per_write=sync_per_write,
                  segment_max_bytes=segment_max_bytes).open()
        store = cls(wal, auto_compact_segments=auto_compact_segments)
        try:
            with wal.writer_lock():
                store._recover()
        except Exception:
            wal.close()
            raise
        return store

    @property
    def path(self) -> Path:
        return self._wal.root

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("KVStore is closed")

    def _recover(self) -> None:
        """Replay segments and discard each segment's invalid suffix."""
        self._cleanup_stale_temporary_segments()
        self._index = Index()
        self._replayed_offsets = {}
        highest_sequence = 0
        for segment_id, segment in self._segments_in_order():
            offset, segment_highest = self._scan_segment(
                segment_id, segment, self._index, truncate_invalid=True
            )
            self._replayed_offsets[segment_id] = offset
            highest_sequence = max(highest_sequence, segment_highest)
        self._known_segment_ids = set(self._replayed_offsets)
        self._next_sequence = highest_sequence + 1

    def _segments_in_order(self):
        for segment in self._wal.segment_paths():
            segment_id = int(segment.stem.split("-")[1])
            yield segment_id, segment

    @staticmethod
    def _discard_suffix(fd: int, segment: Path, offset: int,
                        file_size: int, reason: str) -> None:
        discarded = file_size - offset
        os.ftruncate(fd, offset)
        os.fsync(fd)
        LOGGER.warning("discarded %d bytes from %s at offset %d: %s",
                       discarded, segment, offset, reason)

    def _cleanup_stale_temporary_segments(self) -> None:
        removed = self._wal.temporary_segment_paths()
        for segment in removed:
            segment.unlink()
            LOGGER.warning("discarded stale compaction temporary segment %s",
                           segment)
        if removed:
            sync_directory(str(self._wal.data_dir))

    @staticmethod
    def _apply_record(index: Index, segment_id: int, offset: int,
                      length: int, record: Record) -> None:
        entry = IndexEntry(segment_id, offset, length)
        if record.value is None:
            index.remove(record.key)
        else:
            index.set(record.key, entry)

    def _scan_segment(self, segment_id: int, segment: Path, index: Index,
                      *, start_offset: int = 0,
                      truncate_invalid: bool = False) -> tuple[int, int]:
        flags = os.O_RDWR if truncate_invalid else os.O_RDONLY
        fd = os.open(segment, flags)
        try:
            offset = start_offset
            highest_sequence = 0
            file_size = os.fstat(fd).st_size
            while offset < file_size:
                os.lseek(fd, offset, os.SEEK_SET)
                header = os.read(fd, HEADER_SIZE)
                if len(header) != HEADER_SIZE:
                    if truncate_invalid:
                        self._discard_suffix(fd, segment, offset, file_size,
                                             "torn header")
                    break
                try:
                    length = record_length_from_header(header)
                except RecordError as error:
                    if truncate_invalid:
                        self._discard_suffix(fd, segment, offset, file_size,
                                             str(error))
                    break
                if file_size - offset < length:
                    if truncate_invalid:
                        self._discard_suffix(fd, segment, offset, file_size,
                                             "torn payload")
                    break
                try:
                    payload = _read_exact(fd, length - HEADER_SIZE)
                    record = decode(header + payload)
                except (OSError, RecordError) as error:
                    if truncate_invalid:
                        self._discard_suffix(fd, segment, offset, file_size,
                                             str(error))
                    break
                self._apply_record(index, segment_id, offset, length, record)
                highest_sequence = max(highest_sequence, record.sequence)
                offset += length
            return offset, highest_sequence
        finally:
            os.close(fd)

    def _rebuild_index_read_only(self, segment_paths: list[Path]) -> None:
        new_index = Index()
        new_offsets: dict[int, int] = {}
        highest_sequence = 0
        for segment in segment_paths:
            segment_id = int(segment.stem.split("-")[1])
            offset, segment_highest = self._scan_segment(
                segment_id, segment, new_index
            )
            new_offsets[segment_id] = offset
            highest_sequence = max(highest_sequence, segment_highest)
        self._index = new_index
        self._replayed_offsets = new_offsets
        self._known_segment_ids = set(new_offsets)
        self._next_sequence = max(self._next_sequence, highest_sequence + 1)

    def _refresh_index(self) -> None:
        """Replay records added by cooperating writers since the last read."""
        segment_paths = self._wal.segment_paths()
        segment_ids = {
            int(segment.stem.split("-")[1]) for segment in segment_paths
        }
        if segment_ids != self._known_segment_ids:
            self._rebuild_index_read_only(segment_paths)
            return

        for segment in segment_paths:
            segment_id = int(segment.stem.split("-")[1])
            known_offset = self._replayed_offsets.get(segment_id, 0)
            file_size = segment.stat().st_size
            if file_size < known_offset:
                self._rebuild_index_read_only(segment_paths)
                return
            if file_size > known_offset:
                offset, highest_sequence = self._scan_segment(
                    segment_id, segment, self._index,
                    start_offset=known_offset,
                )
                self._replayed_offsets[segment_id] = offset
                self._next_sequence = max(self._next_sequence,
                                          highest_sequence + 1)

    def _maybe_auto_compact(self) -> None:
        threshold = self._auto_compact_segments
        if threshold is None or len(self._wal.segment_paths()) < threshold:
            return
        try:
            self.compact()
        except (OSError, RuntimeError) as error:
            # The WAL append succeeded, so keep the durable write visible. If
            # compaction fails, leave maintenance for a later operation.
            LOGGER.warning("automatic compaction deferred: %s", error)

    def _read_entry(self, key: bytes, entry: IndexEntry) -> Record:
        segment = self._wal.data_dir / f"segment-{entry.segment_id:020d}.log"
        fd = os.open(segment, os.O_RDONLY)
        try:
            os.lseek(fd, entry.offset, os.SEEK_SET)
            record = decode(_read_exact(fd, entry.length))
        finally:
            os.close(fd)
        if record.key != key or record.value is None:
            raise RuntimeError("index points to an unexpected record")
        return record

    def put(self, key: bytes | bytearray | memoryview,
            value: bytes | bytearray | memoryview) -> None:
        self._ensure_open()
        key_data = _key_bytes(key)
        value_data = _value_bytes(value)
        with self._wal.writer_lock():
            self._refresh_index()
            result = self._wal.append(self._next_sequence, key_data, value_data)
            self._index.set(key_data, IndexEntry(result.segment_id,
                                                 result.offset, result.length))
            self._replayed_offsets[result.segment_id] = result.offset + result.length
            self._known_segment_ids.add(result.segment_id)
            self._next_sequence += 1
        self._maybe_auto_compact()

    def get(self, key: bytes | bytearray | memoryview) -> bytes | None:
        self._ensure_open()
        key_data = _key_bytes(key)
        with self._wal.reader_lock():
            self._refresh_index()
            entry = self._index.get(key_data)
            if entry is None:
                return None
            return self._read_entry(key_data, entry).value

    def delete(self, key: bytes | bytearray | memoryview) -> bool:
        self._ensure_open()
        key_data = _key_bytes(key)
        with self._wal.writer_lock():
            self._refresh_index()
            existed = self._index.get(key_data) is not None
            result = self._wal.append(self._next_sequence, key_data, None)
            self._index.remove(key_data)
            self._replayed_offsets[result.segment_id] = result.offset + result.length
            self._known_segment_ids.add(result.segment_id)
            self._next_sequence += 1
        self._maybe_auto_compact()
        return existed

    def compact(self) -> int:
        """Rewrite live records into one durable segment.

        The writer lock is held for the snapshot, rewrite, rename, cleanup,
        and index swap. A crash before the rename leaves the old segments
        untouched; a crash after it leaves a complete replacement segment
        that recovery can replay.
        """
        self._ensure_open()
        with self._wal.writer_lock():
            self._cleanup_stale_temporary_segments()
            self._refresh_index()
            snapshot = self._index.snapshot()
            old_segments = self._wal.segment_paths()
            next_segment_id = (max(
                [self._wal.active_segment_id]
                + [int(path.stem.split("-")[1]) for path in old_segments]
            ) + 1)
            temp_path = self._wal.data_dir / (
                f"segment-{next_segment_id:020d}.log.tmp"
            )
            new_entries: dict[bytes, IndexEntry] = {}
            fd = os.open(temp_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            try:
                offset = 0
                for key, entry in sorted(snapshot.items()):
                    record = self._read_entry(key, entry)
                    encoded = encode(record.sequence, record.key, record.value)
                    _write_all(fd, encoded)
                    new_entries[key] = IndexEntry(next_segment_id, offset,
                                                  len(encoded))
                    offset += len(encoded)
                os.fsync(fd)
            finally:
                os.close(fd)

            self._wal.install_segment(temp_path, next_segment_id)
            sync_directory(str(self._wal.data_dir))
            for segment in old_segments:
                if segment.exists():
                    segment.unlink()
            sync_directory(str(self._wal.data_dir))
            self._index.replace(new_entries)
            self._replayed_offsets = {next_segment_id: offset}
            self._known_segment_ids = {next_segment_id}
            return len(new_entries)

    def flush(self) -> None:
        self._ensure_open()
        self._wal.sync()

    def close(self) -> None:
        if not self._closed:
            self._wal.close()
            self._closed = True

    def __enter__(self) -> "KVStore":
        self._ensure_open()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
