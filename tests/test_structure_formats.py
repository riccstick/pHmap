from pathlib import Path

import gemmi
import pytest
from fake_structure import CIF

from phmap.errors import ConfigurationError
from phmap.structure_formats import prepare_mmcif


def test_mmcif_preparation_handles_minimal_metadata_and_preserves_original(tmp_path: Path) -> None:
    original = tmp_path / "variant.mmcif"
    original.write_bytes(CIF)
    prepared = prepare_mmcif(original, tmp_path / "backend.pdb")
    assert original.read_bytes() == CIF
    structure = gemmi.read_structure(str(prepared))
    assert len(structure) == 1
    atom = structure[0]["A"][0][0]
    assert atom.name == "N"
    assert (atom.pos.x, atom.pos.y, atom.pos.z) == (0, 0, 0)


def test_unicode_metadata_is_preserved_in_source_and_portable_in_backend(tmp_path: Path) -> None:
    original = tmp_path / "unicode.cif"
    content = CIF.replace(b"data_test\n", "data_test\n_struct.title 'Protein β'\n".encode())
    original.write_bytes(content)
    prepared = prepare_mmcif(original, tmp_path / "backend.pdb")
    assert original.read_bytes() == content
    assert "TITLE     PROTEIN ?" in prepared.read_text(encoding="ascii")
    assert gemmi.read_structure(str(prepared))[0]["A"][0][0].name == "N"


@pytest.mark.parametrize(("old", "new", "message"), [
    (b"1 ALA A N 1", b"1 ALA LONG N 1", "Chain ID"),
    (b"1 ALA A N 1", b"10000 ALA A N 1", "residue identifiers"),
    (b"0.0 0.0 0.0", b"10000.0 0.0 0.0", "coordinates"),
    (b"ATOM 1 N N", b"ATOM 100000 N N", "Atom identifiers"),
])
def test_unrepresentable_identifiers_are_rejected_without_silent_renaming(
    tmp_path: Path, old: bytes, new: bytes, message: str,
) -> None:
    original = tmp_path / "variant.cif"
    content = CIF.replace(old, new)
    original.write_bytes(content)
    with pytest.raises(ConfigurationError, match=message):
        prepare_mmcif(original, tmp_path / "backend.pdb")
    assert original.read_bytes() == content
    assert not (tmp_path / "backend.pdb").exists()


def test_multiple_coordinate_models_are_not_silently_merged(tmp_path: Path) -> None:
    original = tmp_path / "models.cif"
    extra = b"ATOM 2 N N . ALA A 1 1 ? 0.0 0.0 0.0 1.0 10.0 1 ALA A N 2\n"
    original.write_bytes(CIF.removesuffix(b"#\n") + extra + b"#\n")
    with pytest.raises(ConfigurationError, match="one coordinate model"):
        prepare_mmcif(original, tmp_path / "backend.pdb")


@pytest.mark.parametrize("content", [b"", b"not mmcif", b"data_empty\n#\n"])
def test_invalid_mmcif_is_reported_as_an_input_error(tmp_path: Path, content: bytes) -> None:
    original = tmp_path / "invalid.cif"
    original.write_bytes(content)
    with pytest.raises(ConfigurationError):
        prepare_mmcif(original, tmp_path / "backend.pdb")


@pytest.mark.parametrize(("old", "new"), [
    (b"1.0 10.0", b"nan 10.0"), (b"1.0 10.0", b"1.0 nan"),
    (b"1.0 10.0", b"1.0 10000.0"), (b"N N .", "N Nβ .".encode()),
])
def test_invalid_fixed_column_fields_are_rejected_before_writing(
    tmp_path: Path, old: bytes, new: bytes,
) -> None:
    original = tmp_path / "invalid.cif"
    original.write_bytes(CIF.replace(old, new))
    with pytest.raises(ConfigurationError):
        prepare_mmcif(original, tmp_path / "backend.pdb")
    assert not (tmp_path / "backend.pdb").exists()


def test_conversion_never_overwrites_an_existing_destination(tmp_path: Path) -> None:
    original = tmp_path / "variant.cif"
    original.write_bytes(CIF)
    destination = tmp_path / "backend.pdb"
    destination.write_text("Previous data", encoding="ascii")
    with pytest.raises(ConfigurationError, match="Cannot prepare"):
        prepare_mmcif(original, destination)
    assert destination.read_text(encoding="ascii") == "Previous data"


@pytest.mark.parametrize("change", ["identifier", "coordinate", "nonfinite"])
def test_round_trip_audit_catches_changed_atoms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    original = tmp_path / "variant.cif"
    original.write_bytes(CIF)
    reader = gemmi.read_pdb_string

    def changed_structure(text: str) -> gemmi.Structure:
        structure = reader(text)
        atom = structure[0][0][0][0]
        if change == "identifier":
            atom.name = "CA"
        elif change == "coordinate":
            atom.pos.x += 1
        else:
            atom.pos.x = float("nan")
        return structure

    monkeypatch.setattr(gemmi, "read_pdb_string", changed_structure)
    with pytest.raises(ConfigurationError, match="safely preserve"):
        prepare_mmcif(original, tmp_path / "backend.pdb")
    assert not (tmp_path / "backend.pdb").exists()
