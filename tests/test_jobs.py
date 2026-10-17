import threading
from pathlib import Path

import pytest

from phmap.configuration import WorkflowOptions
from phmap.errors import ConfigurationError
from phmap.gui.jobs import JobManager, write_json
from phmap.inputs import validate_run_inputs


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
