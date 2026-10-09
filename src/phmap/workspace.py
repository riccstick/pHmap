"""Isolated filesystem layout for a single pHmap run."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from secrets import token_hex

from phmap.errors import WorkspaceError

_SAFE_PART = re.compile(r"[^A-Za-z0-9_.-]+")


def safe_path_part(value: str) -> str:
    """Return a conservative, non-empty path component."""

    cleaned = _SAFE_PART.sub("-", value.strip()).strip(".-")
    return cleaned or "item"


def default_run_id() -> str:
    """Create a sortable run identifier with collision resistance."""

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{token_hex(3)}"


@dataclass(frozen=True, slots=True)
class TaskPaths:
    """Paths owned by one protein/pH calculation cell."""

    root: Path
    pqr: Path
    apbs_input: Path
    potential: Path
    tile: Path
    pdb2pqr_stdout: Path
    pdb2pqr_stderr: Path
    apbs_stdout: Path
    apbs_stderr: Path
    pymol_stdout: Path
    pymol_stderr: Path
    pymol_script: Path


@dataclass(frozen=True, slots=True)
class RunWorkspace:
    """All paths that a workflow run is permitted to mutate."""

    root: Path
    run_id: str
    inputs: Path
    work: Path
    renders: Path
    logs: Path
    output: Path
    manifest: Path

    @classmethod
    def create(cls, base: Path, run_id: str | None = None, *, exact: bool = False) -> RunWorkspace:
        """Create a new workspace.

        If ``exact`` is true, ``base`` is the run directory itself. Otherwise a
        generated or supplied run ID is created beneath ``base``. Existing paths
        are rejected so a new run can never overwrite another run implicitly.
        """

        resolved_base = base.expanduser().resolve()
        chosen_id = safe_path_part(run_id or default_run_id())
        root = resolved_base if exact else resolved_base / chosen_id
        if root.exists():
            raise WorkspaceError(f"Run directory already exists: {root}")
        try:
            root.mkdir(parents=True, exist_ok=False)
            directories = {
                "inputs": root / "inputs",
                "work": root / "work",
                "renders": root / "renders",
                "logs": root / "logs",
                "output": root / "output",
            }
            for directory in directories.values():
                directory.mkdir()
        except OSError as exc:
            raise WorkspaceError(f"Could not create run directory {root}: {exc}") from exc
        return cls(
            root=root,
            run_id=chosen_id if not exact else root.name,
            inputs=directories["inputs"],
            work=directories["work"],
            renders=directories["renders"],
            logs=directories["logs"],
            output=directories["output"],
            manifest=root / "manifest.json",
        )

    def stage_input(self, source: Path, *, name: str | None = None) -> Path:
        """Copy an input into the run without overwriting an existing file."""

        source = source.expanduser().resolve()
        if not source.is_file():
            raise WorkspaceError(f"Input file does not exist: {source}")
        destination_name = name or source.name
        if (
            not destination_name
            or destination_name in {".", ".."}
            or Path(destination_name).name != destination_name
        ):
            raise WorkspaceError(
                f"Staged input name must be one safe path component: {destination_name!r}"
            )
        destination = self.inputs / destination_name
        if destination.exists():
            raise WorkspaceError(f"Staged input already exists: {destination}")
        try:
            shutil.copy2(source, destination)
        except OSError as exc:
            raise WorkspaceError(f"Could not stage {source}: {exc}") from exc
        return destination

    def task_paths(self, protein_id: str, ph_key: str) -> TaskPaths:
        """Create deterministic directories for one protein/pH cell."""

        protein_part = safe_path_part(protein_id)
        ph_part = f"ph-{safe_path_part(ph_key)}"
        root = self.work / protein_part / ph_part
        render_dir = self.renders / protein_part
        log_dir = self.logs / protein_part / ph_part
        for directory in (root, render_dir, log_dir):
            directory.mkdir(parents=True, exist_ok=True)
        return TaskPaths(
            root=root,
            pqr=root / "model.pqr",
            apbs_input=root / "apbs.in",
            # PDB2PQR 3.7 writes this exact target into its generated APBS
            # input. Keeping the native name avoids rewriting scientific input.
            potential=root / "model.pqr.dx",
            tile=render_dir / f"{ph_part}.png",
            pdb2pqr_stdout=log_dir / "pdb2pqr.stdout.log",
            pdb2pqr_stderr=log_dir / "pdb2pqr.stderr.log",
            apbs_stdout=log_dir / "apbs.stdout.log",
            apbs_stderr=log_dir / "apbs.stderr.log",
            pymol_stdout=log_dir / "pymol.stdout.log",
            pymol_stderr=log_dir / "pymol.stderr.log",
            pymol_script=root / "render.pml",
        )
