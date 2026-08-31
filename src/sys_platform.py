"""Small OS boundary for locking and directory durability."""

from __future__ import annotations

import os
import sys


if sys.platform == "win32":
    def acquire_lock(fd: int, exclusive: bool) -> None:
        raise NotImplementedError(
            "Windows support not yet implemented - see README"
        )


    def release_lock(fd: int) -> None:
        raise NotImplementedError(
            "Windows support not yet implemented - see README"
        )


    def sync_directory(path: str) -> None:
        raise NotImplementedError(
            "Windows support not yet implemented - see README"
        )
else:
    import fcntl

    def acquire_lock(fd: int, exclusive: bool) -> None:
        operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        fcntl.flock(fd, operation)


    def release_lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


    def sync_directory(path: str) -> None:
        directory_fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
