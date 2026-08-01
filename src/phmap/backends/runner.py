"""Safe, observable execution of external scientific commands."""

from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .errors import (
    BackendValidationError,
    CommandExecutionError,
    CommandStartError,
    CommandTimeoutError,
    ExecutableNotFoundError,
    VersionProbeError,
)

_STAGE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")


def discover_executable(
    executable: str | os.PathLike[str],
    *,
    search_path: str | None = None,
) -> Path:
    """Resolve an executable without invoking a shell.

    A value containing a path separator is treated as an explicit path;
    otherwise it is looked up using ``PATH`` (or ``search_path`` when given).
    """

    raw = os.fspath(executable)
    if not isinstance(raw, str):
        raise ExecutableNotFoundError(repr(raw))
    if not raw or "\x00" in raw:
        raise ExecutableNotFoundError(raw)

    has_separator = os.sep in raw or (os.altsep is not None and os.altsep in raw)
    if has_separator:
        candidate = Path(raw).expanduser().resolve()
        resolved = str(candidate)
    else:
        located = shutil.which(raw, path=search_path)
        if located is None:
            raise ExecutableNotFoundError(raw)
        candidate = Path(located).resolve()
        resolved = located

    if not candidate.is_file() or not os.access(candidate, os.X_OK):
        raise ExecutableNotFoundError(resolved)
    return candidate


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Metadata for a completed external command."""

    stage: str
    argv: tuple[str, ...]
    cwd: Path
    returncode: int
    stdout_path: Path
    stderr_path: Path
    duration_seconds: float

    @property
    def succeeded(self) -> bool:
        return self.returncode == 0

    def read_stdout(self) -> str:
        return self.stdout_path.read_text(encoding="utf-8", errors="replace")

    def read_stderr(self) -> str:
        return self.stderr_path.read_text(encoding="utf-8", errors="replace")


@dataclass(frozen=True, slots=True)
class VersionInfo:
    """Result of a lightweight executable version probe."""

    executable: Path
    argv: tuple[str, ...]
    returncode: int
    output: str


@dataclass(slots=True)
class CommandRunner:
    """Run argv-only commands and retain stdout/stderr in per-stage files."""

    log_dir: Path
    default_timeout: float = 900.0

    def __post_init__(self) -> None:
        self.log_dir = Path(self.log_dir)
        if (
            isinstance(self.default_timeout, bool)
            or not math.isfinite(self.default_timeout)
            or self.default_timeout <= 0
        ):
            raise BackendValidationError("default_timeout must be finite and positive")

    def run(
        self,
        stage: str,
        argv: Sequence[str | os.PathLike[str]],
        *,
        cwd: str | Path,
        timeout: float | None = None,
        check: bool = True,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        """Execute an argv sequence without shell interpretation.

        ``stdout`` and ``stderr`` are always written to ``log_dir``. A non-zero
        status raises :class:`CommandExecutionError` by default while retaining
        the complete :class:`CommandResult` on the exception.
        """

        self._validate_stage(stage)
        command = self._normalise_argv(argv)
        work_dir = Path(cwd).resolve()
        if not work_dir.is_dir():
            raise BackendValidationError(
                f"Command working directory does not exist: {work_dir}"
            )

        effective_timeout = self.default_timeout if timeout is None else timeout
        if (
            isinstance(effective_timeout, bool)
            or not math.isfinite(effective_timeout)
            or effective_timeout <= 0
        ):
            raise BackendValidationError("timeout must be finite and positive")

        child_env = None
        if env is not None:
            child_env = os.environ.copy()
            for key, value in env.items():
                if (
                    not isinstance(key, str)
                    or not key
                    or "=" in key
                    or "\x00" in key
                ):
                    raise BackendValidationError(f"Invalid environment key: {key!r}")
                if not isinstance(value, str):
                    raise BackendValidationError(
                        f"Environment value for {key!r} must be a string"
                    )
                if "\x00" in value:
                    raise BackendValidationError(
                        f"Environment value for {key!r} contains a NUL byte"
                    )
                child_env[key] = value

        self.log_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = self.log_dir / f"{stage}.stdout.log"
        stderr_path = self.log_dir / f"{stage}.stderr.log"
        started = time.monotonic()

        try:
            with stdout_path.open("wb") as stdout_stream, stderr_path.open(
                "wb"
            ) as stderr_stream:
                try:
                    completed = subprocess.run(
                        command,
                        cwd=work_dir,
                        env=child_env,
                        stdin=subprocess.DEVNULL,
                        stdout=stdout_stream,
                        stderr=stderr_stream,
                        shell=False,
                        check=False,
                        timeout=effective_timeout,
                    )
                except subprocess.TimeoutExpired as exc:
                    message = (
                        f"\nphmap: command timed out after "
                        f"{effective_timeout:g} seconds\n"
                    ).encode()
                    stderr_stream.write(message)
                    stderr_stream.flush()
                    raise CommandTimeoutError(
                        stage=stage,
                        timeout=effective_timeout,
                        stdout_path=stdout_path,
                        stderr_path=stderr_path,
                    ) from exc
        except CommandTimeoutError:
            raise
        except OSError as exc:
            raise CommandStartError(stage, command[0], exc) from exc

        result = CommandResult(
            stage=stage,
            argv=command,
            cwd=work_dir,
            returncode=completed.returncode,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            duration_seconds=time.monotonic() - started,
        )
        if check and not result.succeeded:
            raise CommandExecutionError(result)
        return result

    @staticmethod
    def probe_version(
        executable: str | os.PathLike[str],
        *,
        args: Sequence[str] = ("--version",),
        cwd: str | Path | None = None,
        timeout: float = 10.0,
        check: bool = False,
    ) -> VersionInfo:
        """Discover *executable* and capture its version output without a shell."""

        resolved = discover_executable(executable)
        if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
            raise BackendValidationError("version timeout must be finite and positive")
        suffix = CommandRunner._normalise_argv(args, allow_empty=True)
        argv = (str(resolved), *suffix)
        work_dir = None if cwd is None else Path(cwd).resolve()
        if work_dir is not None and not work_dir.is_dir():
            raise BackendValidationError(
                f"Version probe working directory does not exist: {work_dir}"
            )
        try:
            completed = subprocess.run(
                argv,
                cwd=work_dir,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                shell=False,
                check=False,
                timeout=timeout,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired as exc:
            raise VersionProbeError(
                f"Version probe for {resolved} exceeded {timeout:g}s"
            ) from exc
        except OSError as exc:
            raise VersionProbeError(
                f"Could not probe version of {resolved}: {exc}"
            ) from exc

        output = "\n".join(
            part.strip() for part in (completed.stdout, completed.stderr) if part.strip()
        )
        if check and completed.returncode != 0:
            raise VersionProbeError(
                f"Version probe for {resolved} exited with status "
                f"{completed.returncode}: {output}"
            )
        return VersionInfo(
            executable=resolved,
            argv=argv,
            returncode=completed.returncode,
            output=output,
        )

    @staticmethod
    def _validate_stage(stage: str) -> None:
        if not isinstance(stage, str) or not _STAGE_PATTERN.fullmatch(stage):
            raise BackendValidationError(
                "stage must contain only letters, digits, '.', '_' and '-', "
                "and must begin with a letter or digit"
            )

    @staticmethod
    def _normalise_argv(
        argv: Sequence[str | os.PathLike[str]], *, allow_empty: bool = False
    ) -> tuple[str, ...]:
        try:
            raw_command = tuple(os.fspath(argument) for argument in argv)
        except TypeError as exc:
            raise BackendValidationError("argv entries must be path-like strings") from exc
        if any(not isinstance(argument, str) for argument in raw_command):
            raise BackendValidationError("argv entries must be strings, not bytes")
        command = raw_command
        if not command and not allow_empty:
            raise BackendValidationError("argv must contain an executable")
        for argument in command:
            if "\x00" in argument:
                raise BackendValidationError("argv entries may not contain NUL bytes")
        if command and not command[0]:
            raise BackendValidationError("argv executable may not be empty")
        return command
