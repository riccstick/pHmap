"""Shared, eagerly validated settings for CLI and browser calculations."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from phmap.backends import BackendValidationError, PyMOLRenderOptions
from phmap.backends.pdb2pqr import validate_force_field
from phmap.backends.pymol import LigandRepresentation
from phmap.compose import CompositionSettings, format_ph_labels
from phmap.errors import ConfigurationError
from phmap.models import RunInputs, structure_format


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
    font_size: int = 22
    outer_margin: int = 24
    column_gap: int = 16
    row_gap: int = 16
    label_gap: int = 16
    show_colorbar: bool = True
    ph_label_precision: int | None = None

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
            font_size=self.font_size, outer_margin=self.outer_margin,
            column_gap=self.column_gap, row_gap=self.row_gap, label_gap=self.label_gap,
            show_colorbar=self.show_colorbar, ph_label_precision=self.ph_label_precision,
            dpi=self.dpi,
        )

    def composition_configuration(self, ph_values: Sequence[Decimal]) -> dict[str, object]:
        return {
            "font": "pillow-default", **asdict(self.composition_settings()),
            "ph_labels": list(format_ph_labels(ph_values, self.ph_label_precision)),
        }


def build_run_configuration(inputs: RunInputs, options: WorkflowOptions) -> dict[str, Any]:
    """Use identical validated configuration for queued submissions and final runs."""

    return {
        "proteins": [{
            "id": protein.protein_id, "label": protein.label, "path": str(protein.path),
            "format": structure_format(protein.path),
            "ligand": None if protein.ligand is None else str(protein.ligand),
        } for protein in inputs.proteins],
        "ph_values": [str(ph) for ph in inputs.ph_values],
        "ph_mode": inputs.ph_mode.value,
        "ph_range": None if inputs.ph_range is None else {
            "start": str(inputs.ph_range.start), "stop": str(inputs.ph_range.stop),
            "step": str(inputs.ph_range.step),
        },
        "view": None if inputs.view is None else list(inputs.view.values),
        "render": options.render_configuration(),
        "composition": options.composition_configuration(inputs.ph_values),
        "scientific": {
            "force_field": options.force_field, "titration_state_method": "propka",
            "drop_water": True,
        },
    }
