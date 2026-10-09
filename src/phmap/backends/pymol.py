"""Headless PyMOL electrostatic-surface renderer."""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .artifacts import require_nonempty_file, require_png
from .errors import BackendValidationError
from .runner import CommandResult, CommandRunner, VersionInfo, discover_executable

_COLOR_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_]*\Z|0x[0-9A-Fa-f]{6}\Z")
_LIGAND_REPRESENTATIONS = {"sticks", "lines", "dots", "spheres"}


def _pml_string(value: str | Path) -> str:
    """Return a quoted PML string after rejecting command separators."""

    raw = str(value)
    if any(character in raw for character in ("\x00", "\r", "\n")):
        raise BackendValidationError("PyMOL paths may not contain control characters")
    return json.dumps(raw, ensure_ascii=True)


def _pml_number(value: float) -> str:
    if isinstance(value, bool) or not math.isfinite(value):
        raise BackendValidationError("PyMOL numeric values must be finite")
    return format(value, ".12g")


def _validate_color(color: str) -> str:
    if not isinstance(color, str) or not _COLOR_PATTERN.fullmatch(color):
        raise BackendValidationError(f"Unsupported PyMOL color name: {color!r}")
    return color


@dataclass(frozen=True, slots=True)
class PyMOLRenderOptions:
    """Validated rendering settings; no arbitrary PML is accepted."""

    width: int = 500
    height: int = 500
    dpi: int = 300
    level: float = 5.0
    ramp_colors: tuple[str, str, str] = ("red", "white", "blue")
    background: str = "white"
    transparent_background: bool = False
    surface_mode: int = 0
    surface_solvent: bool = False
    surface_ramp_above_mode: bool = True
    ligand_representation: Literal["sticks", "lines", "dots", "spheres"] | None = (
        "sticks"
    )
    view: tuple[float, ...] | None = None
    ray_trace: bool = True

    def __post_init__(self) -> None:
        for name, value in (("width", self.width), ("height", self.height)):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 1 <= value <= 16_384
            ):
                raise BackendValidationError(f"PyMOL {name} must be between 1 and 16384")
        if (
            isinstance(self.dpi, bool)
            or not isinstance(self.dpi, int)
            or not 1 <= self.dpi <= 4_800
        ):
            raise BackendValidationError("PyMOL dpi must be between 1 and 4800")
        if (
            isinstance(self.level, bool)
            or not math.isfinite(self.level)
            or self.level <= 0
        ):
            raise BackendValidationError("PyMOL potential level must be positive")
        if len(self.ramp_colors) != 3:
            raise BackendValidationError("PyMOL ramp requires exactly three colors")
        for color in self.ramp_colors:
            _validate_color(color)
        _validate_color(self.background)
        if isinstance(self.surface_mode, bool) or self.surface_mode not in range(5):
            raise BackendValidationError("PyMOL surface_mode must be 0, 1, 2, 3, or 4")
        if (
            self.ligand_representation is not None
            and self.ligand_representation not in _LIGAND_REPRESENTATIONS
        ):
            raise BackendValidationError("Unsupported PyMOL ligand representation")
        if self.view is not None:
            if len(self.view) != 18:
                raise BackendValidationError(
                    "A PyMOL view matrix must contain exactly 18 numbers"
                )
            for matrix_value in self.view:
                if isinstance(matrix_value, bool) or not math.isfinite(matrix_value):
                    raise BackendValidationError(
                        "PyMOL view matrix values must all be finite"
                    )


@dataclass(frozen=True, slots=True)
class PyMOLRenderRequest:
    """Inputs, script, and PNG output for one headless PyMOL render."""

    pqr_path: Path
    dx_path: Path
    png_path: Path
    options: PyMOLRenderOptions = PyMOLRenderOptions()
    script_path: Path | None = None
    work_dir: Path | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "pqr_path", Path(self.pqr_path))
        object.__setattr__(self, "dx_path", Path(self.dx_path))
        object.__setattr__(self, "png_path", Path(self.png_path))
        if self.script_path is not None:
            object.__setattr__(self, "script_path", Path(self.script_path))
        if self.work_dir is not None:
            object.__setattr__(self, "work_dir", Path(self.work_dir))
        inputs_and_output = {
            self.pqr_path.resolve(),
            self.dx_path.resolve(),
            self.png_path.resolve(),
        }
        if len(inputs_and_output) != 3:
            raise BackendValidationError("PQR, DX, and PNG paths must be different")
        script = self.resolved_script_path
        if script.resolve() in inputs_and_output:
            raise BackendValidationError(
                "PyMOL script path must differ from all input and output paths"
            )

    @property
    def resolved_script_path(self) -> Path:
        if self.script_path is not None:
            return self.script_path
        return self.png_path.with_suffix(".pml")


@dataclass(frozen=True, slots=True)
class PyMOLRenderArtifacts:
    """Validated products of a PyMOL render."""

    png_path: Path
    script_path: Path
    command: CommandResult


def build_pymol_script(request: PyMOLRenderRequest) -> str:
    """Create deterministic PML from typed, validated settings."""

    options = request.options
    negative = _pml_number(-options.level)
    positive = _pml_number(options.level)
    colors = ", ".join(options.ramp_colors)

    lines = [
        "reinitialize",
        f"load {_pml_string(request.dx_path.resolve())}, potential",
        f"load {_pml_string(request.pqr_path.resolve())}, molecule",
        "hide everything, all",
        "show surface, molecule",
        f"set surface_mode, {options.surface_mode}",
        f"set surface_solvent, {int(options.surface_solvent)}",
        f"set surface_ramp_above_mode, {int(options.surface_ramp_above_mode)}",
        (
            "ramp_new electrostatic, potential, "
            f"[{negative}, 0, {positive}], [{colors}]"
        ),
        "set surface_color, electrostatic, molecule",
        # Retain the ramp as the surface color source but hide its on-canvas
        # legend; the compositor draws one shared, consistently sized legend.
        "disable electrostatic",
    ]
    if options.ligand_representation is not None:
        lines.append(
            f"show {options.ligand_representation}, molecule and hetatm"
        )
    if options.view is None:
        lines.extend(("orient molecule", "zoom molecule"))
    else:
        values = ", ".join(_pml_number(value) for value in options.view)
        lines.append(f"set_view ({values})")
    lines.extend(
        (
            f"set opaque_background, {int(not options.transparent_background)}",
            f"bg_color {options.background}",
            # PyMOL's PML ``png`` command treats quotes as part of the filename
            # and appends another ``.png``. Use its Python API so paths with
            # spaces and quotes remain both safe and literal.
            "python",
            "from pymol import cmd",
            (
                f"cmd.png({_pml_string(request.png_path.resolve())}, "
                f"width={options.width}, height={options.height}, "
                f"dpi={options.dpi}, ray={int(options.ray_trace)})"
            ),
            "python end",
            "quit",
        )
    )
    return "\n".join(lines) + "\n"


def build_pymol_argv(
    request: PyMOLRenderRequest,
    executable: str | os.PathLike[str] = "pymol",
) -> tuple[str, ...]:
    """Build the quiet, command-line-only PyMOL invocation."""

    return (
        os.fspath(executable),
        "-cq",
        str(request.resolved_script_path.resolve()),
    )


class PyMOLBackend:
    """Checked, out-of-process PyMOL rendering."""

    def __init__(
        self,
        runner: CommandRunner,
        executable: str | os.PathLike[str] = "pymol",
    ) -> None:
        self.runner = runner
        self.executable = discover_executable(executable)

    def render(
        self,
        request: PyMOLRenderRequest,
        *,
        stage: str = "pymol",
        timeout: float | None = None,
    ) -> PyMOLRenderArtifacts:
        require_nonempty_file(request.pqr_path, "PQR input")
        require_nonempty_file(request.dx_path, "OpenDX potential input")

        work_dir = (
            request.work_dir.resolve()
            if request.work_dir is not None
            else request.png_path.resolve().parent
        )
        work_dir.mkdir(parents=True, exist_ok=True)
        request.png_path.resolve().parent.mkdir(parents=True, exist_ok=True)
        script_path = request.resolved_script_path.resolve()
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text(build_pymol_script(request), encoding="utf-8")

        result = self.runner.run(
            stage,
            build_pymol_argv(request, self.executable),
            cwd=work_dir,
            timeout=timeout,
        )
        png = require_png(request.png_path, "rendered PNG")
        require_nonempty_file(script_path, "PyMOL script")
        return PyMOLRenderArtifacts(png, script_path, result)

    def version(
        self, *, timeout: float = 10.0, cwd: str | Path | None = None
    ) -> VersionInfo:
        return self.runner.probe_version(self.executable, timeout=timeout, cwd=cwd)


PyMolBackend = PyMOLBackend
