"""Public API for the embedded key-value store."""

from __future__ import annotations

import os
from pathlib import Path

from index import Index, IndexEntry
from record import decode
from wal import WAL


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
    Recovery is added in the next implementation stage.
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
        return cls(wal)

    @property
    def path(self) -> Path:
        return self._wal.root

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("KVStore is closed")

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
