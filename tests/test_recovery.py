import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest


SRC = Path(__file__).parents[1] / "src"
sys.path.insert(0, str(SRC))

from record import encode  # noqa: E402
from store import KVStore  # noqa: E402


CRASH_SCRIPT = r'''
import os
import signal
import sys

sys.path.insert(0, sys.argv[3])
from record import encode
from store import KVStore

store = KVStore.open(sys.argv[1])
store.put(b"stable", b"survives")
segment = store._wal.segment_paths()[0]
raw = bytearray(encode(2, b"torn", b"payload"))
mode = sys.argv[2]
if mode == "header":
    raw = raw[:7]
elif mode == "payload":
    raw = raw[:23]
elif mode == "crc":
    raw[-1] ^= 0xFF
fd = os.open(segment, os.O_WRONLY | os.O_APPEND)
try:
    os.write(fd, raw)
    os.fsync(fd)
finally:
    os.close(fd)
os.kill(os.getpid(), signal.SIGKILL)
'''


class RecoveryTests(unittest.TestCase):
    def test_replays_latest_values_and_tombstones(self):
        with tempfile.TemporaryDirectory() as directory:
            with KVStore.open(directory) as store:
                store.put(b"key", b"old")
                store.put(b"key", b"new")
                store.put(b"removed", b"value")
                self.assertTrue(store.delete(b"removed"))

            with KVStore.open(directory) as store:
                self.assertEqual(store.get(b"key"), b"new")
                self.assertIsNone(store.get(b"removed"))
                store.put(b"after-recovery", b"works")

            with KVStore.open(directory) as store:
                self.assertEqual(store.get(b"after-recovery"), b"works")

    def test_recovery_discards_each_kind_of_bad_suffix(self):
        for mode in ("header", "payload", "crc"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                result = subprocess.run(
                    [sys.executable, "-c", CRASH_SCRIPT, directory, mode, str(SRC)],
                    check=False,
                )
                self.assertEqual(result.returncode, -signal.SIGKILL)
                with KVStore.open(directory) as store:
                    self.assertEqual(store.get(b"stable"), b"survives")
                    self.assertIsNone(store.get(b"torn"))
                    store.put(b"after-crash", b"usable")

    def test_recovery_is_rerunnable_after_truncation(self):
        with tempfile.TemporaryDirectory() as directory:
            segment = Path(directory) / "data" / "segment-00000000000000000001.log"
            with KVStore.open(directory) as store:
                store.put(b"stable", b"survives")
                raw = encode(2, b"torn", b"payload")[:5]
                fd = os.open(segment, os.O_WRONLY | os.O_APPEND)
                try:
                    os.write(fd, raw)
                    os.fsync(fd)
                finally:
                    os.close(fd)

            with KVStore.open(directory) as store:
                self.assertEqual(store.get(b"stable"), b"survives")
            with KVStore.open(directory) as store:
                self.assertEqual(store.get(b"stable"), b"survives")
                self.assertIsNone(store.get(b"torn"))


if __name__ == "__main__":
    unittest.main()
