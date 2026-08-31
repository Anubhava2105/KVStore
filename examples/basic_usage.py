"""A small Python application backed by KVStore."""

from store import KVStore


DATABASE_PATH = "./demo"


def text_value(store: KVStore, key: bytes) -> str:
    value = store.get(key)
    return "<missing>" if value is None else value.decode("utf-8")


def main() -> None:
    with KVStore.open(DATABASE_PATH) as store:
        store.put(b"app:name", b"KVStore demo")
        store.put(b"app:status", b"ready")

        print("name:", text_value(store, b"app:name"))
        print("status:", text_value(store, b"app:status"))

        store.delete(b"app:status")
        print("status after delete:", text_value(store, b"app:status"))


if __name__ == "__main__":
    main()
