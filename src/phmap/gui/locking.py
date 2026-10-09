"""Small cross-platform advisory locks for a local service and its launcher."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from phmap.errors import ConfigurationError


class LocalLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._descriptor: int | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if sys.platform == "win32":
                import msvcrt

                # Windows byte-range locks require a byte to exist.
                if os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b"\0")
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(descriptor)
            raise ConfigurationError("Another pHmap instance is using this directory") from exc
        self._descriptor = descriptor

    def release(self) -> None:
        if self._descriptor is not None:
            os.close(self._descriptor)
            self._descriptor = None
