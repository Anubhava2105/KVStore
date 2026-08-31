import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest


SRC = Path(__file__).parents[1] / "src"
sys.path.insert(0, str(SRC))

from store import KVStore  # noqa: E402


CRASH_COMPACTION_SCRIPT = r'''
import os
import signal
import sys

sys.path.insert(0, sys.argv[2])
import store as store_module

real_sync_directory = store_module.sync_directory

def sync_then_kill(path):
    real_sync_directory(path)
    os.kill(os.getpid(), signal.SIGKILL)

store_module.sync_directory = sync_then_kill
store = store_module.KVStore.open(sys.argv[1])
store.compact()
'''


CRASH_BEFORE_RENAME_SCRIPT = r'''
import os
import signal
import sys

sys.path.insert(0, sys.argv[2])
from store import KVStore

store = KVStore.open(sys.argv[1])

def kill_before_install(temp_path, segment_id):
    os.kill(os.getpid(), signal.SIGKILL)

store._wal.install_segment = kill_before_install
store.compact()
'''


class CompactionTests(unittest.TestCase):
    def test_compaction_keeps_live_data_and_removes_old_segments(self):
        with tempfile.TemporaryDirectory() as directory:
            with KVStore.open(directory, segment_max_bytes=45) as store:
                for number in range(12):
                    store.put(f"key-{number}".encode(), b"x" * 12)
                store.put(b"key-3", b"latest")
                self.assertTrue(store.delete(b"key-7"))
                live_count = store.compact()
                self.assertEqual(live_count, 11)
                self.assertEqual(store.get(b"key-3"), b"latest")
                self.assertIsNone(store.get(b"key-7"))
                self.assertEqual(len(store._wal.segment_paths()), 1)
                self.assertFalse(list(Path(directory, "data").glob("*.tmp")))

            with KVStore.open(directory, segment_max_bytes=45) as store:
                self.assertEqual(store.get(b"key-3"), b"latest")
                self.assertIsNone(store.get(b"key-7"))
                store.put(b"after-compact", b"works")

            with KVStore.open(directory) as store:
                self.assertEqual(store.get(b"after-compact"), b"works")

    def test_crash_after_compaction_rename_recovers_from_new_segment(self):
        with tempfile.TemporaryDirectory() as directory:
            with KVStore.open(directory, segment_max_bytes=45) as store:
                for number in range(12):
                    store.put(f"key-{number}".encode(), b"x" * 12)

            result = subprocess.run(
                [sys.executable, "-c", CRASH_COMPACTION_SCRIPT,
                 directory, str(SRC)],
                check=False,
            )
            self.assertEqual(result.returncode, -signal.SIGKILL)

            with KVStore.open(directory) as store:
                for number in range(12):
                    self.assertEqual(store.get(f"key-{number}".encode()), b"x" * 12)
                self.assertGreater(len(store._wal.segment_paths()), 1)
                self.assertFalse(list(Path(directory, "data").glob("*.tmp")))

    def test_crash_before_rename_cleans_temp_and_allows_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            with KVStore.open(directory, segment_max_bytes=45) as store:
                for number in range(12):
                    store.put(f"key-{number}".encode(), b"x" * 12)

            result = subprocess.run(
                [sys.executable, "-c", CRASH_BEFORE_RENAME_SCRIPT,
                 directory, str(SRC)],
                check=False,
            )
            self.assertEqual(result.returncode, -signal.SIGKILL)
            self.assertTrue(list(Path(directory, "data").glob("*.tmp")))

            with KVStore.open(directory) as store:
                self.assertFalse(list(Path(directory, "data").glob("*.tmp")))
                self.assertEqual(store.get(b"key-3"), b"x" * 12)
                store.compact()
                self.assertEqual(len(store._wal.segment_paths()), 1)

    def test_stale_writer_refreshes_before_compaction(self):
        with tempfile.TemporaryDirectory() as directory:
            stale = KVStore.open(directory)
            try:
                with KVStore.open(directory) as writer:
                    writer.put(b"written-later", b"preserve-me")
                stale.compact()
            finally:
                stale.close()

            with KVStore.open(directory) as store:
                self.assertEqual(store.get(b"written-later"), b"preserve-me")


if __name__ == "__main__":
    unittest.main()
