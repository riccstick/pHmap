from decimal import Decimal
from pathlib import Path

import pytest

from phmap.models import (
    InputValidationError,
    PHInputMode,
    PHRange,
    ProteinInput,
    PyMOLView,
    RunInputs,
    coerce_ph,
    safe_protein_label,
    slugify_protein_id,
)


def _pdb(tmp_path: Path, name: str = "protein.pdb") -> Path:
    path = tmp_path / name
    path.write_text("ATOM\n", encoding="utf-8")
    return path


def test_coerce_ph_uses_decimal_without_float_drift() -> None:
    assert coerce_ph(0.1) == Decimal("0.1")
    assert coerce_ph(" 5.25 ") == Decimal("5.25")
    assert coerce_ph("7.0000000000001") == Decimal("7.0000000000001")


def test_coerce_ph_rejects_precision_the_backend_cannot_represent() -> None:
    with pytest.raises(InputValidationError, match="more precision"):
        coerce_ph("7.0000000000000001")


@pytest.mark.parametrize("value", ["NaN", "Infinity", -1, 14.1, True, "nope"])
def test_coerce_ph_rejects_invalid_values(value) -> None:
    with pytest.raises(InputValidationError):
        coerce_ph(value)


def test_ph_range_is_inclusive_without_binary_drift() -> None:
    ph_range = PHRange("4.0", "5.0", "0.25")

    assert ph_range.values() == (
        Decimal("4.0"),
        Decimal("4.25"),
        Decimal("4.50"),
        Decimal("4.75"),
        Decimal("5.00"),
    )


def test_ph_range_does_not_invent_a_non_aligned_stop() -> None:
    assert PHRange("4", "5", ".3").values() == (
        Decimal("4"),
        Decimal("4.3"),
        Decimal("4.6"),
        Decimal("4.9"),
    )


@pytest.mark.parametrize(
    "values", [("5", "4", "1"), ("4", "5", "0"), ("4", "15", "1")]
)
def test_ph_range_rejects_invalid_bounds(values) -> None:
    with pytest.raises(InputValidationError):
        PHRange(*values)


def test_pymol_view_requires_exactly_18_finite_numbers() -> None:
    view = PyMOLView(range(18))
    assert view.values == tuple(float(number) for number in range(18))

    with pytest.raises(InputValidationError, match="exactly 18"):
        PyMOLView(range(17))
    with pytest.raises(InputValidationError, match="finite"):
        PyMOLView([0.0] * 17 + [float("nan")])
    with pytest.raises(InputValidationError, match="sequence"):
        PyMOLView("000000000000000000")  # type: ignore[arg-type]


def test_protein_input_validates_files_and_derives_safe_names(tmp_path: Path) -> None:
    pdb = _pdb(tmp_path, "My protein.v2.pdb")
    ligand = tmp_path / "ligand.MOL2"
    ligand.write_text("@<TRIPOS>MOLECULE\n", encoding="utf-8")

    protein = ProteinInput(pdb, ligand=ligand)

    assert protein.path == pdb.resolve()
    assert protein.protein_id == "my-protein-v2"
    assert protein.label == "My protein.v2"
    assert protein.ligand == ligand.resolve()


def test_protein_input_rejects_missing_or_wrong_file_types(tmp_path: Path) -> None:
    with pytest.raises(InputValidationError, match="does not exist"):
        ProteinInput(tmp_path / "missing.pdb")
    wrong = tmp_path / "protein.txt"
    wrong.write_text("ATOM\n", encoding="utf-8")
    with pytest.raises(InputValidationError, match=".pdb extension"):
        ProteinInput(wrong)

    pdb = _pdb(tmp_path)
    wrong_ligand = tmp_path / "ligand.pdb"
    wrong_ligand.write_text("ATOM\n", encoding="utf-8")
    with pytest.raises(InputValidationError, match=".mol2 extension"):
        ProteinInput(pdb, ligand=wrong_ligand)


def test_safe_name_helpers_handle_paths_unicode_and_reserved_names() -> None:
    assert slugify_protein_id("../../ 42 β mutant") == "protein-42-mutant"
    assert safe_protein_label("alpha/beta") == "alpha-beta"
    assert slugify_protein_id("CON") == "protein-con"


def test_run_inputs_preserve_explicit_ph_order(tmp_path: Path) -> None:
    proteins = (ProteinInput(_pdb(tmp_path)),)
    run = RunInputs(proteins, ["7.0", "5.0"], ph_mode=PHInputMode.EXPLICIT)

    assert run.ph_values == (Decimal("7.0"), Decimal("5.0"))
    assert isinstance(run.proteins, tuple)


def test_run_inputs_require_unique_ids_labels_and_ph_values(tmp_path: Path) -> None:
    first = ProteinInput(_pdb(tmp_path, "one.pdb"), "first", "Same")
    second = ProteinInput(_pdb(tmp_path, "two.pdb"), "second", "same")
    with pytest.raises(InputValidationError, match="labels must be unique"):
        RunInputs([first, second], [5])
    with pytest.raises(InputValidationError, match="pH values must be unique"):
        RunInputs([first], ["5.0", "5.00"])
