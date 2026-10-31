"""Locked, recoverable replacement of one calculation, without version history."""

from __future__ import annotations

import copy
import shutil
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

from phmap.errors import ConfigurationError
from phmap.json_state import _retry_windows_file_operation, read_json, write_json
from phmap.locking import LocalLock
from phmap.manifest import sha256_file, utc_now
from phmap.workspace import safe_path_part

if TYPE_CHECKING:
    from phmap.saved_runs import SavedRun


class RunUpdate:
    """Build in a private sibling workspace; install only a completed result.

    The small journal covers the two directory renames. A crash before installation
    restores the previous result; a crash after installation finishes cleanup.
    Only this transaction's marked directory may be removed.
    """

    def __init__(self, target: Path) -> None:
        self.target = target.expanduser().absolute()
        self.folder = self.target.parent / ".phmap-updates" / self.target.name
        self.pending = self.folder / "result"
        self.previous = self.folder / "previous"
        self.marker = self.folder / "transaction.json"
        self.lock = LocalLock(self.folder.parent / ".locks" / f"{self.target.name}.lock")
        self.committed = False
        self.cleanup_error: str | None = None
        self.source_digest: str | None = None

    def _metadata(self) -> dict[str, Any]:
        if (
            self.folder.parent.is_symlink()
            or self.folder.is_symlink()
            or self.marker.is_symlink()
            or self.target.is_symlink()
        ):
            raise ConfigurationError("Update storage must not use symbolic links")
        data = read_json(self.marker)
        if (
            self.folder.is_symlink()
            or self.marker.is_symlink()
            or data.get("owner") != "phmap-run-update-1"
            or data.get("target") != str(self.target)
        ):
            raise ConfigurationError(
                f"Unrecognized update directory; left untouched: {self.folder}"
            )
        for path in (self.pending, self.previous):
            if path.is_symlink():
                raise ConfigurationError("An update directory must not be a symbolic link")
        return data

    def _remove(self) -> None:
        self._metadata()
        # Keep the journal until all large data is gone, so interrupted cleanup
        # remains recoverable (particularly Windows sharing violations).
        for name in ("result", "previous", "uploads"):
            path = self.folder / name
            if path.exists():
                _retry_windows_file_operation(partial(shutil.rmtree, path))
        self.marker.unlink()
        self.folder.rmdir()

    def recover(self) -> None:
        """Caller must hold the per-calculation lock."""
        if not self.folder.exists():
            return
        if not self.marker.exists() and not any(self.folder.iterdir()):
            self.folder.rmdir()
            return
        data = self._metadata()
        digest = sha256_file(self.target / "manifest.json") if self.target.is_dir() else None
        if digest == data.get("replacement_sha256") and digest is not None:
            self._remove()  # Already installed; no historical backup is retained.
        elif not self.target.exists() and self.previous.is_dir():
            if sha256_file(self.previous / "manifest.json") != data["source_sha256"]:
                raise ConfigurationError("Recovery checksum mismatch; update files were preserved")
            _retry_windows_file_operation(lambda: self.previous.rename(self.target))
            self._remove()
        elif self.target.is_dir() and not self.previous.exists():
            self._remove()  # Uncommitted work, including cancelled/interrupted workers.
        else:
            raise ConfigurationError(
                f"Cannot safely recover update; files preserved in {self.folder}"
            )

    def begin(self, expected_digest: str) -> None:
        if self.folder.parent.is_symlink() or (self.folder.parent / ".locks").is_symlink():
            raise ConfigurationError("Update storage must not use symbolic links")
        self.lock.acquire()
        try:
            self.recover()
            self.source_digest = expected_digest
            if (
                self.target.is_symlink()
                or sha256_file(self.target / "manifest.json") != expected_digest
            ):
                raise ConfigurationError("Calculation changed; reload its settings before updating")
            known = {"manifest.json", "inputs", "work", "renders", "logs", "output", ".DS_Store"}
            if any(path.name not in known for path in self.target.iterdir()):
                raise ConfigurationError(
                    "Calculation folder contains additional user files; "
                    "move them out before updating. "
                    "No files have been replaced."
                )
            self.folder.mkdir(parents=True, exist_ok=False)
            write_json(
                self.marker,
                {
                    "owner": "phmap-run-update-1",
                    "target": str(self.target),
                    "source_sha256": expected_digest,
                },
            )
        except BaseException:
            self.lock.release()
            raise

    def discard(self) -> None:
        try:
            self.recover()
        except (OSError, ConfigurationError) as exc:
            if not self.committed:
                raise
            self.cleanup_error = str(exc)
        finally:
            self.lock.release()

    def commit(self) -> None:
        """The caller has stopped its worker and checked cancellation."""
        data = self._metadata()
        if sha256_file(self.target / "manifest.json") != data["source_sha256"]:
            raise ConfigurationError(
                "Calculation changed while updating; previous result was not replaced"
            )
        manifest = read_json(self.pending / "manifest.json")
        if manifest.get("status") != "completed":
            raise ConfigurationError("Only a successfully completed update can replace a result")
        original = read_json(self.target / "manifest.json")
        finder_metadata = self.target / ".DS_Store"
        if finder_metadata.is_file() and not finder_metadata.is_symlink():
            shutil.copyfile(finder_metadata, self.pending / ".DS_Store")
        relocate_manifest(manifest, self.pending, self.target)
        manifest.update(
            run_id=self.target.name, created_at=original["created_at"], updated_at=utc_now()
        )
        manifest.pop("based_on", None)
        manifest["last_update"] = {
            "operation": manifest.get("plan", {}).get("operation", "full"),
            "previous_manifest_sha256": data["source_sha256"],
            "finished_at": utc_now(),
        }
        write_json(self.pending / "manifest.json", manifest)
        data["replacement_sha256"] = sha256_file(self.pending / "manifest.json")
        write_json(self.marker, data)
        try:
            _retry_windows_file_operation(lambda: self.target.rename(self.previous))
            _retry_windows_file_operation(lambda: self.pending.rename(self.target))
        except BaseException:
            self.recover()
            raise
        self.committed = True
        try:
            self._remove()
        except (OSError, ConfigurationError) as exc:
            self.cleanup_error = str(exc)  # Installed successfully; finish cleanup on recovery.


def recover_run_update(target: Path, *, skip_busy: bool = False) -> None:
    """Restore a journaled calculation before a caller needs its manifest."""
    update = RunUpdate(target.expanduser().resolve())
    if not update.folder.exists():
        return
    if update.folder.parent.is_symlink() or (update.folder.parent / ".locks").is_symlink():
        raise ConfigurationError("Update storage must not use symbolic links")
    try:
        update.lock.acquire()
    except ConfigurationError:
        if skip_busy:
            return
        raise
    try:
        update.recover()
    finally:
        update.lock.release()


def recover_run_updates(runs_dir: Path) -> None:
    """Discover owned journals, including CLI-only runs absent from GUI records."""
    folder = runs_dir / ".phmap-updates"
    if not folder.is_dir() or folder.is_symlink():
        return
    for entry in folder.iterdir():
        if entry.name.startswith(".") or entry.is_symlink() or not entry.is_dir():
            continue
        if read_json(entry / "transaction.json").get("owner") == "phmap-run-update-1":
            recover_run_update(runs_dir / entry.name, skip_busy=True)


def relocate_manifest(data: dict[str, Any], old_root: Path, new_root: Path) -> None:
    """Relocate artifact pointers, while preserving the exact executed argv."""
    for records, key in (
        (data.get("inputs", []), "staged_path"),
        (data.get("outputs", []), "path"),
    ):
        for record in records:
            path = Path(record[key])
            if path.is_relative_to(old_root):
                record[key] = str(new_root / path.relative_to(old_root))
    for task in data.get("tasks", []):
        for stage in task.get("stages", []):
            for output in stage.get("outputs", []):
                path = Path(output["path"])
                if path.is_relative_to(old_root):
                    output["path"] = str(new_root / path.relative_to(old_root))
            for key in ("stdout", "stderr"):
                log_path = Path(stage[key]) if key in stage else None
                if log_path is not None and log_path.is_relative_to(old_root):
                    stage[key] = str(new_root / log_path.relative_to(old_root))


def preserve_artifacts(source: SavedRun, pending: Path) -> None:
    """Keep verified raw/scientific artifacts through compose → render iterations.

    Missing optional artifacts stay missing; modified retained files are rejected.
    Removed rows/columns are not carried into the new calculation.
    """
    from phmap.saved_runs import SavedArtifact

    current = read_json(pending / "manifest.json")
    selected_ids = {item["id"] for item in current["configuration"]["proteins"]}
    inputs = []
    for record in source.manifest.get("inputs", []):
        if record["role"].split(":")[-1] not in selected_ids:
            continue
        name = Path(record["staged_path"].replace("\\", "/")).name
        path = (source.root / "inputs" / name).resolve()
        if not path.is_relative_to(source.root) or not path.is_file():
            continue
        target = pending / "inputs" / name
        SavedArtifact(path, record["sha256"], record["size"]).copy_to(target)
        inputs.append({**record, "staged_path": str(target)})
    current["inputs"] = inputs
    tasks = {(task["protein_id"], task["ph"]): task for task in source.manifest["tasks"]}
    for task in current["tasks"]:
        previous = tasks[task["protein_id"], task["ph"]]
        new_stages = {stage["name"]: stage for stage in task["stages"]}
        stages = []
        for stage in previous["stages"]:
            name = stage["name"]
            if name in new_stages and not new_stages[name].get("reused"):
                stages.append(new_stages.pop(name))
                continue
            retained = copy.deepcopy(stage)
            retained["outputs"] = []
            for output in stage.get("outputs", []):
                filename = Path(output["path"].replace("\\", "/")).name
                allowed = {
                    "pdb2pqr": {"model.pqr", "apbs.in"},
                    "apbs": {"model.pqr.dx"},
                    "pymol": {"render.pml"},
                }
                ph_part = f"ph-{safe_path_part(task['ph'])}"
                if name == "pymol" and filename == f"{ph_part}.png":
                    relative = Path("renders") / task["protein_id"] / filename
                elif filename in allowed.get(name, set()):
                    relative = Path("work") / task["protein_id"] / ph_part / filename
                else:
                    continue
                path = (source.root / relative).resolve()
                if not path.is_file():
                    continue
                artifact = source.artifact(stage, relative)
                target = pending / relative
                if not target.exists():
                    artifact.copy_to(target)
                retained["outputs"].append({**output, "path": str(target)})
            if name == "pymol" and name in new_stages:
                new_stage = new_stages.pop(name)
                present = {item["path"] for item in new_stage["outputs"]}
                new_stage["outputs"].extend(
                    item for item in retained["outputs"] if item["path"] not in present
                )
                retained = new_stage
            else:
                new_stages.pop(name, None)
            for stream in ("stdout", "stderr"):
                filename = f"{name}.{stream}.log"
                relative = (
                    Path("logs")
                    / task["protein_id"]
                    / f"ph-{safe_path_part(task['ph'])}"
                    / filename
                )
                log = (source.root / relative).resolve()
                target = pending / relative
                if log.is_file() and log.is_relative_to(source.root) and not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(log, target)
            stages.append(retained)
        stages.extend(new_stages.values())
        task["stages"] = stages
    write_json(pending / "manifest.json", current)
