import threading
from pathlib import Path

import pytest

from phmap.cli import build_parser
from phmap.configuration import WorkflowOptions
from phmap.errors import ConfigurationError
from phmap.gui.jobs import JobManager, build_worker_argv, write_json
from phmap.inputs import validate_run_inputs


@pytest.mark.parametrize("mode", range(5))
@pytest.mark.parametrize("representation", ["sticks", "lines", "dots", "spheres", None])
@pytest.mark.parametrize("solvent,above", [
    (False, True), (True, False), (True, True), (False, False),
])
def test_advanced_render_settings_survive_worker_serialization(
    tmp_path: Path, mode: int, representation, solvent: bool, above: bool,
) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "input.pdb"
    path.write_text("ATOM\n", encoding="utf-8")
    options = WorkflowOptions(
        output_dir=tmp_path / "run", surface_mode=mode, surface_solvent=solvent,
        surface_ramp_above_mode=above, ligand_representation=representation,
    )
    inputs = validate_run_inputs(path, ph_values=["7"])
    arguments = build_worker_argv(inputs, options)
    # Workers run `python -m phmap`; parse their arguments with the actual CLI.
    parsed = build_parser().parse_args(arguments[3:])
    assert parsed.surface_mode == mode
    assert parsed.surface_solvent is solvent
    assert parsed.surface_ramp_above_mode is above
    assert parsed.ligand_representation == (representation or "none")


def test_cancelled_waiting_jobs_immediately_free_queue_capacity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = threading.Event()
    release = threading.Event()

    def hold_worker(self: JobManager, run_id: str) -> None:
        entered.set()
        assert release.wait(10), "Test worker was not released"
        with self._condition:
            self._jobs[run_id]["status"] = "completed"
            self._save(run_id)

    monkeypatch.setattr(JobManager, "_execute", hold_worker)
    pdb = tmp_path / "input.pdb"
    pdb.write_text("ATOM\n", encoding="utf-8")
    inputs = validate_run_inputs(pdb, ph_values=["5"])
    options = WorkflowOptions(size=64)
    manager = JobManager(tmp_path / "runs")
    manager.start()
    try:
        active = manager.submit(inputs, options)
        assert entered.wait(2)
        waiting = [manager.submit(inputs, options) for _ in range(20)]
        with pytest.raises(ConfigurationError, match="queue is full"):
            manager.submit(inputs, options)
        for run_id in waiting:
            manager.cancel(run_id)
            manager.cancel(run_id)  # Repeated cancellation is harmless.
            assert manager.snapshot(run_id)["status"] == "cancelled"
            assert not manager.run_directory(run_id).exists()
        replacement = manager.submit(inputs, options)
        assert manager.snapshot(active)["status"] == "running"
        assert manager.snapshot(replacement)["status"] == "queued"
    finally:
        release.set()
        manager.close()


def test_history_uses_summaries_without_scanning_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runs" / "existing"
    root.mkdir(parents=True)
    write_json(root / "manifest.json", {
        "status": "completed", "created_at": "2026-01-01T00:00:00Z",
        "configuration": {
            "proteins": [{"id": "wt", "label": "WT"}], "ph_values": ["3", "8"],
        },
        "tasks": [{"protein_id": "wt", "ph": "3", "status": "completed"}],
    })
    manager = JobManager(root.parent)
    summary = manager.history()[0]
    detail = manager.snapshot("existing")
    assert summary == {key: detail[key] for key in summary}
    monkeypatch.setattr(manager, "snapshot", lambda _: pytest.fail("Full snapshot requested"))
    monkeypatch.setattr(Path, "rglob", lambda *args: pytest.fail("Artifacts were scanned"))
    monkeypatch.setattr(manager, "log_tail", lambda _: pytest.fail("Logs were read"))
    assert manager.history() == [{
        "run_id": "existing", "name": "WT", "source": "cli", "status": "completed",
        "created_at": "2026-01-01T00:00:00Z", "completed": 1, "total": 2,
    }]
