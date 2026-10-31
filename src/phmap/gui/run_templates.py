"""Restore validated settings and hash-checked input copies from saved runs.

Never follow the original input paths or replay recorded executable arguments.
Only saved files confined to the selected run's inputs directory are eligible.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from phmap.errors import ConfigurationError
from phmap.inputs import parse_ph_range, parse_ph_values
from phmap.json_state import read_json, write_json
from phmap.manifest import sha256_file
from phmap.models import PyMOLView, structure_suffix, validate_protein_id

from .schemas import RunSettings

_MAX_INPUT = 64 * 1024 * 1024


@dataclass(frozen=True)
class SavedInput:
    path: Path
    digest: str
    source: dict[str, Any] | None = None

    def copy_to(self, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        size = 0
        digest = hashlib.sha256()
        with self.path.open("rb") as source, destination.open("xb") as output:
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                if size > _MAX_INPUT:
                    raise ConfigurationError("Saved input exceeds the 64 MiB limit")
                output.write(chunk)
                digest.update(chunk)
        if digest.hexdigest() != self.digest:
            raise ConfigurationError(f"Saved input changed while copying {self.path.name}")
        if self.source:
            write_json(destination.with_suffix(destination.suffix + ".source.json"), self.source)
        return destination


class RunTemplate:
    def __init__(
        self,
        root: Path,
        *,
        submission_path: Path | None = None,
        input_root: Path | None = None,
    ) -> None:
        self.root = root.resolve()
        metadata = self.root / "manifest.json" if submission_path is None else submission_path
        if metadata.is_symlink():
            raise ConfigurationError("Template metadata must not be a symbolic link")
        self.input_root = self.root / "inputs" if input_root is None else input_root
        self.manifest = read_json(metadata)
        if not self.manifest:
            raise ConfigurationError("This run has no readable saved setup")
        if self.manifest.get("status") not in {"completed", "failed", "cancelled", "interrupted"}:
            raise ConfigurationError("Wait for the source calculation to finish before copying it")
        digest_key = "manifest_sha256" if submission_path is None else "submission_sha256"
        self.based_on = {
            "run_id": self.root.name,
            "operation": "full-recalculation",
            digest_key: sha256_file(metadata),
        }
        self.configuration = self.manifest["configuration"]

    def saved_input(self, role: str) -> SavedInput:
        records = [item for item in self.manifest.get("inputs", []) if item.get("role") == role]
        if len(records) != 1:
            raise ConfigurationError(
                f"The run has no unique saved input for {role}; re-add its file",
            )
        record = records[0]
        recorded = Path(record["staged_path"])
        # Rebind moved run folders, but never read any original/external path.
        if recorded.parent.name != self.input_root.name:
            raise ConfigurationError(f"Saved input path is not in the run's inputs folder: {role}")
        path = (self.input_root / recorded.name).resolve()
        expected_parent = self.input_root
        if path.parent != expected_parent or not path.is_file():
            raise ConfigurationError(f"Saved input is missing or outside its run: {role}")
        if not 0 < path.stat().st_size <= _MAX_INPUT:
            raise ConfigurationError(f"Saved input is empty or exceeds 64 MiB: {role}")
        digest = record.get("sha256")
        if not isinstance(digest, str) or sha256_file(path) != digest:
            raise ConfigurationError(f"Saved input checksum does not match: {role}")
        source = record.get("source")
        if not isinstance(source, dict) or source.get("sha256") != digest:
            source = None
        return SavedInput(path, digest, source)

    def protein(self, protein_id: str) -> SavedInput:
        validate_protein_id(protein_id)
        if protein_id not in {item["id"] for item in self.configuration["proteins"]}:
            raise ConfigurationError("The saved protein does not belong to the selected source run")
        saved = self.saved_input(f"protein:{protein_id}")
        structure_suffix(saved.path)
        return saved

    def view(self) -> PyMOLView | None:
        value = self.configuration.get("view")
        return None if value is None else PyMOLView(value)

    def ligand(self) -> SavedInput | None:
        proteins = self.configuration["proteins"]
        if not any(item.get("ligand") is not None for item in proteins):
            return None
        if not all(item.get("ligand") is not None for item in proteins):
            raise ConfigurationError("The GUI cannot restore a partially shared ligand assignment")
        ligands = [self.saved_input(f"ligand:{item['id']}") for item in proteins]
        if len({item.digest for item in ligands}) != 1:
            raise ConfigurationError("The GUI supports one shared ligand, not per-protein ligands")
        if ligands[0].path.suffix.lower() != ".mol2":
            raise ConfigurationError("The saved shared ligand must be a MOL2 file")
        return ligands[0]

    def response(self, *, name: str, verify_inputs: bool = True) -> dict[str, Any]:
        config = self.configuration
        proteins = config["proteins"]
        if not 1 <= len(proteins) <= 32:
            raise ConfigurationError("This run exceeds the GUI's 32-protein limit")
        saved = [self.protein(item["id"]) for item in proteins] if verify_inputs else []
        view = self.view()
        has_ligand = (
            self.ligand() is not None
            if verify_inputs
            else any(item.get("ligand") is not None for item in proteins)
        )
        render = config.get("render", {})
        composition = config.get("composition", {})
        fields = {
            key: value
            for key, value in render.items()
            if key in RunSettings.model_fields and key not in {"foreground", "background"}
        }
        fields["level"] = render.get("potential_level", 5.0)
        fields.update(
            {key: value for key, value in composition.items() if key in RunSettings.model_fields}
        )
        fields["background"] = composition.get("background", render.get("background"))
        fields["foreground"] = composition.get("foreground", render.get("foreground", "black"))
        fields["force_field"] = str(
            config.get("scientific", {}).get("force_field", "PARSE"),
        ).upper()
        if "workers" in config.get("execution", {}):
            fields["workers"] = config["execution"]["workers"]
        numbers = parse_ph_values(config["ph_values"])
        values = [str(value) for value in numbers]
        fields.update(
            name=(name[:113] + " (copy)"),
            ph_mode="explicit",
            ph_values=", ".join(values),
            labels=[item["label"] for item in proteins],
            source_run_id=self.root.name,
            structures=[{"saved_protein_id": item["id"]} for item in proteins],
            reuse_view=view is not None,
            reuse_ligand=has_ligand,
        )
        saved_range = config.get("ph_range")
        if config.get("ph_mode") == "range" and saved_range is not None:
            triple = (saved_range["start"], saved_range["stop"], saved_range["step"])
            if parse_ph_range(triple).values() != numbers:
                raise ConfigurationError("Saved pH range does not match the calculation values")
            fields.update(
                ph_mode="range",
                ph_start=triple[0],
                ph_stop=triple[1],
                ph_step=triple[2],
            )
        try:
            settings = RunSettings.model_validate(fields)
            settings.options(self.root.parent)
            settings.selected_rows(0)
        except ValidationError as exc:
            raise ConfigurationError(
                "This run contains settings outside the local GUI's supported limits"
            ) from exc
        return {
            "settings": settings.model_dump(),
            "proteins": [
                {
                    "id": item["id"],
                    "label": item["label"],
                    "filename": saved[index].path.name if verify_inputs else item["id"],
                }
                for index, item in enumerate(proteins)
            ],
            "has_view": view is not None,
            "has_ligand": has_ligand,
        }
