"""File locks shared by every process that writes the state root.

Windows: `msvcrt.locking` on one byte at offset 0, tried every 10 ms for 10 s (`LK_NBLCK`: the same lock `LK_LOCK`
takes, without its one-second wait between tries). POSIX: `fcntl.flock`, tried the same way. When the lock can't be
had, a hook goes on without it (a racy write beats a stuck hook) and a command refuses (`required=True`: two exclusive
holders of one lease are worse than a retry). The locked byte must not change on Windows, so older tools that lock the
same files keep interleaving safely.
"""
from __future__ import annotations

import contextlib
import os
import threading
import time
from pathlib import Path

RETRY_SECONDS, RETRY_WAIT = 10.0, 0.01


def _lock(fh) -> bool:
    """Try the lock every few milliseconds for up to 10 s (`LK_LOCK` would wait a whole second between tries)."""
    if os.name == 'nt':
        try:
            import msvcrt
        except ImportError:
            return False
        try_lock = lambda: msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)  # noqa: E731
        fh.seek(0)
    else:
        try:
            import fcntl
        except ImportError:
            return False
        try_lock = lambda: fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # noqa: E731
    deadline = time.monotonic() + RETRY_SECONDS
    while True:
        try:
            try_lock()
            return True
        except OSError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(RETRY_WAIT)


def _unlock(fh) -> None:
    if os.name == 'nt':
        import msvcrt
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl
    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


class LockTimeout(OSError):
    """The lock was required and could not be had in time."""


@contextlib.contextmanager
def file_lock(path, required=False):
    """Hold the lock file `path` for the block. Yields whether the lock was really held; with `required`, raises
    `LockTimeout` instead of running the block without it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'a+b') as fh:
        held = _lock(fh)
        if required and not held:
            raise LockTimeout(f'could not lock {path.name}')
        try:
            yield held
        finally:
            if held:
                _unlock(fh)


_local = threading.local()


def _depths() -> dict[str, int]:
    if not hasattr(_local, 'depth'):
        _local.depth = {}
    return _local.depth


@contextlib.contextmanager
def reentrant_lock(path):
    """`file_lock`, reentrant inside one thread: a command that writes a status card may call another that does. Two
    threads (`serve`) each take the file lock."""
    key, depths = str(Path(path)), _depths()
    if depths.get(key):
        depths[key] += 1
        try:
            yield True
        finally:
            depths[key] -= 1
        return
    with file_lock(path) as held:
        depths[key] = 1
        try:
            yield held
        finally:
            depths[key] = 0


def depth(path) -> int:
    return _depths().get(str(Path(path)), 0)
