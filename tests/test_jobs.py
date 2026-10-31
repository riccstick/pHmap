import threading
from decimal import Decimal
from pathlib import Path

import pytest

from phmap.cli import build_parser
from phmap.configuration import WorkflowOptions
from phmap.errors import ConfigurationError
from phmap.gui.jobs import JobManager, build_worker_argv, read_json, write_json
from phmap.inputs import validate_run_inputs
from phmap.manifest import sha256_file
from phmap.run_updates import RunUpdate


@pytest.mark.parametrize("status", ["cancelled", "interrupted"])
def test_cancellation_finalization_preserves_prior_failures_and_completed_work(
    tmp_path: Path, status: str,
) -> None:
    manager = JobManager(tmp_path / "runs")
    path = manager.runs_dir / "run" / "manifest.json"
    path.parent.mkdir(parents=True)
    completed = {
        "task_id": "completed", "status": "completed",
        "stages": [{"name": "apbs", "status": "completed", "outputs": ["model.dx"]}],
    }
    failed = {
        "task_id": "failed-before-cancel", "status": "failed", "error": "Real failure",
        "stages": [{"name": "apbs", "status": "failed", "error": "Real failure"}],
    }
    write_json(path, {
        "status": "running",
        "tasks": [
            completed, failed,
            {"task_id": "failed-during-stop", "status": "failed", "stages": [
                {"name": "pdb2pqr", "status": "completed", "outputs": ["model.pqr"]},
                {"name": "apbs", "status": "failed", "returncode": 13,
                 "stdout": "apbs.stdout.log", "stderr": "apbs.stderr.log"},
            ]},
            {"task_id": "running", "status": "running", "current_stage": "apbs",
             "stages": [{"name": "apbs", "status": "running"}]},
            {"task_id": "pending", "status": "pending", "stages": []},
        ],
    })
    manager._mark_manifest(
        "run", status, "Stopped",
        cancelled_tasks={"completed", "failed-during-stop", "running", "pending"},
    )
    data = read_json(path)
    assert data["status"] == status
    done, prior_failure, shutdown_failure, active, pending = data["tasks"]
    assert done == completed
    assert prior_failure == failed
    assert shutdown_failure["status"] == ("cancelled" if status == "cancelled" else "failed")
    first, last = shutdown_failure["stages"]
    assert first == {"name": "pdb2pqr", "status": "completed", "outputs": ["model.pqr"]}
    assert last["status"] == shutdown_failure["status"]
    assert last["returncode"] == 13
    assert last["stdout"] == "apbs.stdout.log" and last["stderr"] == "apbs.stderr.log"
    assert active["status"] == pending["status"] == status
    assert active["stages"][-1]["status"] == status
    assert "current_stage" not in active


@pytest.mark.parametrize("mode", range(5))
@pytest.mark.parametrize("representation", ["sticks", "lines", "dots", "spheres", None])
@pytest.mark.parametrize(
    "solvent,above",
    [
        (False, True),
        (True, False),
        (True, True),
        (False, False),
    ],
)
def test_advanced_render_settings_survive_worker_serialization(
    tmp_path: Path,
    mode: int,
    representation,
    solvent: bool,
    above: bool,
) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "input.pdb"
    path.write_text("ATOM\n", encoding="utf-8")
    options = WorkflowOptions(
        output_dir=tmp_path / "run",
        surface_mode=mode,
        surface_solvent=solvent,
        surface_ramp_above_mode=above,
        ligand_representation=representation,
    )
    inputs = validate_run_inputs(path, ph_values=["7"])
    arguments = build_worker_argv(inputs, options)
    # Workers run `python -m phmap`; parse their arguments with the actual CLI.
    parsed = build_parser().parse_args(arguments[3:])
    assert parsed.surface_mode == mode
    assert parsed.surface_solvent is solvent
    assert parsed.surface_ramp_above_mode is above
    assert parsed.ligand_representation == (representation or "none")


@pytest.mark.parametrize("precision", [None, 0, 2])
@pytest.mark.parametrize("legend", [False, True])
def test_publication_settings_survive_worker_serialization(
    tmp_path: Path,
    precision: int | None,
    legend: bool,
) -> None:
    path = tmp_path / "input.pdb"
    path.write_text("ATOM\n", encoding="utf-8")
    inputs = validate_run_inputs(path, ph_values=["5.125", "7.5"])
    options = WorkflowOptions(
        font_size=36,
        outer_margin=0,
        column_gap=22,
        row_gap=35,
        label_gap=10,
        show_colorbar=legend,
        ph_label_precision=precision,
        foreground="#555555",
    )
    parsed = build_parser().parse_args(build_worker_argv(inputs, options)[3:])
    assert parsed.font_size == 36
    assert parsed.outer_margin == 0
    assert parsed.column_gap == 22 and parsed.row_gap == 35
    assert parsed.label_gap == 10
    assert parsed.show_colorbar is legend
    assert parsed.ph_label_precision == precision
    assert parsed.foreground == "#555555"
    assert tuple(Decimal(ph) for ph in parsed.ph_values) == inputs.ph_values


def test_worker_receives_the_original_non_aligned_range(tmp_path: Path) -> None:
    path = tmp_path / "input.pdb"
    path.write_text("ATOM\n", encoding="utf-8")
    inputs = validate_run_inputs(path, ph_range=("3", "8", "2"))
    assert inputs.ph_range is not None
    arguments = build_parser().parse_args(build_worker_argv(inputs, WorkflowOptions())[3:])
    assert arguments.ph_range == ["3", "8", "2"]
    assert arguments.ph_values is None


def test_parallel_worker_setting_survives_worker_arguments(tmp_path: Path) -> None:
    path = tmp_path / "input.pdb"
    path.write_text("ATOM\n")
    arguments = build_parser().parse_args(
        build_worker_argv(
            validate_run_inputs(path, ph_values=["5", "7"]),
            WorkflowOptions(workers=4),
        )[3:]
    )
    assert arguments.workers == 4


def test_older_cancelled_jobs_without_a_snapshot_cannot_offer_a_template(tmp_path: Path) -> None:
    records = tmp_path / "runs/.gui/jobs"
    records.mkdir(parents=True)
    write_json(
        records / "old-cancelled.json",
        {
            "status": "cancelled",
            "configuration": {
                "proteins": [{"id": "wt", "label": "WT"}],
                "ph_values": ["7"],
            },
        },
    )
    manager = JobManager(tmp_path / "runs")
    manager.start()
    try:
        assert manager.snapshot("old-cancelled")["can_template"] is False
    finally:
        manager.close()


@pytest.mark.parametrize("installed", [False, True])
def test_queue_restart_recovers_an_update_without_corrupting_the_current_calculation(
    tmp_path: Path,
    installed: bool,
) -> None:
    runs = tmp_path / "runs"
    root = runs / "session"
    root.mkdir(parents=True)
    (root / "output").mkdir()
    (root / "output/pHmap.png").write_bytes(b"old figure")
    manifest = {
        "schema_version": 1,
        "run_id": "session",
        "created_at": "original-time",
        "status": "completed",
        "configuration": {
            "proteins": [{"id": "wt", "label": "WT"}],
            "ph_values": ["7"],
        },
        "tasks": [],
    }
    write_json(root / "manifest.json", manifest)
    before = (root / "manifest.json").read_bytes()
    digest = sha256_file(root / "manifest.json")
    update = RunUpdate(root)
    update.begin(digest)
    update.pending.mkdir()
    (update.pending / "output").mkdir()
    (update.pending / "output/pHmap.png").write_bytes(b"updated figure")
    write_json(update.pending / "manifest.json", manifest)
    if installed:
        update.commit()
    update.lock.release()  # Simulate loss of the owning GUI process.
    records = runs / ".gui/jobs"
    records.mkdir(parents=True)
    write_json(
        records / "session.json",
        {
            "run_id": "session",
            "name": "Calculation",
            "status": "running",
            "update_in_place": True,
            "source_manifest_sha256": digest,
        },
    )
    manager = JobManager(runs)
    manager.start()
    try:
        assert manager.snapshot("session")["status"] == (
            "completed" if installed else "interrupted"
        )
        assert (root / "output/pHmap.png").read_bytes() == (
            b"updated figure" if installed else b"old figure"
        )
        if not installed:
            assert (root / "manifest.json").read_bytes() == before
        assert not update.folder.exists()
        assert len(manager.history()) == 1
    finally:
        manager.close()


def test_cancelled_waiting_jobs_immediately_free_queue_capacity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runs" / "existing"
    root.mkdir(parents=True)
    write_json(
        root / "manifest.json",
        {
            "status": "completed",
            "created_at": "2026-01-01T00:00:00Z",
            "configuration": {
                "proteins": [{"id": "wt", "label": "WT"}],
                "ph_values": ["3", "8"],
            },
            "tasks": [{"protein_id": "wt", "ph": "3", "status": "completed"}],
        },
    )
    manager = JobManager(root.parent)
    summary = manager.history()[0]
    detail = manager.snapshot("existing")
    assert summary == {key: detail[key] for key in summary}
    monkeypatch.setattr(manager, "snapshot", lambda _: pytest.fail("Full snapshot requested"))
    monkeypatch.setattr(Path, "rglob", lambda *args: pytest.fail("Artifacts were scanned"))
    monkeypatch.setattr(manager, "log_tail", lambda _: pytest.fail("Logs were read"))
    assert manager.history() == [
        {
            "run_id": "existing",
            "name": "WT",
            "source": "cli",
            "status": "completed",
            "created_at": "2026-01-01T00:00:00Z",
            "completed": 1,
            "total": 2,
        }
    ]
