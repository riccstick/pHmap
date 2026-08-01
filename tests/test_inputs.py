from decimal import Decimal
from pathlib import Path

import pytest

from phmap.inputs import (
    build_protein_inputs,
    load_pymol_view,
    parse_ph_range,
    parse_ph_selection,
    parse_ph_values,
    parse_pymol_view,
    validate_mol2_path,
    validate_pdb_path,
    validate_run_inputs,
)
from phmap.models import InputValidationError, PHInputMode, PHRange


def _structure(tmp_path: Path, directory: str, name: str) -> Path:
    parent = tmp_path / directory
    parent.mkdir()
    path = parent / name
    path.write_text("ATOM\n", encoding="utf-8")
    return path


def _view_text(values=range(18)) -> str:
    rows = []
    numbers = [f"{float(value):.6f}" for value in values]
    for start in range(0, 18, 3):
        rows.append("    " + ", ".join(numbers[start : start + 3]))
    return "set_view (\\\n" + ",\\\n".join(rows) + " )\n"


def test_parse_explicit_ph_values_from_strings_and_iterables() -> None:
    expected = (Decimal("4.0"), Decimal("4.5"), Decimal("5.0"))
    assert parse_ph_values("4.0, 4.5 5.0") == expected
    assert parse_ph_values(["4.0,4.5", Decimal("5.0")]) == expected


@pytest.mark.parametrize("values", ["", "4,,5", ["5", "5.0"], ["nan"]])
def test_parse_explicit_ph_values_rejects_malformed_input(values) -> None:
    with pytest.raises(InputValidationError):
        parse_ph_values(values)


def test_parse_ph_range_supports_text_iterable_and_arguments() -> None:
    assert parse_ph_range("4 5 .5") == PHRange("4", "5", ".5")
    assert parse_ph_range([4, 5, ".5"]) == PHRange("4", "5", ".5")
    assert parse_ph_range(4, 5, ".5") == PHRange("4", "5", ".5")


def test_parse_ph_selection_requires_exactly_one_source() -> None:
    values, mode = parse_ph_selection(ph_range="4 5 .5")
    assert values == (Decimal("4"), Decimal("4.5"), Decimal("5.0"))
    assert mode is PHInputMode.RANGE

    with pytest.raises(InputValidationError, match="exactly one"):
        parse_ph_selection()
    with pytest.raises(InputValidationError, match="exactly one"):
        parse_ph_selection(ph_values="4,5", ph_range="4 5 1")


def test_parse_real_get_view_output() -> None:
    path = Path(__file__).parents[1] / "example" / "set_view.txt"
    view = load_pymol_view(path)

    assert len(view.values) == 18
    assert view.values[0] == pytest.approx(-0.583356678)
    assert view.values[-1] == pytest.approx(-20.0)


@pytest.mark.parametrize(
    "text",
    [
        "set_view (" + ",".join("0" for _ in range(17)) + ")",
        "set_view (" + ",".join(["0"] * 17 + ["nan"]) + ")",
        "set_view (" + ",".join(["0"] * 17 + ["1); delete all"]) + ")",
        "set_view " + ",".join("0" for _ in range(18)),
    ],
)
def test_pymol_view_parser_rejects_wrong_length_nonfinite_and_commands(text) -> None:
    with pytest.raises(InputValidationError):
        parse_pymol_view(text)


def test_pymol_view_accepts_plain_numeric_sequence_and_file(tmp_path: Path) -> None:
    view_path = tmp_path / "camera.txt"
    view_path.write_text(_view_text(), encoding="utf-8")

    assert parse_pymol_view(tuple(range(18))).values[-1] == 17.0
    assert load_pymol_view(view_path).values[3] == 3.0


def test_path_validators_require_existing_expected_file_types(tmp_path: Path) -> None:
    pdb = tmp_path / "protein.PDB"
    pdb.write_text("ATOM\n", encoding="utf-8")
    mol2 = tmp_path / "ligand.mol2"
    mol2.write_text("@<TRIPOS>MOLECULE\n", encoding="utf-8")

    assert validate_pdb_path(pdb) == pdb.resolve()
    assert validate_mol2_path(mol2) == mol2.resolve()
    with pytest.raises(InputValidationError, match="does not exist"):
        validate_pdb_path(tmp_path / "missing.pdb")
    with pytest.raises(InputValidationError, match=".mol2 extension"):
        validate_mol2_path(pdb)


def test_build_proteins_generates_safe_unique_names_for_duplicate_stems(
    tmp_path: Path,
) -> None:
    first = _structure(tmp_path, "one", "Protein sample.pdb")
    second = _structure(tmp_path, "two", "Protein sample.pdb")

    proteins = build_protein_inputs([first, second])

    assert [protein.protein_id for protein in proteins] == [
        "protein-sample",
        "protein-sample-2",
    ]
    assert [protein.label for protein in proteins] == [
        "Protein sample",
        "Protein sample (2)",
    ]


def test_build_proteins_reserves_explicit_names_before_generating(tmp_path: Path) -> None:
    first = _structure(tmp_path, "one", "sample.pdb")
    second = _structure(tmp_path, "two", "other.pdb")

    proteins = build_protein_inputs(
        [first, second], protein_ids=[None, "sample"], labels=[None, "sample"]
    )

    assert proteins[0].protein_id == "sample-2"
    assert proteins[0].label == "sample (2)"
    assert proteins[1].protein_id == "sample"


def test_build_proteins_rejects_unsafe_or_duplicate_explicit_names(
    tmp_path: Path,
) -> None:
    first = _structure(tmp_path, "one", "one.pdb")
    second = _structure(tmp_path, "two", "two.pdb")

    with pytest.raises(InputValidationError, match="only ASCII"):
        build_protein_inputs([first], protein_ids=["../../one"])
    with pytest.raises(InputValidationError, match="labels must be unique"):
        build_protein_inputs([first, second], labels=["Same", "same"])


def test_validate_run_inputs_builds_complete_domain_model(tmp_path: Path) -> None:
    pdb = tmp_path / "protein.pdb"
    pdb.write_text("ATOM\n", encoding="utf-8")
    ligand = tmp_path / "ligand.mol2"
    ligand.write_text("@<TRIPOS>MOLECULE\n", encoding="utf-8")
    view_file = tmp_path / "view.txt"
    view_file.write_text(_view_text(), encoding="utf-8")

    run = validate_run_inputs(
        [pdb],
        ph_range="5 7 1",
        ligand=ligand,
        view_file=view_file,
    )

    assert run.ph_values == (Decimal("5"), Decimal("6"), Decimal("7"))
    assert run.ph_mode is PHInputMode.RANGE
    assert run.proteins[0].ligand == ligand.resolve()
    assert run.view is not None

