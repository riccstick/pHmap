"""Validated browser request models, separate from HTTP and filesystem handling."""

from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from phmap.backends.pymol import LigandRepresentation
from phmap.compose import MAX_FONT_SIZE, MAX_SPACING, format_ph_labels
from phmap.configuration import WorkflowOptions
from phmap.inputs import parse_ph_selection, validate_protein_names
from phmap.models import InputValidationError, validate_protein_id
from phmap.parallel import MAX_WORKERS, recommended_workers
from phmap.structures import normalize_pdb_id


class StructureSelection(BaseModel):
    """One ordered row, from either an upload or an official archive entry."""

    model_config = ConfigDict(extra="forbid")
    upload_index: int | None = Field(default=None, ge=0, le=31, strict=True)
    pdb_id: str | None = Field(default=None, max_length=20)
    saved_protein_id: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def one_source(self) -> Self:
        if (
            sum(
                value is not None
                for value in (
                    self.upload_index,
                    self.pdb_id,
                    self.saved_protein_id,
                )
            )
            != 1
        ):
            raise ValueError("Choose exactly one upload, PDB ID, or saved input per protein row")
        if self.pdb_id is not None:
            self.pdb_id = normalize_pdb_id(self.pdb_id)
        if self.saved_protein_id is not None:
            validate_protein_id(self.saved_protein_id)
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
    font_size: int = Field(default=22, ge=1, le=MAX_FONT_SIZE, strict=True)
    outer_margin: int = Field(default=24, ge=0, le=MAX_SPACING, strict=True)
    column_gap: int = Field(default=16, ge=0, le=MAX_SPACING, strict=True)
    row_gap: int = Field(default=16, ge=0, le=MAX_SPACING, strict=True)
    label_gap: int = Field(default=16, ge=0, le=MAX_SPACING, strict=True)
    show_colorbar: bool = Field(default=True, strict=True)
    ph_label_precision: int | None = Field(default=None, ge=0, le=6, strict=True)
    source_run_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$",
    )
    reuse_view: bool = Field(default=False, strict=True)
    reuse_ligand: bool = Field(default=False, strict=True)
    operation: Literal["full", "compose", "render"] = "full"
    source_manifest_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    update_in_place: bool = Field(default=False, strict=True)
    workers: int = Field(default_factory=recommended_workers, ge=1, le=MAX_WORKERS, strict=True)

    @model_validator(mode="after")
    def saved_inputs_have_a_source(self) -> Self:
        if self.source_run_id is None and (
            self.operation != "full"
            or self.update_in_place
            or self.reuse_view
            or self.reuse_ligand
            or any(row.saved_protein_id is not None for row in self.structures)
        ):
            raise ValueError("Reusing saved inputs requires a source run")
        return self

    def options(self, runs_dir: Path) -> WorkflowOptions:
        return WorkflowOptions(
            runs_dir=runs_dir,
            size=self.size,
            dpi=self.dpi,
            potential_level=self.level,
            ramp_colors=self.ramp_colors,
            background=self.background,
            foreground=self.foreground,
            force_field=self.force_field,
            ray_trace=self.ray_trace,
            surface_mode=self.surface_mode,
            surface_solvent=self.surface_solvent,
            surface_ramp_above_mode=self.surface_ramp_above_mode,
            ligand_representation=self.ligand_representation,
            font_size=self.font_size,
            outer_margin=self.outer_margin,
            column_gap=self.column_gap,
            row_gap=self.row_gap,
            label_gap=self.label_gap,
            show_colorbar=self.show_colorbar,
            ph_label_precision=self.ph_label_precision,
            workers=self.workers,
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
            if self.ph_mode == "range"
            else None,
        )
        if len(values) * len(rows) > 512:
            raise InputValidationError("Use at most 512 calculation cells per GUI run")
        format_ph_labels(values, self.ph_label_precision)
        return rows
