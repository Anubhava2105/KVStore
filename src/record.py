"""Encoding and validation for the on-disk KVStore record format."""

from __future__ import annotations

from dataclasses import dataclass
import struct
import zlib


HEADER = struct.Struct("<IQII")
HEADER_SIZE = HEADER.size
TOMBSTONE_LENGTH = 0xFFFFFFFF
MAX_FIELD_LENGTH = TOMBSTONE_LENGTH - 1


class RecordError(ValueError):
    """Raised when a record is malformed or fails its checksum."""


@dataclass(frozen=True)
class Record:
    sequence: int
    key: bytes
    value: bytes | None

    @property
    def is_tombstone(self) -> bool:
        return self.value is None


def _as_bytes(value: bytes | bytearray | memoryview, field: str) -> bytes:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise TypeError(f"{field} must be bytes-like")
    return bytes(value)


def encode(sequence: int, key: bytes | bytearray | memoryview,
           value: bytes | bytearray | memoryview | None) -> bytes:
    """Return one complete record, including its CRC32 header field."""
    if not isinstance(sequence, int) or not 0 <= sequence <= 0xFFFFFFFFFFFFFFFF:
        raise ValueError("sequence must fit in an unsigned 64-bit integer")
    key_bytes = _as_bytes(key, "key")
    if not key_bytes:
        raise RecordError("key must not be empty")
    if len(key_bytes) > MAX_FIELD_LENGTH:
        raise RecordError("key is too large")

    if value is None:
        value_bytes = b""
        value_length = TOMBSTONE_LENGTH
    else:
        value_bytes = _as_bytes(value, "value")
        if len(value_bytes) > MAX_FIELD_LENGTH:
            raise RecordError("value is too large")
        value_length = len(value_bytes)

    body = struct.pack("<QII", sequence, len(key_bytes), value_length)
    body += key_bytes + value_bytes
    checksum = zlib.crc32(body) & 0xFFFFFFFF
    return struct.pack("<I", checksum) + body


def decode(data: bytes | bytearray | memoryview) -> Record:
    """Decode exactly one complete record and verify its CRC32."""
    raw = bytes(data)
    if len(raw) < HEADER_SIZE:
        raise RecordError("record is shorter than its fixed-size header")
    checksum, sequence, key_length, value_length = HEADER.unpack_from(raw)
    if key_length == 0 or key_length > MAX_FIELD_LENGTH:
        raise RecordError("invalid key length")
    if value_length != TOMBSTONE_LENGTH and value_length > MAX_FIELD_LENGTH:
        raise RecordError("invalid value length")
    payload_length = key_length + (0 if value_length == TOMBSTONE_LENGTH else value_length)
    record_length = HEADER_SIZE + payload_length
    if len(raw) != record_length:
        raise RecordError("record has an unexpected length")
    body = raw[4:]
    if zlib.crc32(body) & 0xFFFFFFFF != checksum:
        raise RecordError("record CRC32 mismatch")
    key_start = HEADER_SIZE
    key_end = key_start + key_length
    value = None if value_length == TOMBSTONE_LENGTH else raw[key_end:]
    return Record(sequence, raw[key_start:key_end], value)


def record_length_from_header(header: bytes | bytearray | memoryview) -> int:
    """Return the total record length described by a complete header."""
    raw = bytes(header)
    if len(raw) != HEADER_SIZE:
        raise RecordError("header must be exactly 20 bytes")
    _, _, key_length, value_length = HEADER.unpack(raw)
    if key_length == 0 or key_length > MAX_FIELD_LENGTH:
        raise RecordError("invalid key length")
    if value_length != TOMBSTONE_LENGTH and value_length > MAX_FIELD_LENGTH:
        raise RecordError("invalid value length")
    return HEADER_SIZE + key_length + (
        0 if value_length == TOMBSTONE_LENGTH else value_length
    )
