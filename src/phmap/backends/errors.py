"""Exceptions raised by external scientific backends."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .runner import CommandResult


class BackendError(RuntimeError):
    """Base class for backend integration errors."""


class BackendValidationError(BackendError, ValueError):
    """A backend request is invalid before an external command is started."""


class ExecutableNotFoundError(BackendError, FileNotFoundError):
    """An external executable could not be found or is not executable."""

    def __init__(self, executable: str) -> None:
        self.executable = executable
        super().__init__(f"Executable not found or not executable: {executable!r}")


class CommandStartError(BackendError):
    """A command could not be started."""

    def __init__(self, stage: str, executable: str, reason: OSError) -> None:
        self.stage = stage
        self.executable = executable
        self.reason = reason
        super().__init__(
            f"Stage {stage!r} could not start {executable!r}: {reason}"
        )


class CommandExecutionError(BackendError):
    """A command completed with a non-zero exit status."""

    def __init__(self, result: CommandResult) -> None:
        self.result = result
        super().__init__(
            f"Stage {result.stage!r} exited with status {result.returncode}; "
            f"stdout: {result.stdout_path}; stderr: {result.stderr_path}"
        )


class CommandTimeoutError(BackendError, TimeoutError):
    """A command exceeded its configured timeout."""

    def __init__(
        self,
        *,
        stage: str,
        timeout: float,
        stdout_path: Path,
        stderr_path: Path,
    ) -> None:
        self.stage = stage
        self.timeout = timeout
        self.stdout_path = stdout_path
        self.stderr_path = stderr_path
        super().__init__(
            f"Stage {stage!r} exceeded its {timeout:g}s timeout; "
            f"stdout: {stdout_path}; stderr: {stderr_path}"
        )


class VersionProbeError(BackendError):
    """An executable version probe failed or timed out."""


class ArtifactError(BackendError):
    """An expected backend input or output artifact is missing or invalid."""

    def __init__(self, path: Path, description: str, reason: str) -> None:
        self.path = path
        self.description = description
        self.reason = reason
        super().__init__(f"Invalid {description} artifact at {path}: {reason}")
