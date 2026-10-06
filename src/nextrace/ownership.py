from __future__ import annotations

import errno
import os
import uuid
import weakref
from pathlib import Path
from typing import BinaryIO


def _lock(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


class RecordingOwner:
    """An OS lock survives long calls and is released even when its process is killed."""

    def __init__(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.id = uuid.uuid4().hex
        self.pid = os.getpid()
        self.path = directory / self.id
        self.handle = self.path.open("x+b")
        weakref.finalize(self, _release, self.handle, self.path, self.pid)
        self.handle.write(b"0")
        self.handle.flush()
        _lock(self.handle)


def _release(handle: BinaryIO, path: Path, pid: int) -> None:
    handle.close()
    # A forked child must not remove its parent's ownership marker.
    if pid == os.getpid():
        path.unlink(missing_ok=True)


def owner_alive(directory: Path, owner_id: str) -> bool | None:
    if not isinstance(owner_id, str) or len(owner_id) != 32:
        return None
    try:
        if uuid.UUID(owner_id).hex != owner_id:
            return None
    except ValueError:
        return None
    try:
        with (directory / owner_id).open("r+b") as handle:
            _lock(handle)
    except FileNotFoundError:
        return False
    except OSError as error:
        if error.errno in {errno.EACCES, errno.EAGAIN}:
            return True
        return None
    return False
