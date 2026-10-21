"""Cross-platform advisory locks for services and shared structure caches."""

from __future__ import annotations

import errno
import math
import os
import sys
import time
from pathlib import Path

from phmap.errors import ConfigurationError


class LocalLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._descriptor: int | None = None

    def acquire(self, *, timeout: float = 0) -> None:
        if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout < 0:
            raise ConfigurationError("Lock timeout must be finite and nonnegative")
        if self._descriptor is not None:
            raise ConfigurationError("This pHmap lock is already held")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        deadline = time.monotonic() + timeout
        try:
            if sys.platform == "win32" and os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
            while True:
                try:
                    if sys.platform == "win32":
                        import msvcrt

                        os.lseek(descriptor, 0, os.SEEK_SET)
                        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or exc.errno not in (
                        errno.EACCES, errno.EAGAIN, errno.EDEADLK,
                    ):
                        raise ConfigurationError(
                            "Another pHmap instance is using this directory"
                        ) from exc
                    time.sleep(min(0.05, remaining))
        except BaseException:
            os.close(descriptor)
            raise
        self._descriptor = descriptor

    def release(self) -> None:
        if self._descriptor is not None:
            os.close(self._descriptor)
            self._descriptor = None
