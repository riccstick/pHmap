"""Bounded cell concurrency; scientific programs execute in separate processes."""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from threading import Event, Lock
from typing import TypeVar

MAX_WORKERS = 32
_T = TypeVar("_T")
_R = TypeVar("_R")

# Set on each child's environment, never by mutating the shared parent process.
SINGLE_THREAD_ENV = {
    "OMP_NUM_THREADS": "1",
    "OMP_THREAD_LIMIT": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "VECLIB_MAXIMUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
    "BLIS_NUM_THREADS": "1",
}


def available_cpus() -> int:
    try:
        affinity = getattr(os, "sched_getaffinity", None)
        if affinity is not None:
            return max(1, len(affinity(0)))
    except OSError:
        pass
    return max(1, os.cpu_count() or 1)


def recommended_workers() -> int:
    """Conservative default: APBS cells can each require substantial memory."""
    return min(2, available_cpus())


class CellCancelled(Exception):
    """A sibling cell failed; do not start another scientific stage."""


def check_cancelled(stop: Event) -> None:
    if stop.is_set():
        raise CellCancelled("Stopped because another calculation cell failed")


def run_cells(
    items: Sequence[_T],
    function: Callable[[_T, Event], _R],
    *,
    workers: int,
) -> list[_R]:
    """Keep at most ``workers`` cells in flight and preserve input result order.

    On failure stop submitting cells and starting stages. Already running backend
    commands finish under their normal timeouts before the error is propagated;
    GUI cancellation can terminate the entire owned worker/process tree at once.
    """
    stop = Event()
    if workers == 1:
        return [function(item, stop) for item in items]
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="phmap-cell")
    pending: dict[Future[_R], int] = {}
    results: dict[int, _R] = {}
    failures: list[BaseException] = []
    failure_lock = Lock()
    next_index = 0

    def invoke(item: _T) -> _R:
        check_cancelled(stop)
        try:
            return function(item, stop)
        except CellCancelled:
            raise
        except BaseException as exc:
            with failure_lock:
                failures.append(exc)
            stop.set()
            raise

    def submit() -> None:
        nonlocal next_index
        while next_index < len(items) and len(pending) < workers and not stop.is_set():
            index = next_index
            next_index += 1
            pending[pool.submit(invoke, items[index])] = index

    try:
        submit()
        while pending:
            completed, _ = wait(pending, return_when=FIRST_COMPLETED)
            # Inspect the whole completed batch before launching another cell.
            for future in completed:
                results[pending.pop(future)] = future.result()
            submit()
        return [results[index] for index in range(len(items))]
    except CellCancelled:
        with failure_lock:
            original = failures[0] if failures else None
        if original is not None:
            raise original from None
        raise
    finally:
        stop.set()
        for future in pending:
            future.cancel()
        pool.shutdown(wait=True, cancel_futures=True)
