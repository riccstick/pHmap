from pathlib import Path

from phmap.cli import main


def test_validate_command_normalizes_inputs(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    pdb = tmp_path / "mutant.two.pdb"
    pdb.write_text("ATOM\n", encoding="utf-8")

    status = main(["validate", str(pdb), "--ph", "5.0", "--ph", "7.0"])

    assert status == 0
    output = capsys.readouterr().out
    assert "Inputs are valid" in output
    assert "mutant-two" in output
    assert "5.0, 7.0" in output


def test_validate_command_returns_clean_error(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    missing = tmp_path / "missing.pdb"

    status = main(["validate", str(missing), "--ph", "7"])

    assert status == 2
    assert "does not exist" in capsys.readouterr().err
