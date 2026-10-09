"""PDB2PQR/PROPKA backend adapter."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .artifacts import require_nonempty_file
from .errors import BackendValidationError
from .runner import CommandResult, CommandRunner, VersionInfo, discover_executable

_FORCE_FIELD_PATTERN = re.compile(r"[A-Za-z0-9_-]+\Z")


def _path(value: str | os.PathLike[str]) -> Path:
    return Path(value)


def _ph_decimal(value: Decimal | float | int | str) -> Decimal:
    if isinstance(value, bool):
        raise BackendValidationError("PDB2PQR pH must be a decimal number")
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise BackendValidationError("PDB2PQR pH must be a decimal number") from exc
    if not parsed.is_finite() or not Decimal("0") <= parsed <= Decimal("14"):
        raise BackendValidationError("PDB2PQR pH must be between 0 and 14")
    if Decimal(str(float(parsed))) != parsed:
        raise BackendValidationError(
            "PDB2PQR pH has more precision than its floating-point parser can represent"
        )
    return parsed


def pdb2pqr_dx_path(pqr_path: str | os.PathLike[str]) -> Path:
    """Return the DX path written by PDB2PQR 3.7's generated APBS input."""

    return Path(f"{Path(pqr_path)}.dx")


@dataclass(frozen=True, slots=True)
class Pdb2pqrRequest:
    """Inputs and expected outputs for one PDB2PQR calculation."""

    pdb_path: Path
    pqr_path: Path
    apbs_input_path: Path
    ph: Decimal | float | int | str
    force_field: str = "PARSE"
    ligand_path: Path | None = None
    drop_water: bool = True
    work_dir: Path | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "pdb_path", _path(self.pdb_path))
        object.__setattr__(self, "pqr_path", _path(self.pqr_path))
        object.__setattr__(self, "apbs_input_path", _path(self.apbs_input_path))
        if self.ligand_path is not None:
            object.__setattr__(self, "ligand_path", _path(self.ligand_path))
        if self.work_dir is not None:
            object.__setattr__(self, "work_dir", _path(self.work_dir))
        object.__setattr__(self, "ph", _ph_decimal(self.ph))
        if not _FORCE_FIELD_PATTERN.fullmatch(self.force_field):
            raise BackendValidationError(
                "PDB2PQR force_field contains unsupported characters"
            )
        destinations = {
            self.pdb_path.resolve(),
            self.pqr_path.resolve(),
            self.apbs_input_path.resolve(),
        }
        if len(destinations) != 3:
            raise BackendValidationError(
                "PDB input, PQR output, and APBS input must be different paths"
            )
        if self.pqr_path.resolve().parent != self.apbs_input_path.resolve().parent:
            raise BackendValidationError(
                "PQR output and generated APBS input must share a directory; "
                "PDB2PQR writes the PQR basename into its APBS input"
            )


@dataclass(frozen=True, slots=True)
class Pdb2pqrArtifacts:
    """Validated products of a PDB2PQR calculation."""

    pqr_path: Path
    apbs_input_path: Path
    command: CommandResult


def build_pdb2pqr_argv(
    request: Pdb2pqrRequest,
    executable: str | os.PathLike[str] = "pdb2pqr30",
) -> tuple[str, ...]:
    """Build the modern PDB2PQR CLI invocation for *request*."""

    command = [
        os.fspath(executable),
        f"--ff={request.force_field}",
        f"--with-ph={request.ph}",
        "--titration-state-method=propka",
        f"--apbs-input={request.apbs_input_path.resolve()}",
    ]
    if request.drop_water:
        command.append("--drop-water")
    if request.ligand_path is not None:
        command.append(f"--ligand={request.ligand_path.resolve()}")
    command.extend((str(request.pdb_path.resolve()), str(request.pqr_path.resolve())))
    return tuple(command)


class Pdb2pqrBackend:
    """Checked, out-of-process PDB2PQR execution."""

    def __init__(
        self,
        runner: CommandRunner,
        executable: str | os.PathLike[str] = "pdb2pqr30",
    ) -> None:
        self.runner = runner
        self.executable = discover_executable(executable)

    def run(
        self,
        request: Pdb2pqrRequest,
        *,
        stage: str = "pdb2pqr",
        timeout: float | None = None,
    ) -> Pdb2pqrArtifacts:
        require_nonempty_file(request.pdb_path, "PDB input")
        if request.ligand_path is not None:
            require_nonempty_file(request.ligand_path, "ligand input")

        work_dir = (
            request.work_dir.resolve()
            if request.work_dir is not None
            else request.pqr_path.resolve().parent
        )
        work_dir.mkdir(parents=True, exist_ok=True)
        request.pqr_path.resolve().parent.mkdir(parents=True, exist_ok=True)
        request.apbs_input_path.resolve().parent.mkdir(parents=True, exist_ok=True)

        result = self.runner.run(
            stage,
            build_pdb2pqr_argv(request, self.executable),
            cwd=work_dir,
            timeout=timeout,
        )
        pqr = require_nonempty_file(request.pqr_path, "PQR output")
        apbs_input = require_nonempty_file(request.apbs_input_path, "APBS input")
        return Pdb2pqrArtifacts(pqr, apbs_input, result)

    def version(
        self, *, timeout: float = 10.0, cwd: str | Path | None = None
    ) -> VersionInfo:
        return self.runner.probe_version(self.executable, timeout=timeout, cwd=cwd)


# Common spelling retained as an import-friendly alias.
PDB2PQRBackend = Pdb2pqrBackend
