"""Shared, eagerly validated settings for CLI and browser calculations."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from phmap.backends import BackendValidationError, PyMOLRenderOptions
from phmap.backends.pdb2pqr import validate_force_field
from phmap.backends.pymol import LigandRepresentation
from phmap.compose import CompositionSettings
from phmap.errors import ConfigurationError


@dataclass(frozen=True, slots=True)
class WorkflowOptions:
    """Validate all presentation settings before creating a run or starting tools."""

    output_dir: Path | None = None
    runs_dir: Path = Path("runs")
    size: int = 500
    dpi: int = 300
    potential_level: float = 5.0
    ramp_colors: tuple[str, str, str] = ("red", "white", "blue")
    background: str | None = None
    foreground: str = "black"
    force_field: str = "PARSE"
    timeout: float = 1800.0
    ray_trace: bool = True
    pdb2pqr_executable: str | Path = "pdb2pqr30"
    apbs_executable: str | Path = "apbs"
    pymol_executable: str | Path = "pymol"
    surface_mode: int = 0
    surface_solvent: bool = False
    surface_ramp_above_mode: bool = True
    ligand_representation: LigandRepresentation | None = "sticks"

    def __post_init__(self) -> None:
        if (
            isinstance(self.size, bool)
            or not isinstance(self.size, int)
            or not 64 <= self.size <= 16_384
        ):
            raise ConfigurationError("Image size must be between 64 and 16384 pixels")
        if (
            isinstance(self.dpi, bool)
            or not isinstance(self.dpi, int)
            or not 1 <= self.dpi <= 4_800
        ):
            raise ConfigurationError("DPI must be between 1 and 4800")
        if (
            isinstance(self.potential_level, bool)
            or not math.isfinite(self.potential_level)
            or self.potential_level <= 0
        ):
            raise ConfigurationError("Potential level must be finite and positive")
        if (
            isinstance(self.timeout, bool)
            or not math.isfinite(self.timeout)
            or self.timeout <= 0
        ):
            raise ConfigurationError("Backend timeout must be finite and positive")
        if len(self.ramp_colors) != 3:
            raise ConfigurationError("The potential ramp requires exactly three colors")
        try:
            validate_force_field(self.force_field)
            self.render_options()
            self.composition_settings()
        except BackendValidationError as exc:
            raise ConfigurationError(str(exc)) from exc

    def render_options(self, *, view: tuple[float, ...] | None = None) -> PyMOLRenderOptions:
        """Build the same validated render settings for preflight and execution."""

        return PyMOLRenderOptions(
            width=self.size, height=self.size, dpi=self.dpi, level=self.potential_level,
            ramp_colors=self.ramp_colors,
            background="white" if self.background is None else self.background,
            transparent_background=self.background is None, view=view, ray_trace=self.ray_trace,
            surface_mode=self.surface_mode, surface_solvent=self.surface_solvent,
            surface_ramp_above_mode=self.surface_ramp_above_mode,
            ligand_representation=self.ligand_representation,
        )

    def render_configuration(self) -> dict[str, object]:
        """Persist the effective settings in both queued jobs and run manifests."""

        return {
            "size": self.size, "dpi": self.dpi, "potential_level": self.potential_level,
            "ramp_colors": list(self.ramp_colors), "background": self.background,
            "foreground": self.foreground, "ray_trace": self.ray_trace,
            "surface_mode": self.surface_mode, "surface_solvent": self.surface_solvent,
            "surface_ramp_above_mode": self.surface_ramp_above_mode,
            "ligand_representation": self.ligand_representation,
        }

    def composition_settings(self) -> CompositionSettings:
        return CompositionSettings(
            background=self.background, foreground=self.foreground,
            potential_level=self.potential_level, ramp_colors=self.ramp_colors,
        )
