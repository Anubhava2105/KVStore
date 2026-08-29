import sys
from pathlib import Path
import unittest


sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from record import HEADER_SIZE, RecordError, decode, encode  # noqa: E402


class RecordTests(unittest.TestCase):
    def test_round_trip_value(self):
        record = decode(encode(42, b"alpha", b"value"))
        self.assertEqual((record.sequence, record.key, record.value),
                         (42, b"alpha", b"value"))

    def test_round_trip_tombstone(self):
        record = decode(encode(43, b"alpha", None))
        self.assertTrue(record.is_tombstone)

    def test_checksum_rejects_corruption(self):
        raw = bytearray(encode(1, b"key", b"value"))
        raw[-1] ^= 0xFF
        with self.assertRaises(RecordError):
            decode(raw)

    def test_short_header_is_rejected(self):
        with self.assertRaises(RecordError):
            decode(b"\0" * (HEADER_SIZE - 1))


if __name__ == "__main__":
    unittest.main()
