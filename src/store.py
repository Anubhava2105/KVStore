"""Public API for the embedded key-value store."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from index import Index, IndexEntry
from record import HEADER_SIZE, RecordError, decode, record_length_from_header
from wal import WAL


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
    Opening a store replays valid records and truncates any torn or corrupt
    suffix from each segment.
    """

    def __init__(self, wal: WAL) -> None:
        self._wal = wal
        self._index = Index()
        self._next_sequence = 1
        self._closed = False

    @classmethod
    def open(cls, path: str | os.PathLike[str], *, sync_per_write: bool = True,
             segment_max_bytes: int = 64 * 1024 * 1024) -> "KVStore":
        wal = WAL(path, sync_per_write=sync_per_write,
                  segment_max_bytes=segment_max_bytes).open()
        store = cls(wal)
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
        highest_sequence = 0
        for segment_id, segment in self._segments_in_order():
            fd = os.open(segment, os.O_RDWR)
            try:
                offset = 0
                file_size = os.fstat(fd).st_size
                while offset < file_size:
                    os.lseek(fd, offset, os.SEEK_SET)
                    header = os.read(fd, HEADER_SIZE)
                    if len(header) != HEADER_SIZE:
                        self._discard_suffix(fd, segment, offset, file_size,
                                             "torn header")
                        break
                    try:
                        length = record_length_from_header(header)
                    except RecordError as error:
                        self._discard_suffix(fd, segment, offset, file_size,
                                             str(error))
                        break
                    if file_size - offset < length:
                        self._discard_suffix(fd, segment, offset, file_size,
                                             "torn payload")
                        break
                    try:
                        payload = _read_exact(fd, length - HEADER_SIZE)
                        record = decode(header + payload)
                    except (OSError, RecordError) as error:
                        self._discard_suffix(fd, segment, offset, file_size,
                                             str(error))
                        break

                    entry = IndexEntry(segment_id, offset, length)
                    if record.value is None:
                        self._index.remove(record.key)
                    else:
                        self._index.set(record.key, entry)
                    highest_sequence = max(highest_sequence, record.sequence)
                    offset += length
            finally:
                os.close(fd)
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

    def put(self, key: bytes | bytearray | memoryview,
            value: bytes | bytearray | memoryview) -> None:
        self._ensure_open()
        key_data = _key_bytes(key)
        value_data = _value_bytes(value)
        with self._wal.writer_lock():
            result = self._wal.append(self._next_sequence, key_data, value_data)
            self._index.set(key_data, IndexEntry(result.segment_id,
                                                 result.offset, result.length))
            self._next_sequence += 1

    def get(self, key: bytes | bytearray | memoryview) -> bytes | None:
        self._ensure_open()
        key_data = _key_bytes(key)
        with self._wal.reader_lock():
            entry = self._index.get(key_data)
            if entry is None:
                return None
            segment = self._wal.data_dir / f"segment-{entry.segment_id:020d}.log"
            fd = os.open(segment, os.O_RDONLY)
            try:
                os.lseek(fd, entry.offset, os.SEEK_SET)
                record = decode(_read_exact(fd, entry.length))
            finally:
                os.close(fd)
        if record.key != key_data or record.value is None:
            raise RuntimeError("index points to an unexpected record")
        return record.value

    def delete(self, key: bytes | bytearray | memoryview) -> bool:
        self._ensure_open()
        key_data = _key_bytes(key)
        with self._wal.writer_lock():
            existed = self._index.get(key_data) is not None
            result = self._wal.append(self._next_sequence, key_data, None)
            self._index.remove(key_data)
            self._next_sequence += 1
            return existed

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
