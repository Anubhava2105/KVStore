"""Command-line entrypoint for KVStore."""

from __future__ import annotations

import argparse
import logging
import sys

from store import KVStore


EXIT_OK = 0
EXIT_NOT_FOUND = 1
EXIT_USAGE = 2
EXIT_ERROR = 3


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Crash-safe embedded key-value store")
    parser.add_argument(
        "--batched",
        action="store_true",
        help="defer fsync until the command closes the store (faster, less durable)",
    )
    parser.add_argument(
        "--auto-compact-segments",
        type=int,
        metavar="N",
        help="compact automatically after the log reaches N segments",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    put = commands.add_parser("put", help="store a UTF-8 value")
    put.add_argument("store")
    put.add_argument("key")
    put.add_argument("value")

    get = commands.add_parser("get", help="print a value as UTF-8 bytes")
    get.add_argument("store")
    get.add_argument("key")

    delete = commands.add_parser("delete", help="delete a key")
    delete.add_argument("store")
    delete.add_argument("key")

    compact = commands.add_parser("compact", help="rewrite live records")
    compact.add_argument("store")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    sync_per_write = not args.batched
    try:
        with KVStore.open(
            args.store,
            sync_per_write=sync_per_write,
            auto_compact_segments=args.auto_compact_segments,
        ) as store:
            if args.command == "put":
                store.put(args.key.encode("utf-8"), args.value.encode("utf-8"))
                return EXIT_OK
            if args.command == "get":
                value = store.get(args.key.encode("utf-8"))
                if value is None:
                    print("key not found", file=sys.stderr)
                    return EXIT_NOT_FOUND
                sys.stdout.buffer.write(value + b"\n")
                return EXIT_OK
            if args.command == "delete":
                return EXIT_OK if store.delete(args.key.encode("utf-8")) else EXIT_NOT_FOUND
            if args.command == "compact":
                print(store.compact())
                return EXIT_OK
    except (OSError, TypeError, ValueError, NotImplementedError) as error:
        logging.error("%s", error)
        return EXIT_ERROR
    return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
