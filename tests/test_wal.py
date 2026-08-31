import os
import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

import wal as wal_module  # noqa: E402
from wal import WAL  # noqa: E402


class WALTests(unittest.TestCase):
    def test_append_is_readable_and_rotates(self):
        with tempfile.TemporaryDirectory() as directory:
            wal = WAL(directory, segment_max_bytes=30).open()
            try:
                first = wal.append(1, b"a", b"one")
                second = wal.append(2, b"b", b"two")
            finally:
                wal.close()

            self.assertEqual(first.segment_id, 1)
            self.assertEqual(second.segment_id, 2)
            self.assertEqual(len(list(Path(directory, "data").glob("*.log"))), 2)
            self.assertGreater(os.path.getsize(Path(directory, "data", "segment-00000000000000000001.log")), 0)

    def test_batched_rotation_flushes_the_outgoing_segment(self):
        with tempfile.TemporaryDirectory() as directory:
            wal = WAL(directory, sync_per_write=False, segment_max_bytes=30).open()
            try:
                wal.append(1, b"a", b"one")
                with patch.object(wal_module.os, "fsync") as fsync:
                    wal.append(2, b"b", b"two")
                    self.assertGreaterEqual(fsync.call_count, 1)
            finally:
                wal.close()


if __name__ == "__main__":
    unittest.main()
