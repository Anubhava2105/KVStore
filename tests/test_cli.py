import contextlib
import io
import sys
from pathlib import Path
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from cli import EXIT_NOT_FOUND, EXIT_OK, main  # noqa: E402
from store import KVStore  # noqa: E402


class CLITests(unittest.TestCase):
    def test_put_get_delete_exit_codes(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(main(["put", directory, "key", "value"]), EXIT_OK)
            with KVStore.open(directory) as store:
                self.assertEqual(store.get(b"key"), b"value")
            self.assertEqual(main(["delete", directory, "key"]), EXIT_OK)
            self.assertEqual(main(["get", directory, "key"]), EXIT_NOT_FOUND)
            self.assertEqual(main(["delete", directory, "key"]), EXIT_NOT_FOUND)

    def test_compact_command(self):
        with tempfile.TemporaryDirectory() as directory:
            main(["put", directory, "key", "value"])
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["compact", directory]), EXIT_OK)
            self.assertEqual(output.getvalue().strip(), "1")


if __name__ == "__main__":
    unittest.main()
