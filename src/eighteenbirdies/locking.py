"""Local advisory locks using only platform standard libraries."""

from contextlib import contextmanager
import errno
import os
from pathlib import Path
import stat
import time


@contextmanager
def file_lock(path: Path):
    """Serialize local imports; reject symlink and non-regular lock files."""
    if path.is_symlink():
        raise OSError("Store lock must be a regular file")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError("Store lock must be a regular file")
        deadline = time.monotonic() + 30
        if os.name == "nt":
            import msvcrt

            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
            while True:
                os.lseek(descriptor, 0, os.SEEK_SET)
                try:
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise OSError("Timed out waiting for the local store lock") from None
                    time.sleep(0.05)
        else:
            import fcntl

            os.fchmod(descriptor, 0o600)
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as error:
                    if error.errno not in (errno.EACCES, errno.EAGAIN):
                        raise
                    if time.monotonic() >= deadline:
                        raise OSError("Timed out waiting for the local store lock") from None
                    time.sleep(0.05)
        yield
    finally:
        os.close(descriptor)
