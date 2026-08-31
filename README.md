# KVStore

KVStore is a durable, embedded local key-value store implemented with only
Python’s standard library. It uses an append-only write-ahead log, an
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
the active segment, and only then updates the in-memory index. This is the
durability boundary: when the operation returns successfully, the record has
been handed to the operating system for durable storage. The tradeoff is
throughput—fsync per write is slower than batching. Pass
`sync_per_write=False` to the API or `--batched` to the CLI for faster writes,
then call `flush()`; data written since the last flush is intentionally not
protected from a crash.

Records contain a CRC32, sequence number, key length, value length, key, and
value. A delete is a tombstone record. Segment files live under `data/` and
are rotated by size.

## Recovery and compaction

Opening a store takes the exclusive cooperative writer lock, scans segments in
numeric creation order, verifies every record, rebuilds the index, and resumes
the sequence number after the highest valid record. If it sees a torn header,
torn payload, invalid lengths, or a CRC mismatch, it truncates that segment at
the last known-good offset and logs the discarded suffix. Recovery is `O(log
size)` because the complete log is replayed on every startup; a larger system
would periodically checkpoint its index.

`compact()` takes the writer lock for its snapshot and rewrite. It writes live
records to a temporary segment, fsyncs it, atomically installs it with
`os.replace()`, syncs the containing directory, removes old segments, syncs
the directory again, and finally swaps the in-memory index. A crash before
rename leaves old segments untouched; a crash after rename can leave both old
and new segments, and recovery can safely replay that state.

Compaction is currently manual. The shipped policy does not include an
automatic size trigger yet; this is a scoped follow-up.

## Platform and concurrency scope

OS-specific behavior is isolated in `sys_platform.py`. The POSIX backend uses
`fcntl.flock` for shared/exclusive locking and fsyncs the data directory after
compaction. The project is developed and tested on POSIX through Linux/WSL2.
Windows support is a scoped follow-up, not a redesign; its current backend
raises `NotImplementedError` rather than making an untested durability claim.

The single-writer guarantee is advisory and applies only to cooperating
clients that use this library’s lock file. It does not prevent an arbitrary
script from opening a segment directly and writing without the lock.

## Known limitations

- The index is entirely in memory and therefore RAM-bound. An on-disk B-tree
  or SSTable index is out of scope.
- Recovery replays the complete log on startup and has no index checkpoint.
- Single-writer safety is advisory against arbitrary direct file access.
- Compaction is manual rather than automatically triggered by segment size.
- POSIX is the tested platform; Windows support is not yet implemented.

## Project files

- `src/record.py`: binary record encoding, decoding, and checksums.
- `src/wal.py`: append-only WAL, fsync policy, rotation, and locking hooks.
- `src/index.py`: in-memory key-to-location index.
- `src/store.py`: public API, recovery, and compaction.
- `src/sys_platform.py`: the only OS-specific interface.
- `src/cli.py`: argparse command-line entrypoint.
- `deps-proof.txt`: generated stdlib-only import check.
