"""In-memory key-to-record-location index."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IndexEntry:
    segment_id: int
    offset: int
    length: int


class Index:
    """Map byte keys to their latest durable record location."""

    def __init__(self) -> None:
        self._entries: dict[bytes, IndexEntry] = {}

    def get(self, key: bytes) -> IndexEntry | None:
        return self._entries.get(key)

    def set(self, key: bytes, entry: IndexEntry) -> None:
        self._entries[key] = entry

    def remove(self, key: bytes) -> bool:
        return self._entries.pop(key, None) is not None

    def snapshot(self) -> dict[bytes, IndexEntry]:
        return self._entries.copy()

    def __len__(self) -> int:
        return len(self._entries)
