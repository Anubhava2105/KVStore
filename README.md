# KVStore

KVStore is a durable, embedded local key-value store implemented with only
Python's standard library. It uses an append-only write-ahead log, an
in-memory key index, CRC32-protected records, crash recovery, and atomic
segment compaction.

## Quick start

```sh
make test
make deps-proof
make build
./kv.pyz put ./demo alpha value
./kv.pyz get ./demo alpha
./kv.pyz compact ./demo
```

The library API is bytes-oriented:

When running a script from the repository root, add `src` to Python's import
path first:

```sh
PYTHONPATH=src python3 your_script.py
```

```python
from store import KVStore

with KVStore.open("./demo") as store:
    store.put(b"alpha", b"value")
    assert store.get(b"alpha") == b"value"
    store.delete(b"alpha")
```

The CLI encodes its key and value arguments as UTF-8. `get` and `delete` return
exit code `1` when the key does not exist; argparse usage errors return `2`,
and operational failures return `3`.

## Durability model

Each normal `put` and `delete` writes a complete record, calls `os.fsync()` on
the active segment, and only then updates the in-memory index. A successful
operation has therefore handed its record to the operating system for durable
storage. Fsync per write slows writes compared with batching. Pass
`sync_per_write=False` to the API or `--batched` to the CLI for faster writes.
Call `flush()` before relying on batched writes after a crash. Data written
since the last flush is not protected.

Records contain a CRC32, sequence number, key length, value length, key, and
value. A delete is a tombstone record. Segment files live under `data/` and
are rotated by size.

## Recovery and compaction

When a process opens a store, it acquires the exclusive cooperative writer
lock. It scans segments in numeric creation order, verifies each record,
rebuilds the index, and resumes the sequence number after the highest valid
record. If it finds a torn header, torn payload, invalid lengths, or a CRC
mismatch, it truncates that segment at the last known-good offset and logs the
discarded suffix. Recovery reads the complete log on every startup, so startup
time grows with the amount of log data. A larger system would periodically
checkpoint its index.

`compact()` acquires the writer lock, takes a snapshot, and rewrites the live
records. It fsyncs the temporary segment, installs it with `os.replace()`, and
syncs the containing directory. It then removes the old segments, syncs the
directory again, and swaps the in-memory index. A crash before the rename
leaves the old segments untouched. A crash after the rename can leave both old
and new segments, and recovery can replay that state safely.

Compaction is manual by default. Applications can opt into a synchronous
segment-count policy with `KVStore.open(path, auto_compact_segments=8)` or the
CLI flag `--auto-compact-segments 8`. After a successful write, if the log has
at least the configured number of segments, the writer releases its operation
lock and starts compaction. Compaction adds write latency and disk I/O at the
trigger point, so the default remains manual. If it fails, the store retains
the already-durable write and logs the failure. A later write can try again.

## Platform and concurrency scope

The project keeps OS-specific behavior in `sys_platform.py`. The POSIX backend
uses `fcntl.flock` for shared and exclusive locking and fsyncs the data
directory after compaction. The project is developed and tested on POSIX
through Linux and WSL2. The Windows backend currently raises
`NotImplementedError`, so the project makes no Windows durability claim.

The single-writer guarantee is advisory. It applies only to cooperating clients
that use this library's lock file. It does not stop an arbitrary script from
opening a segment directly and writing without the lock.

## Known limitations

- The index stays in memory, so its size is limited by available RAM. An
  on-disk B-tree or SSTable index is out of scope.
- Recovery reads the complete log on startup and has no index checkpoint.
- Single-writer safety is advisory against arbitrary direct file access.
- Compaction is manual by default; automatic segment-count compaction is opt-in.
- POSIX is the tested platform. Windows support is not yet implemented.

## Project files

- `src/record.py`: binary record encoding, decoding, and checksums.
- `src/wal.py`: append-only WAL, fsync policy, rotation, and locking hooks.
- `src/index.py`: in-memory key-to-location index.
- `src/store.py`: public API, recovery, and compaction.
- `src/sys_platform.py`: the only OS-specific interface.
- `src/cli.py`: argparse command-line entrypoint.
- `deps-proof.txt`: generated stdlib-only import check.
