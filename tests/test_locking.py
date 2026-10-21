import subprocess
import sys
from pathlib import Path

import pytest

from phmap.errors import ConfigurationError
from phmap.gui.locking import LocalLock as CompatibilityLock
from phmap.locking import LocalLock


def test_lock_remains_available_at_the_original_import_path() -> None:
    assert CompatibilityLock is LocalLock


@pytest.mark.parametrize("timeout", [-1, float("nan"), float("inf"), True])
def test_invalid_lock_timeouts_do_not_create_files(tmp_path: Path, timeout: float) -> None:
    lock = LocalLock(tmp_path / "file.lock")
    with pytest.raises(ConfigurationError, match="timeout"):
        lock.acquire(timeout=timeout)
    assert not lock.path.exists()


def test_contended_lock_times_out_then_can_be_reused(tmp_path: Path) -> None:
    first = LocalLock(tmp_path / "file.lock")
    second = LocalLock(first.path)
    first.acquire()
    try:
        with pytest.raises(ConfigurationError, match="Another pHmap"):
            second.acquire(timeout=0.01)
    finally:
        first.release()
    second.acquire()
    second.release()


def test_lock_can_be_waited_on_from_another_process(tmp_path: Path) -> None:
    lock = LocalLock(tmp_path / "file.lock")
    lock.acquire()
    code = """
import sys
from pathlib import Path
from phmap.locking import LocalLock
lock = LocalLock(Path(sys.argv[1]))
print('ready', flush=True)
lock.acquire(timeout=5)
print('acquired', flush=True)
lock.release()
"""
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(lock.path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "ready"
        assert process.poll() is None
        lock.release()
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr
        assert stdout.strip() == "acquired"
    finally:
        lock.release()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
