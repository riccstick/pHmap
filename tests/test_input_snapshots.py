from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from phmap.configuration import WorkflowOptions
from phmap.errors import ConfigurationError
from phmap.gui.input_snapshots import remove_input_snapshot, retire_input_snapshot
from phmap.gui.jobs import JobManager
from phmap.inputs import validate_run_inputs
from phmap.json_state import read_json, write_json
from phmap.manifest import sha256_file
from phmap.models import RunInputs
from phmap.structures import pdb_download_url


@pytest.fixture
def waiting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[JobManager, str, RunInputs, WorkflowOptions, threading.Event]]:
    entered, release = threading.Event(), threading.Event()

    def hold(self: JobManager, run_id: str) -> None:
        entered.set()
        assert release.wait(10)
        with self._condition:
            self._jobs[run_id]["status"] = "completed"
            self._save(run_id)

    monkeypatch.setattr(JobManager, "_execute", hold)
    pdb, ligand, view = (tmp_path / name for name in ("protein.pdb", "ligand.mol2", "view.txt"))
    pdb.write_text("ATOM\n")
    ligand.write_text("shared ligand\n")
    view.write_text(", ".join(str(value) for value in range(18)))
    write_json(pdb.with_suffix(".pdb.source.json"), {
        "provider": "RCSB PDB", "pdb_id": "1CRN", "url": pdb_download_url("1CRN"),
        "downloaded_at": "2026-10-31T00:00:00Z", "sha256": sha256_file(pdb),
    })
    inputs = validate_run_inputs(pdb, ph_values=["5"], ligand=ligand, view_file=view)
    options = WorkflowOptions(size=64)
    manager = JobManager(tmp_path / "runs")
    manager.start()
    try:
        manager.submit(inputs, options)
        assert entered.wait(2)
        run_id = manager.submit(inputs, options)
        manager.cancel(run_id)
        yield manager, run_id, inputs, options, release
    finally:
        release.set()
        manager.close()


def _resume(
    manager: JobManager, run_id: str, inputs: RunInputs, options: WorkflowOptions,
) -> str:
    return manager.submit(
        inputs, options, update_run_id=run_id,
        expected_manifest=sha256_file(manager.records / f"{run_id}.json"),
    )


def test_repeated_resumes_replace_the_snapshot_without_retaining_previous_copies(waiting) -> None:
    manager, run_id, inputs, options, _ = waiting
    for _ in range(3):
        old = manager.load_template(run_id).input_root
        # Keep a different calculation and unrecognized user directories untouched.
        user_folder = manager.uploads / "my-notes"
        user_folder.mkdir(exist_ok=True)
        assert _resume(manager, run_id, inputs, options) == run_id
        assert not old.exists()
        manager.cancel(run_id)
        template = manager.load_template(run_id)
        assert template.protein(inputs.proteins[0].protein_id).path.is_file()
        assert template.ligand().path.is_file()
        assert template.view().values == inputs.view.values
        assert len(list(manager.uploads.iterdir())) == 3  # Blocker, current snapshot, user folder.
        assert "retired_input_snapshots" not in read_json(manager.records / f"{run_id}.json")


@pytest.mark.parametrize("failure", ["copy", "save"])
def test_failed_resume_keeps_the_previous_setup_and_discards_unpublished_staging(
    waiting, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    manager, run_id, inputs, options, _ = waiting
    old = manager.load_template(run_id).input_root
    before = (manager.records / f"{run_id}.json").read_bytes()
    contents = {path.name: path.read_bytes() for path in old.iterdir()}
    folders = set(manager.uploads.iterdir())
    with monkeypatch.context() as patch:
        if failure == "save":
            def fail_save(identity: str) -> None:
                assert identity == run_id
                raise OSError("disk full")

            patch.setattr(manager, "_save", fail_save)
        else:
            import shutil

            copy = shutil.copy2

            def fail_copy(source, destination):  # type: ignore[no-untyped-def]
                if Path(source).suffix == ".mol2":
                    raise OSError("copy failed")  # The protein was already staged.
                return copy(source, destination)

            patch.setattr("phmap.gui.jobs.shutil.copy2", fail_copy)
        with pytest.raises(OSError, match="disk full|copy failed"):
            _resume(manager, run_id, inputs, options)
    assert set(manager.uploads.iterdir()) == folders
    assert (manager.records / f"{run_id}.json").read_bytes() == before
    assert {path.name: path.read_bytes() for path in old.iterdir()} == contents
    assert manager.snapshot(run_id)["status"] == "cancelled"
    assert manager.load_template(run_id).input_root == old
    assert _resume(manager, run_id, inputs, options) == run_id
    assert not old.exists()
    manager.cancel(run_id)


def test_failed_deletion_is_retried_on_restart_without_rejecting_the_new_setup(
    waiting, monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, run_id, inputs, options, release = waiting
    old = manager.load_template(run_id).input_root

    def locked(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise PermissionError("snapshot is temporarily locked")

    with monkeypatch.context() as patch:
        patch.setattr("phmap.gui.jobs.remove_input_snapshot", locked)
        assert _resume(manager, run_id, inputs, options) == run_id
    assert old.is_dir()
    record = read_json(manager.records / f"{run_id}.json")
    assert record["retired_input_snapshots"] and record["input_cleanup_error"]
    manager.cancel(run_id)
    release.set()
    manager.close()
    recovered = JobManager(manager.runs_dir)
    recovered.start()
    try:
        assert not old.exists()
        assert recovered.load_template(run_id).protein(inputs.proteins[0].protein_id).path.is_file()
        record = read_json(recovered.records / f"{run_id}.json")
        assert "retired_input_snapshots" not in record
        assert "input_cleanup_error" not in record
    finally:
        recovered.close()


def test_cleanup_does_not_remove_added_user_files(waiting) -> None:
    manager, run_id, inputs, options, _ = waiting
    old = manager.load_template(run_id).input_root
    note = old / "notes.txt"
    note.write_text("keep my notes")
    assert _resume(manager, run_id, inputs, options) == run_id
    assert note.read_text() == "keep my notes"
    assert old.is_dir()
    manager.cancel(run_id)
    assert manager.load_template(run_id).input_root != old
    assert "additional user files" in manager._jobs[run_id]["input_cleanup_error"]


@pytest.mark.parametrize("folder", [".", "..", "../other", "/", "another-calculation"])
def test_cleanup_rejects_unowned_folders(waiting, folder: str) -> None:
    manager, run_id, _, _, _ = waiting
    snapshot = retire_input_snapshot(run_id, manager._jobs[run_id])
    snapshot["input_folder"] = folder
    with pytest.raises(ConfigurationError, match="Unrecognized"):
        remove_input_snapshot(manager.uploads, run_id, snapshot)
    assert manager.load_template(run_id).input_root.is_dir()


@pytest.mark.parametrize("location", ["directory", "input"])
def test_cleanup_rejects_symbolic_links(waiting, tmp_path: Path, location: str) -> None:
    manager, run_id, _, _, _ = waiting
    snapshot = retire_input_snapshot(run_id, manager._jobs[run_id])
    original = (
        manager.uploads / snapshot["input_folder"] if location == "directory"
        else Path(snapshot["inputs"][0]["staged_path"])
    )
    moved = tmp_path / "preserved-inputs"
    original.rename(moved)
    try:
        original.symlink_to(moved, target_is_directory=location == "directory")
    except OSError:
        moved.rename(original)
        pytest.skip("Symlinks are unavailable")
    with pytest.raises(ConfigurationError, match="symbolic|additional user files"):
        remove_input_snapshot(manager.uploads, run_id, snapshot)
    assert moved.exists() and original.is_symlink()


def test_cleanup_preserves_modified_inputs(waiting) -> None:
    manager, run_id, inputs, options, _ = waiting
    old = manager.load_template(run_id).input_root
    path = Path(manager._jobs[run_id]["inputs"][0]["staged_path"])
    path.write_text("user's edited structure\n")
    assert _resume(manager, run_id, inputs, options) == run_id
    assert path.read_text() == "user's edited structure\n"
    assert old.is_dir()
    assert "modified" in manager._jobs[run_id]["input_cleanup_error"]
    manager.cancel(run_id)


def test_cleanup_does_not_remove_a_referenced_snapshot(waiting) -> None:
    manager, run_id, _, _, _ = waiting
    record = manager._jobs[run_id]
    record["retired_input_snapshots"] = [retire_input_snapshot(run_id, record)]
    manager._save(run_id)
    manager._cleanup_retired_inputs(run_id)
    assert manager.load_template(run_id).input_root.is_dir()
    assert "still referenced" in record["input_cleanup_error"]


def test_retirement_cleanup_is_idempotent_after_a_crash(waiting) -> None:
    manager, run_id, inputs, options, release = waiting
    ticket = retire_input_snapshot(run_id, manager._jobs[run_id])
    assert _resume(manager, run_id, inputs, options) == run_id
    manager.cancel(run_id)
    release.set()
    manager.close()
    # Simulate a crash after removing the old folder but before clearing its ticket.
    path = manager.records / f"{run_id}.json"
    record = read_json(path)
    record["retired_input_snapshots"] = [ticket]
    write_json(path, record)
    recovered = JobManager(manager.runs_dir)
    recovered.start()
    try:
        assert "retired_input_snapshots" not in read_json(path)
        assert recovered.load_template(run_id).protein(inputs.proteins[0].protein_id).path.is_file()
    finally:
        recovered.close()
