import tempfile
import sys
from pathlib import Path
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from store import KVStore  # noqa: E402


class AutomaticCompactionTests(unittest.TestCase):
    def test_default_behavior_remains_manual(self):
        with tempfile.TemporaryDirectory() as directory:
            with KVStore.open(directory, segment_max_bytes=45) as store:
                for number in range(4):
                    store.put(f"key-{number}".encode(), b"x" * 12)
                self.assertGreater(len(store._wal.segment_paths()), 1)

    def test_threshold_compacts_and_preserves_values(self):
        with tempfile.TemporaryDirectory() as directory:
            with KVStore.open(
                directory,
                segment_max_bytes=45,
                auto_compact_segments=2,
            ) as store:
                for number in range(8):
                    store.put(f"key-{number}".encode(), b"x" * 12)
                self.assertEqual(len(store._wal.segment_paths()), 1)
                for number in range(8):
                    self.assertEqual(store.get(f"key-{number}".encode()), b"x" * 12)

            with KVStore.open(directory) as store:
                for number in range(8):
                    self.assertEqual(store.get(f"key-{number}".encode()), b"x" * 12)

    def test_delete_can_trigger_automatic_compaction(self):
        with tempfile.TemporaryDirectory() as directory:
            with KVStore.open(
                directory,
                segment_max_bytes=45,
                auto_compact_segments=2,
            ) as store:
                for number in range(4):
                    store.put(f"key-{number}".encode(), b"x" * 12)
                self.assertTrue(store.delete(b"key-0"))
                self.assertIsNone(store.get(b"key-0"))
                self.assertEqual(store.get(b"key-3"), b"x" * 12)

    def test_failed_maintenance_does_not_undo_durable_write(self):
        with tempfile.TemporaryDirectory() as directory:
            with KVStore.open(
                directory,
                segment_max_bytes=45,
                auto_compact_segments=2,
            ) as store:
                store.put(b"first", b"x" * 12)
                with patch.object(store, "compact", side_effect=OSError("busy")):
                    store.put(b"second", b"x" * 12)
                self.assertEqual(store.get(b"second"), b"x" * 12)

    def test_threshold_must_allow_at_least_two_segments(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                KVStore.open(directory, auto_compact_segments=1)


if __name__ == "__main__":
    unittest.main()
