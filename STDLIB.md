# Standard-library substitutions

KVStore deliberately has no runtime dependencies outside Python’s standard
library. The implementation uses these standard modules in place of common
third-party conveniences:

1. `zlib.crc32` replaces `crc32c` or `fastcrc` packages for record checksums.
2. `struct.pack` and `struct.unpack` replace `construct` or `bitstruct` for the binary format.
3. `fcntl.flock` behind `sys_platform.py` replaces `portalocker` or `filelock`.
4. `os.write` and `os.fsync` replace buffered storage helpers on the durability path.
5. `os.replace` replaces `atomicwrites` for atomic compaction installation.
6. `argparse` replaces `click` or `typer` for the CLI.
7. `sys.stdlib_module_names` replaces `pipdeptree` or `pip-licenses` for dependency proof.
8. `ast` replaces an import-analysis package in the dependency proof script.
9. `pathlib`, `re`, and filesystem primitives replace globbing and path packages.
10. `unittest` and `subprocess` replace pytest and process-test helpers.
11. `contextlib.contextmanager` and `dataclasses` provide lifecycle and data-object abstractions.

The Windows branch remains an explicit `NotImplementedError`. The POSIX
backend is the tested implementation for Linux, WSL2, and macOS development.
