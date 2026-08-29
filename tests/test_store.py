import sys
import tempfile
from pathlib import Path
import unittest


sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from store import KVStore  # noqa: E402


class StoreTests(unittest.TestCase):
    def test_put_get_overwrite_and_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            store = KVStore.open(directory)
            try:
                self.assertIsNone(store.get(b"name"))
                store.put(b"name", b"first")
                self.assertEqual(store.get(b"name"), b"first")
                store.put(b"name", b"second")
                self.assertEqual(store.get(b"name"), b"second")
                self.assertTrue(store.delete(b"name"))
                self.assertIsNone(store.get(b"name"))
                self.assertFalse(store.delete(b"name"))
            finally:
                store.close()

    def test_context_manager_and_batched_flush(self):
        with tempfile.TemporaryDirectory() as directory:
            with KVStore.open(directory, sync_per_write=False) as store:
                store.put(b"key", b"value")
                store.flush()
                self.assertEqual(store.get(b"key"), b"value")

    def test_operations_reject_text_keys_and_values(self):
        with tempfile.TemporaryDirectory() as directory:
            with KVStore.open(directory) as store:
                with self.assertRaises(TypeError):
                    store.put("key", b"value")
                with self.assertRaises(TypeError):
                    store.put(b"key", "value")


if __name__ == "__main__":
    unittest.main()
