"""Process-wide serialization for release pointer mutations.

The lock file is deliberately persistent.  Removing it after unlocking would
allow two processes to lock different filesystem objects during a delete/open
race.  A held OS lock disappears automatically when its process exits.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import stat
from typing import Iterator

_LOCK_FILE = ".atlas-update.lock"


class UpdateLockError(RuntimeError):
    """The installation lock is unsafe, unavailable or already held."""


def _lock(fd: int) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        raise UpdateLockError("another update operation holds the installation lock") from exc


def _unlock(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def exclusive_update_lock(root: Path) -> Iterator[None]:
    """Hold the installation's non-blocking, cross-process update lock.

    The caller must already have established the trusted installation root and
    its ACL policy.  This primitive refuses symlinked/non-regular lock files,
    but does not claim that the surrounding directory permissions are safe.
    """
    if not isinstance(root, Path) or root.is_symlink() or not root.is_dir():
        raise UpdateLockError("unsafe or missing installation root")
    lock_path = root / _LOCK_FILE
    if lock_path.is_symlink():
        raise UpdateLockError("unsafe update lock file")
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise UpdateLockError("update lock file could not be opened safely") from exc
    acquired = False
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise UpdateLockError("unsafe update lock file")
        if info.st_size == 0:
            os.write(fd, b"\0")
            os.fsync(fd)
        _lock(fd)
        acquired = True
        yield
    finally:
        if acquired:
            try:
                _unlock(fd)
            except OSError:
                # Closing the descriptor still releases the operating-system
                # lock.  Do not hide an exception already raised by the body.
                pass
        os.close(fd)
