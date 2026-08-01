"""APBS backend adapter."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .artifacts import require_nonempty_file
from .errors import BackendValidationError
from .runner import CommandResult, CommandRunner, VersionInfo, discover_executable


@dataclass(frozen=True, slots=True)
class ApbsRequest:
    """Inputs and expected DX output for one APBS calculation."""

    apbs_input_path: Path
    dx_path: Path
    work_dir: Path | None = None
    apbs_output_path: Path | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "apbs_input_path", Path(self.apbs_input_path))
        object.__setattr__(self, "dx_path", Path(self.dx_path))
        if self.work_dir is not None:
            object.__setattr__(self, "work_dir", Path(self.work_dir))
        if self.apbs_output_path is not None:
            object.__setattr__(self, "apbs_output_path", Path(self.apbs_output_path))
        paths = {self.apbs_input_path.resolve(), self.dx_path.resolve()}
        if self.apbs_output_path is not None:
            paths.add(self.apbs_output_path.resolve())
        expected = 3 if self.apbs_output_path is not None else 2
        if len(paths) != expected:
            raise BackendValidationError("APBS input and outputs must use different paths")


@dataclass(frozen=True, slots=True)
class ApbsArtifacts:
    """Validated products of an APBS calculation."""

    dx_path: Path
    command: CommandResult
    apbs_output_path: Path | None = None


def build_apbs_argv(
    request: ApbsRequest,
    executable: str | os.PathLike[str] = "apbs",
) -> tuple[str, ...]:
    """Build an APBS invocation without shell syntax."""

    command = [os.fspath(executable)]
    if request.apbs_output_path is not None:
        command.append(f"--output-file={request.apbs_output_path.resolve()}")
    command.append(str(request.apbs_input_path.resolve()))
    return tuple(command)


class ApbsBackend:
    """Checked, out-of-process APBS execution."""

    def __init__(
        self,
        runner: CommandRunner,
        executable: str | os.PathLike[str] = "apbs",
    ) -> None:
        self.runner = runner
        self.executable = discover_executable(executable)

    def run(
        self,
        request: ApbsRequest,
        *,
        stage: str = "apbs",
        timeout: float | None = None,
    ) -> ApbsArtifacts:
        require_nonempty_file(request.apbs_input_path, "APBS input")
        work_dir = (
            request.work_dir.resolve()
            if request.work_dir is not None
            else request.apbs_input_path.resolve().parent
        )
        work_dir.mkdir(parents=True, exist_ok=True)
        request.dx_path.resolve().parent.mkdir(parents=True, exist_ok=True)
        if request.apbs_output_path is not None:
            request.apbs_output_path.resolve().parent.mkdir(parents=True, exist_ok=True)

        result = self.runner.run(
            stage,
            build_apbs_argv(request, self.executable),
            cwd=work_dir,
            timeout=timeout,
        )
        dx = require_nonempty_file(request.dx_path, "OpenDX potential output")
        apbs_output = None
        if request.apbs_output_path is not None:
            apbs_output = require_nonempty_file(
                request.apbs_output_path, "APBS output log"
            )
        return ApbsArtifacts(dx, result, apbs_output)

    def version(
        self, *, timeout: float = 10.0, cwd: str | Path | None = None
    ) -> VersionInfo:
        return self.runner.probe_version(self.executable, timeout=timeout, cwd=cwd)


APBSBackend = ApbsBackend
