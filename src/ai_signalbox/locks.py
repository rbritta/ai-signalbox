"""File locks shared by every process that writes the state root.

Windows: `msvcrt.locking` on one byte at offset 0 with `LK_LOCK`, which retries for about 10 s and then raises.
POSIX: `fcntl.flock`, retried for 10 s. When the lock can't be had, the caller goes on without it: a racy write beats a
stuck hook. The primitive must not change on Windows, so older tools that lock the same files keep interleaving safely.
"""
from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path

POSIX_RETRY_SECONDS = 10.0


def _lock(fh) -> bool:
    if os.name == 'nt':
        try:
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)  # retries for 10 s, then OSError
            return True
        except (ImportError, OSError):
            return False
    try:
        import fcntl
    except ImportError:
        return False
    deadline = time.monotonic() + POSIX_RETRY_SECONDS
    while True:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)


def _unlock(fh) -> None:
    if os.name == 'nt':
        import msvcrt
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl
    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def file_lock(path):
    """Hold the lock file `path` for the block. Yields whether the lock was really held."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'a+b') as fh:
        held = _lock(fh)
        try:
            yield held
        finally:
            if held:
                _unlock(fh)


_depth: dict[str, int] = {}


@contextlib.contextmanager
def reentrant_lock(path):
    """`file_lock`, reentrant inside one process: a command that writes a status card may call another that does."""
    key = str(Path(path))
    if _depth.get(key):
        _depth[key] += 1
        try:
            yield True
        finally:
            _depth[key] -= 1
        return
    with file_lock(path) as held:
        _depth[key] = 1
        try:
            yield held
        finally:
            _depth[key] = 0


def depth(path) -> int:
    return _depth.get(str(Path(path)), 0)
