"""Preflight checks for the local scientific toolchain."""

from __future__ import annotations

import platform
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class DoctorResult:
    """One preflight check result."""

    name: str
    ok: bool
    path: str | None
    version: str | None
    message: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class _ToolCheck:
    name: str
    executable: str
    version_args: tuple[str, ...]
    accepted_returncodes: tuple[int, ...] = (0,)


_TOOLS = (
    _ToolCheck("PDB2PQR", "pdb2pqr30", ("--version",)),
    # APBS 3.4.1's conda-forge binary prints a valid version and exits 13 for
    # informational flags. A real calculation still uses normal checked exits.
    _ToolCheck("APBS", "apbs", ("--version",), (0, 13)),
    _ToolCheck("PyMOL", "pymol", ("--version",)),
)


def _first_line(stdout: str, stderr: str) -> str | None:
    combined = "\n".join(part.strip() for part in (stdout, stderr) if part.strip())
    if not combined:
        return None
    lines = [line.strip() for line in combined.splitlines() if line.strip()]
    version_line = next(
        (
            line
            for line in lines
            if re.search(r"\b(?:PDB2PQR|pdb2pqr|APBS|PyMOL)\b.*\d", line)
        ),
        None,
    )
    return (version_line or lines[0])[:500]


def check_tool(
    name: str,
    executable: str,
    version_args: Sequence[str],
    *,
    timeout: float = 15,
    accepted_returncodes: Sequence[int] = (0,),
    cwd: Path | None = None,
) -> DoctorResult:
    """Resolve a tool and execute its lightweight version command."""

    resolved = shutil.which(executable)
    if resolved is None:
        return DoctorResult(
            name=name,
            ok=False,
            path=None,
            version=None,
            message=f"{executable!r} was not found on PATH",
        )
    try:
        completed = subprocess.run(
            [resolved, *version_args],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return DoctorResult(
            name=name,
            ok=False,
            path=resolved,
            version=None,
            message=f"Could not query version: {exc}",
        )
    version = _first_line(completed.stdout, completed.stderr)
    if completed.returncode not in accepted_returncodes:
        return DoctorResult(
            name=name,
            ok=False,
            path=resolved,
            version=version,
            message=f"Version command exited with status {completed.returncode}",
        )
    return DoctorResult(
        name=name,
        ok=True,
        path=resolved,
        version=version,
        message="ready",
    )


def run_doctor(output_base: Path | None = None) -> list[DoctorResult]:
    """Run Python, filesystem, and backend preflight checks."""

    python_ok = sys.version_info >= (3, 11)
    results = [
        DoctorResult(
            name="Python",
            ok=python_ok,
            path=sys.executable,
            version=platform.python_version(),
            message=(
                f"{platform.system()} {platform.machine()}"
                if python_ok
                else "Python 3.11 or newer is required"
            ),
        )
    ]
    base = (output_base or Path("runs")).expanduser().resolve()
    writable_parent = base if base.exists() else next(
        (parent for parent in base.parents if parent.exists()), base.parent
    )
    writable = writable_parent.is_dir() and os_access_writable(writable_parent)
    results.append(
        DoctorResult(
            name="Output directory",
            ok=writable,
            path=str(base),
            version=None,
            message=(
                f"parent is writable: {writable_parent}"
                if writable
                else f"parent is not writable: {writable_parent}"
            ),
        )
    )
    # APBS writes an ``io.mc`` file even for informational flags. Keep version
    # probes isolated so a read-only preflight never pollutes the project tree.
    with tempfile.TemporaryDirectory(prefix="phmap-doctor-") as probe_dir:
        results.extend(
            check_tool(
                tool.name,
                tool.executable,
                tool.version_args,
                accepted_returncodes=tool.accepted_returncodes,
                cwd=Path(probe_dir),
            )
            for tool in _TOOLS
        )
    return results


def os_access_writable(path: Path) -> bool:
    """Check filesystem write access without creating a probe file."""

    import os

    return os.access(path, os.W_OK)
