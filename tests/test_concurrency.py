from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


SRC = Path(__file__).parents[1] / "src"
sys.path.insert(0, str(SRC))

from store import KVStore  # noqa: E402


WRITER_SCRIPT = r'''
import sys
sys.path.insert(0, sys.argv[4])
from store import KVStore

with KVStore.open(sys.argv[1]) as store:
    for number in range(int(sys.argv[2]), int(sys.argv[3])):
        store.put(f"key-{number}".encode(), b"value")
'''


READER_SCRIPT = r'''
from pathlib import Path
import sys
import time

sys.path.insert(0, sys.argv[2])
from store import KVStore

with KVStore.open(sys.argv[1]) as store:
    Path(sys.argv[3]).write_text("ready", encoding="ascii")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if store.get(b"shared") == b"updated":
            raise SystemExit(0)
        time.sleep(0.01)
raise SystemExit(1)
'''


class ConcurrencyTests(unittest.TestCase):
    def test_cooperating_writers_are_serialized(self):
        with tempfile.TemporaryDirectory() as directory:
            processes = [
                subprocess.Popen([
                    sys.executable, "-c", WRITER_SCRIPT, directory,
                    str(start), str(start + 10), str(SRC),
                ])
                for start in range(0, 40, 10)
            ]
            for process in processes:
                self.assertEqual(process.wait(), 0)

            with KVStore.open(directory) as store:
                for number in range(40):
                    self.assertEqual(store.get(f"key-{number}".encode()), b"value")

    def test_reader_opened_earlier_sees_a_cooperating_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            with KVStore.open(directory):
                pass
            ready = str(Path(directory) / "reader-ready")
            reader = subprocess.Popen([
                sys.executable, "-c", READER_SCRIPT, directory, str(SRC), ready,
            ])
            deadline = time.monotonic() + 5
            while not Path(ready).exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(Path(ready).exists())

            with KVStore.open(directory) as writer:
                writer.put(b"shared", b"updated")
            self.assertEqual(reader.wait(), 0)


if __name__ == "__main__":
    unittest.main()
