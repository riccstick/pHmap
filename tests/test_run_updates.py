from __future__ import annotations

from pathlib import Path

import pytest

from phmap.errors import ConfigurationError
from phmap.gui.jobs import JobManager
from phmap.json_state import read_json, write_json
from phmap.manifest import sha256_file
from phmap.run_updates import RunUpdate


def _update(tmp_path: Path) -> RunUpdate:
    root = tmp_path / "runs/session"
    root.mkdir(parents=True)
    (root / "output").mkdir()
    (root / "output/pHmap.png").write_bytes(b"last good figure")
    write_json(
        root / "manifest.json",
        {
            "schema_version": 1,
            "run_id": "session",
            "created_at": "original-time",
            "status": "completed",
        },
    )
    update = RunUpdate(root)
    update.begin(sha256_file(root / "manifest.json"))
    update.pending.mkdir()
    (update.pending / "output").mkdir()
    (update.pending / "output/pHmap.png").write_bytes(b"new figure")
    write_json(
        update.pending / "manifest.json",
        {
            "schema_version": 1,
            "run_id": "result",
            "created_at": "new-time",
            "status": "completed",
            "outputs": [{"path": str(update.pending / "output/pHmap.png")}],
            "plan": {"operation": "compose"},
            "tasks": [
                {
                    "stages": [
                        {
                            "argv": ["tool", str(update.pending / "model.pqr")],
                            "outputs": [{"path": str(update.pending / "tile.png")}],
                            "stdout": str(update.pending / "stdout.log"),
                        }
                    ]
                }
            ],
        },
    )
    return update


def test_commit_retains_identity_relocates_artifacts_and_keeps_no_old_version(
    tmp_path: Path,
) -> None:
    update = _update(tmp_path)
    digest = sha256_file(update.target / "manifest.json")
    try:
        assert (update.target / "output/pHmap.png").read_bytes() == b"last good figure"
        update.commit()
        data = read_json(update.target / "manifest.json")
        assert data["run_id"] == "session" and data["created_at"] == "original-time"
        assert data["last_update"]["previous_manifest_sha256"] == digest
        assert data["outputs"][0]["path"] == str(update.target / "output/pHmap.png")
        stage = data["tasks"][0]["stages"][0]
        assert stage["outputs"][0]["path"] == str(update.target / "tile.png")
        assert stage["stdout"] == str(update.target / "stdout.log")
        assert stage["argv"] == ["tool", str(update.pending / "model.pqr")]
        assert (update.target / "output/pHmap.png").read_bytes() == b"new figure"
        assert not update.folder.exists()
    finally:
        update.discard()


def test_failed_install_rolls_back_original_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    update = _update(tmp_path)
    before = (update.target / "manifest.json").read_bytes()
    rename = Path.rename

    def fail_install(path: Path, destination: Path) -> Path:
        if path == update.pending:
            raise PermissionError("simulated Windows file lock")
        return rename(path, destination)

    monkeypatch.setattr(Path, "rename", fail_install)
    try:
        with pytest.raises(PermissionError, match="file lock"):
            update.commit()
        assert (update.target / "manifest.json").read_bytes() == before
        assert (update.target / "output/pHmap.png").read_bytes() == b"last good figure"
        assert not update.folder.exists()
    finally:
        update.discard()


@pytest.mark.parametrize("installed", [False, True])
def test_recovery_of_process_exit_between_directory_renames(
    tmp_path: Path, installed: bool
) -> None:
    update = _update(tmp_path)
    data = read_json(update.marker)
    data["replacement_sha256"] = sha256_file(update.pending / "manifest.json")
    write_json(update.marker, data)
    update.target.rename(update.previous)
    if installed:
        update.pending.rename(update.target)
    update.lock.release()  # Simulate process death, then a fresh queue/CLI instance.
    recovering = RunUpdate(update.target)
    recovering.lock.acquire()
    try:
        recovering.recover()
        expected = b"new figure" if installed else b"last good figure"
        assert (update.target / "output/pHmap.png").read_bytes() == expected
        assert not update.folder.exists()
    finally:
        recovering.lock.release()


def test_calculation_lock_prevents_concurrent_replacements(tmp_path: Path) -> None:
    update = _update(tmp_path)
    second = RunUpdate(update.target)
    try:
        with pytest.raises(ConfigurationError, match="Another pHmap"):
            second.begin(sha256_file(update.target / "manifest.json"))
        assert update.pending.is_dir()
    finally:
        update.discard()


def test_external_changes_reject_commit_but_discard_only_our_pending_work(tmp_path: Path) -> None:
    update = _update(tmp_path)
    changed = read_json(update.target / "manifest.json")
    changed["manual_change"] = True
    write_json(update.target / "manifest.json", changed)
    try:
        with pytest.raises(ConfigurationError, match="changed while updating"):
            update.commit()
    finally:
        update.discard()
    assert read_json(update.target / "manifest.json")["manual_change"]
    assert not update.folder.exists()


def test_unrecognized_transaction_directories_are_never_deleted(tmp_path: Path) -> None:
    update = _update(tmp_path)
    update.discard()
    update.folder.mkdir()
    note = update.folder / "my-file.txt"
    note.write_text("user-owned data")
    with pytest.raises(ConfigurationError, match="Unrecognized"):
        update.begin(sha256_file(update.target / "manifest.json"))
    assert note.read_text() == "user-owned data"
    assert (update.target / "output/pHmap.png").read_bytes() == b"last good figure"


def test_cleanup_error_after_install_is_recoverable_not_a_failed_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import shutil

    update = _update(tmp_path)
    remove = shutil.rmtree

    def fail_backup_cleanup(path: Path, *args: object, **kwargs: object) -> None:
        if path == update.previous:
            raise PermissionError("backup is temporarily locked")
        remove(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(shutil, "rmtree", fail_backup_cleanup)
        update.commit()
        assert update.committed and update.cleanup_error
        assert update.marker.exists()
        update.discard()
    assert (update.target / "output/pHmap.png").read_bytes() == b"new figure"
    recovery = RunUpdate(update.target)
    recovery.lock.acquire()
    try:
        recovery.recover()
        assert not update.folder.exists()
    finally:
        recovery.lock.release()


def test_update_refuses_to_remove_unrelated_files_from_a_calculation(tmp_path: Path) -> None:
    update = _update(tmp_path)
    update.discard()
    notes = update.target / "my-notes.txt"
    notes.write_text("keep my notes")
    with pytest.raises(ConfigurationError, match="additional user files"):
        update.begin(sha256_file(update.target / "manifest.json"))
    assert notes.read_text() == "keep my notes"
    assert (update.target / "output/pHmap.png").read_bytes() == b"last good figure"


def test_gui_start_recovers_cli_only_transactions_without_queue_records(tmp_path: Path) -> None:
    update = _update(tmp_path)
    update.target.rename(update.previous)
    update.lock.release()
    manager = JobManager(update.target.parent)
    try:
        manager.start()
        assert (update.target / "output/pHmap.png").read_bytes() == b"last good figure"
        assert not update.folder.exists()
        assert manager.history()[0]["run_id"] == "session"
    finally:
        manager.close()


def test_gui_recovery_does_not_discard_an_active_cli_update(tmp_path: Path) -> None:
    update = _update(tmp_path)
    manager = JobManager(update.target.parent)
    try:
        manager.start()
        assert update.pending.exists() and update.marker.exists()
    finally:
        manager.close()
        update.discard()
