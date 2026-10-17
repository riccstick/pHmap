from pathlib import Path

import pytest

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


@pytest.mark.parametrize("arguments", [
    ["--ramp-colors", "red", "white", "notacolor"],
    ["--foreground", "notacolor"], ["--background", "white; quit"],
    ["--force-field", "PARSE; quit"],
])
def test_run_rejects_invalid_settings_before_starting_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, arguments: list[str],
) -> None:  # type: ignore[no-untyped-def]
    pdb = tmp_path / "input.pdb"
    pdb.write_text("ATOM\n", encoding="utf-8")
    output = tmp_path / "run"
    monkeypatch.setattr(
        "phmap.cli.LocalWorkflow.run", lambda self: pytest.fail("Workflow must not start"),
    )
    assert main(["run", str(pdb), "--ph", "5", "--output-dir", str(output), *arguments]) == 2
    assert "phmap: error:" in capsys.readouterr().err
    assert not output.exists()
