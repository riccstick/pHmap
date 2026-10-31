from __future__ import annotations

import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

import pytest
from fake_tools import fake_tools

from phmap.backends import (
    ApbsBackend,
    BackendValidationError,
    CommandRunner,
    Pdb2pqrBackend,
    PyMOLRenderOptions,
)
from phmap.cli import build_parser
from phmap.configuration import WorkflowOptions
from phmap.errors import ConfigurationError
from phmap.inputs import validate_run_inputs
from phmap.manifest import RunManifest
from phmap.parallel import check_cancelled, run_cells
from phmap.workflow import LocalWorkflow


@pytest.mark.parametrize("value", [0, -1, 33, True, "2", 1.5])
def test_invalid_worker_counts_are_rejected(value: object) -> None:
    with pytest.raises(ConfigurationError, match="Parallel workers"):
        WorkflowOptions(workers=value)


@pytest.mark.parametrize("value", [0, -1, 33, True, "2", 1.5])
def test_invalid_pymol_thread_limits_are_rejected(value: object) -> None:
    with pytest.raises(BackendValidationError, match="max_threads"):
        PyMOLRenderOptions(max_threads=value)


def test_worker_budget_is_bounded_by_cpu_count_and_cell_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("phmap.configuration.available_cpus", lambda: 4)
    options = WorkflowOptions(workers=8)
    assert options.execution_configuration(20)["effective_workers"] == 4
    assert options.execution_configuration(2)["effective_workers"] == 2
    assert options.execution_configuration(1)["backend_threads"] is None
    assert options.execution_configuration(20, parallelizable=False)["effective_workers"] == 1
    assert build_parser().parse_args(
        ["run", "x.pdb", "--ph", "5", "--workers", "auto"]
    ).workers in {1, 2}


def test_pool_really_overlaps_cells_and_preserves_input_order() -> None:
    barrier = threading.Barrier(2)
    lock = threading.Lock()
    active, peak = 0, 0

    def work(value: int, stop: threading.Event) -> int:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        barrier.wait(timeout=5)
        time.sleep(0.03 if value % 2 == 0 else 0)
        with lock:
            active -= 1
        return value

    assert run_cells(list(range(6)), work, workers=2) == list(range(6))
    assert peak == 2


def test_failure_stops_new_cells_and_preserves_the_original_error() -> None:
    barrier = threading.Barrier(2)
    seen = []

    def work(value: int, stop: threading.Event) -> int:
        seen.append(value)
        barrier.wait(timeout=5)
        if value == 0:
            raise RuntimeError("original scientific failure")
        assert stop.wait(5)
        check_cancelled(stop)
        raise AssertionError("Sibling should have stopped")

    with pytest.raises(RuntimeError, match="original scientific failure"):
        run_cells(list(range(10)), work, workers=2)
    assert set(seen) == {0, 1}


def test_parallel_manifest_writes_do_not_lose_stages_or_reorder_cells(tmp_path: Path) -> None:
    manifest = RunManifest(tmp_path / "manifest.json", "test")
    phs = [Decimal(index) / 10 for index in range(20)]
    manifest.prepare_tasks([("protein", ph) for ph in phs])

    def record(ph: Decimal) -> None:
        task = manifest.start_task(f"protein@{ph}", "protein", ph, metrics={"charge": float(ph)})
        for name in ("pdb2pqr", "apbs", "pymol"):
            manifest.start_stage(task, name)
            manifest.record_stage(task, {"name": name, "status": "completed"})
        manifest.finish_task(task, "completed")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(record, phs))
    data = json.loads(manifest.path.read_bytes())
    assert [task["ph"] for task in data["tasks"]] == [str(ph) for ph in phs]
    assert all(task["status"] == "completed" and len(task["stages"]) == 3 for task in data["tasks"])
    assert all(task["scientific_metrics"] for task in data["tasks"])


def test_child_thread_limits_never_mutate_the_parent_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OMP_NUM_THREADS", "9")
    runner = CommandRunner(tmp_path / "logs", environment={"OMP_NUM_THREADS": "1"})
    result = runner.run(
        "environment",
        [sys.executable, "-c", "import os; print(os.environ['OMP_NUM_THREADS'])"],
        cwd=tmp_path,
    )
    assert result.read_stdout().strip() == "1"
    assert os.environ["OMP_NUM_THREADS"] == "9"


def test_full_workflow_parallelizes_real_child_commands_with_isolated_cells(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("phmap.configuration.available_cpus", lambda: 4)
    source = tmp_path / "protein.pdb"
    source.write_text("ATOM\n")
    pdb2pqr, apbs, pymol = fake_tools(tmp_path)
    original = Pdb2pqrBackend.run
    barrier = threading.Barrier(2)

    def synchronized(backend, request, **kwargs):  # type: ignore[no-untyped-def]
        assert backend.runner.environment["OMP_NUM_THREADS"] == "1"
        barrier.wait(timeout=5)
        return original(backend, request, **kwargs)

    monkeypatch.setattr(Pdb2pqrBackend, "run", synchronized)
    result = LocalWorkflow(
        validate_run_inputs(source, ph_values=["5.0", "7.0"]),
        WorkflowOptions(
            output_dir=tmp_path / "parallel",
            workers=2,
            size=64,
            ray_trace=False,
            pdb2pqr_executable=pdb2pqr,
            apbs_executable=apbs,
            pymol_executable=pymol,
        ),
    ).run()
    manifest = json.loads(result.manifest.read_bytes())
    assert manifest["configuration"]["execution"] == {
        "workers": 2,
        "effective_workers": 2,
        "backend_threads": 1,
    }
    assert [task["ph"] for task in manifest["tasks"]] == ["5.0", "7.0"]
    for ph in ("5.0", "7.0"):
        paths = result.workspace.task_paths("protein", ph)
        assert paths.pqr.is_file() and paths.potential.is_file() and paths.tile.is_file()
        assert "set max_threads, 1" in paths.pymol_script.read_text()


def test_parallel_workflow_failure_cancels_remaining_stages_and_unstarted_cells(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("phmap.configuration.available_cpus", lambda: 4)
    source = tmp_path / "protein.pdb"
    source.write_text("ATOM\n")
    pdb2pqr, apbs, pymol = fake_tools(tmp_path)
    original = Pdb2pqrBackend.run
    barrier = threading.Barrier(2)
    seen = []

    def fail_one(backend, request, **kwargs):  # type: ignore[no-untyped-def]
        seen.append(request.ph)
        barrier.wait(timeout=5)
        if request.ph == Decimal("5"):
            raise RuntimeError("failed first cell")
        time.sleep(0.1)
        return original(backend, request, **kwargs)

    monkeypatch.setattr(Pdb2pqrBackend, "run", fail_one)
    monkeypatch.setattr(
        ApbsBackend, "run", lambda *_args, **_kwargs: pytest.fail("Must not start APBS")
    )
    workflow = LocalWorkflow(
        validate_run_inputs(source, ph_values=["5", "7", "8"]),
        WorkflowOptions(
            output_dir=tmp_path / "failed",
            workers=2,
            size=64,
            pdb2pqr_executable=pdb2pqr,
            apbs_executable=apbs,
            pymol_executable=pymol,
        ),
    )
    with pytest.raises(RuntimeError, match="failed first cell"):
        workflow.run()
    data = json.loads((tmp_path / "failed/manifest.json").read_bytes())
    assert data["status"] == "failed" and set(seen) == {Decimal("5"), Decimal("7")}
    assert [task["status"] for task in data["tasks"]] == ["failed", "cancelled", "cancelled"]
    assert not (tmp_path / "failed/output/pHmap.png").exists()


@pytest.mark.parametrize("workers", [1, 2])
def test_interrupted_workflow_finalizes_every_unfinished_cell(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    workers: int,
) -> None:
    monkeypatch.setattr("phmap.configuration.available_cpus", lambda: 4)
    source = tmp_path / "protein.pdb"
    source.write_text("ATOM\n")
    pdb2pqr, apbs, pymol = fake_tools(tmp_path)

    def interrupt(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise KeyboardInterrupt

    monkeypatch.setattr(Pdb2pqrBackend, "run", interrupt)
    workflow = LocalWorkflow(
        validate_run_inputs(source, ph_values=["5", "7", "8"]),
        WorkflowOptions(
            output_dir=tmp_path / "interrupted",
            workers=workers,
            size=64,
            pdb2pqr_executable=pdb2pqr,
            apbs_executable=apbs,
            pymol_executable=pymol,
        ),
    )
    with pytest.raises(KeyboardInterrupt):
        workflow.run()
    data = json.loads((tmp_path / "interrupted/manifest.json").read_bytes())
    assert data["status"] == "cancelled"
    assert all(task["status"] == "cancelled" for task in data["tasks"])
    assert all("current_stage" not in task for task in data["tasks"])
    assert all(
        stage["status"] != "running" for task in data["tasks"] for stage in task["stages"]
    )
