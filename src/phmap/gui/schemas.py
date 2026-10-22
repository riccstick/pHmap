"""Validated browser request models, separate from HTTP and filesystem handling."""

from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from phmap.backends.pymol import LigandRepresentation
from phmap.configuration import WorkflowOptions
from phmap.inputs import parse_ph_selection, validate_protein_names
from phmap.models import InputValidationError
from phmap.structures import normalize_pdb_id


class StructureSelection(BaseModel):
    """One ordered row, from either an upload or an official archive entry."""

    model_config = ConfigDict(extra="forbid")
    upload_index: int | None = Field(default=None, ge=0, le=31, strict=True)
    pdb_id: str | None = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def one_source(self) -> Self:
        if (self.upload_index is None) == (self.pdb_id is None):
            raise ValueError("Choose exactly one upload or PDB ID per protein row")
        if self.pdb_id is not None:
            self.pdb_id = normalize_pdb_id(self.pdb_id)
        return self


class FetchSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pdb_id: str = Field(max_length=20)
    refresh: bool = False


class RunSettings(BaseModel):
    """Browser inputs have the same validation boundary as command-line inputs."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    name: str = Field(default="", max_length=120)
    ph_mode: Literal["range", "explicit"] = "range"
    ph_start: str = "3"
    ph_stop: str = "8"
    ph_step: str = "1"
    ph_values: str = ""
    labels: list[str] = Field(default_factory=list, max_length=32)
    structures: list[StructureSelection] = Field(default_factory=list, max_length=32)
    size: int = Field(default=500, ge=64, le=4096)
    dpi: int = Field(default=300, ge=1, le=4800)
    level: float = Field(default=5.0, gt=0)
    background: str | None = "white"
    foreground: str = "black"
    ramp_colors: tuple[str, str, str] = ("red", "white", "blue")
    force_field: Literal["PARSE", "AMBER", "CHARMM"] = "PARSE"
    ray_trace: bool = True
    surface_mode: int = Field(default=0, ge=0, le=4, strict=True)
    surface_solvent: bool = Field(default=False, strict=True)
    surface_ramp_above_mode: bool = Field(default=True, strict=True)
    ligand_representation: LigandRepresentation | None = "sticks"

    def options(self, runs_dir: Path) -> WorkflowOptions:
        return WorkflowOptions(
            runs_dir=runs_dir, size=self.size, dpi=self.dpi, potential_level=self.level,
            ramp_colors=self.ramp_colors, background=self.background,
            foreground=self.foreground, force_field=self.force_field, ray_trace=self.ray_trace,
            surface_mode=self.surface_mode, surface_solvent=self.surface_solvent,
            surface_ramp_above_mode=self.surface_ramp_above_mode,
            ligand_representation=self.ligand_representation,
        )

    def selected_rows(self, upload_count: int) -> list[StructureSelection]:
        if upload_count > 32:
            raise InputValidationError("Choose at most 32 protein structure files")
        rows = self.structures or [
            StructureSelection(upload_index=index) for index in range(upload_count)
        ]
        if not 1 <= len(rows) <= 32:
            raise InputValidationError("Add between 1 and 32 protein structures or PDB IDs")
        indexes = [row.upload_index for row in rows if row.upload_index is not None]
        if sorted(indexes) != list(range(upload_count)):
            raise InputValidationError("Each uploaded structure must appear exactly once")
        validate_protein_names(len(rows), labels=self.labels or None)
        values, _ = parse_ph_selection(
            ph_values=self.ph_values if self.ph_mode == "explicit" else None,
            ph_range=(self.ph_start, self.ph_stop, self.ph_step)
            if self.ph_mode == "range" else None,
        )
        if len(values) * len(rows) > 512:
            raise InputValidationError("Use at most 512 calculation cells per GUI run")
        return rows
