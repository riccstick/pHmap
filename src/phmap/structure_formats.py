"""Preserve PDBx/mmCIF originals and prepare checked input for the pinned backend."""

from __future__ import annotations

import math
from importlib.metadata import version
from pathlib import Path

import gemmi

from phmap.errors import ConfigurationError


def read_mmcif(source: Path) -> gemmi.Structure:
    """Parse a complete coordinate document, without imposing backend limits."""

    try:
        document = gemmi.cif.read_file(str(source))
        if len(document) != 1:
            raise ConfigurationError("Choose an mmCIF file with exactly one structure data block")
        structure = gemmi.make_structure_from_block(document[0])
    except (RuntimeError, ValueError, UnicodeError) as exc:
        raise ConfigurationError(f"Cannot read PDBx/mmCIF structure {source.name}: {exc}") from exc
    if not any(len(residue) for model in structure for chain in model for residue in chain):
        raise ConfigurationError("The mmCIF document contains no atomic coordinates")
    return structure


def _ascii_identifier(value: str, maximum: int) -> bool:
    return bool(value) and len(value) <= maximum and value.isascii() and value.isprintable()


def validate_mmcif(source: Path) -> gemmi.Structure:
    """Check the fixed-column limits of the pinned PDB2PQR backend."""

    structure = read_mmcif(source)
    if len(structure) != 1:
        raise ConfigurationError(
            "The current backend requires exactly one coordinate model; export a single model first"
        )
    atom_count = 0
    for chain in structure[0]:
        if not _ascii_identifier(chain.name, 1):
            raise ConfigurationError(
                f"Chain ID {chain.name!r} cannot be represented by the current PDB2PQR backend. "
                "Prepare explicit one-character chain IDs before running pHmap."
            )
        for residue in chain:
            number = residue.seqid.num
            if (
                not _ascii_identifier(residue.name, 3)
                or number is None or not -999 <= number <= 9999
            ):
                raise ConfigurationError(
                    "This mmCIF has residue identifiers outside the current backend's limits; "
                    "prepare explicit shorter residue names/numbers first"
                )
            for atom in residue:
                atom_count += 1
                if atom_count > 99999:
                    raise ConfigurationError("The current backend supports at most 99999 atoms")
                if not _ascii_identifier(atom.name, 4) or not 0 <= atom.serial <= 99999:
                    raise ConfigurationError("Atom identifiers exceed the current backend's limits")
                coordinates = (atom.pos.x, atom.pos.y, atom.pos.z)
                if any(
                    not math.isfinite(value) or not -999.999 <= value <= 9999.999
                    for value in coordinates
                ):
                    raise ConfigurationError(
                        "Atomic coordinates exceed the current backend's limits"
                    )
                if abs(atom.charge) > 9:
                    raise ConfigurationError("Formal charges exceed the current backend's limits")
                if any(
                    not math.isfinite(value) or len(f"{value:6.2f}") > 6
                    for value in (atom.occ, atom.b_iso)
                ):
                    raise ConfigurationError(
                        "Occupancy or temperature-factor values exceed the backend's limits"
                    )
    return structure


def _check_pdb_round_trip(original: gemmi.Structure, text: str) -> None:
    """Do not accept a bridge that drops, renames, or rearranges atoms."""

    converted = gemmi.read_pdb_string(text)
    before = list(original[0].all())
    if len(converted) != 1:
        raise ConfigurationError("Backend conversion changed the number of coordinate models")
    after = list(converted[0].all())
    if len(before) != len(after):
        raise ConfigurationError("Backend conversion changed the number of atoms")
    for first, second in zip(before, after, strict=True):
        first_id = (
            first.chain.name, str(first.residue.seqid), first.residue.name,
            first.atom.name, first.atom.altloc, first.atom.serial,
            first.atom.element.name, first.atom.charge,
        )
        second_id = (
            second.chain.name, str(second.residue.seqid), second.residue.name,
            second.atom.name, second.atom.altloc, second.atom.serial,
            second.atom.element.name, second.atom.charge,
        )
        distance = first.atom.pos.dist(second.atom.pos)
        if first_id != second_id or not math.isfinite(distance) or distance > 0.001:
            raise ConfigurationError(
                "Backend conversion cannot safely preserve atom identifiers/coordinates"
            )


def prepare_mmcif(source: Path, destination: Path) -> Path:
    """Create a backend-only PDB bridge; leave the original mmCIF unchanged."""

    if source.resolve() == destination.resolve():
        raise ConfigurationError("The backend structure must not overwrite the original input")
    structure = validate_mmcif(source)
    options = gemmi.PdbWriteOptions(preserve_serial=True, conect_records=True)
    try:
        # Preserve full Unicode metadata in the source. Backend-only headers must
        # be portable to tools using the platform's default text encoding.
        text = structure.make_pdb_string(options).encode("ascii", errors="replace").decode("ascii")
        _check_pdb_round_trip(structure, text)
        with destination.open("x", encoding="ascii", newline="\n") as handle:
            handle.write(text)
    except (RuntimeError, ValueError, OSError) as exc:
        raise ConfigurationError(
            f"Cannot prepare backend structure {destination.name}: {exc}"
        ) from exc
    return destination


def mmcif_preparation_info() -> dict[str, str]:
    return {
        "software": "Gemmi", "version": version("gemmi"),
        "operation": "mmcif-to-backend-pdb", "coordinate_precision_angstrom": "0.001",
        "reason": "PDB2PQR 3.7.1 mmCIF metadata reader compatibility",
    }
