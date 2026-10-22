import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from fake_structure import CIF

from phmap.cli import main
from phmap.errors import StructureFetchError


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


def test_cli_can_combine_a_pdb_id_and_local_mmcif_variant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:  # type: ignore[no-untyped-def]
    reference = tmp_path / "3sqr.cif"
    variant = tmp_path / "variant.mmcif"
    reference.write_bytes(CIF)
    variant.write_bytes(CIF)
    fetch = Mock(return_value=reference)
    monkeypatch.setattr("phmap.cli.fetch_structure", fetch)
    assert main([
        "validate", "--pdb-id", "3sqr", str(variant), "--ph-range", "3", "8", "1",
        "--label", "WT", "--label", "Variant", "--json",
    ]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert [p["label"] for p in summary["proteins"]] == ["WT", "Variant"]
    assert [p["format"] for p in summary["proteins"]] == ["mmcif", "mmcif"]
    fetch.assert_called_once_with("3SQR", Path("runs/.structures"), refresh=False)


def test_cli_supports_id_only_and_reports_download_errors_cleanly(
    monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:  # type: ignore[no-untyped-def]
    fetch = Mock(side_effect=StructureFetchError("Check internet access"))
    monkeypatch.setattr("phmap.cli.fetch_structure", fetch)
    assert main(["validate", "--pdb-id", "3sqr", "--ph", "7"]) == 2
    assert "Check internet access" in capsys.readouterr().err
    fetch.assert_called_once()


def test_cli_rejects_all_invalid_ids_before_any_download(
    monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:  # type: ignore[no-untyped-def]
    fetch = Mock()
    monkeypatch.setattr("phmap.cli.fetch_structure", fetch)
    assert main(["validate", "--pdb-id", "3sqr", "--pdb-id", "../evil", "--ph", "7"]) == 2
    assert "not a URL" in capsys.readouterr().err
    fetch.assert_not_called()
    assert main(["validate", "--ph", "7"]) == 2
    assert "Provide local structure files" in capsys.readouterr().err


@pytest.mark.parametrize("arguments", [
    ["--surface-mode", "-1"], ["--surface-mode", "5"], ["--surface-mode", "2.0"],
    ["--ligand-representation", "cartoon"], ["--ligand-representation", "sticks; quit"],
])
def test_cli_rejects_invalid_advanced_settings_before_fetching(
    monkeypatch: pytest.MonkeyPatch, arguments: list[str],
) -> None:
    fetch = Mock(side_effect=AssertionError("Must not fetch"))
    monkeypatch.setattr("phmap.cli.fetch_structure", fetch)
    with pytest.raises(SystemExit) as exc:
        main(["run", "--pdb-id", "3sqr", "--ph", "7", *arguments])
    assert exc.value.code == 2
    fetch.assert_not_called()


@pytest.mark.parametrize("invalid", ["label", "count", "view", "ligand", "path", "cif"])
def test_cli_preflights_local_inputs_and_metadata_before_downloading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid: str,
) -> None:
    fetch = Mock(side_effect=AssertionError("Must not download for invalid local inputs"))
    monkeypatch.setattr("phmap.cli.fetch_structure", fetch)
    arguments = ["validate", "--pdb-id", "3sqr", "--ph", "7"]
    if invalid == "label":
        arguments += ["--label", "Invalid/name"]
    elif invalid == "count":
        arguments += ["--label", "One", "--label", "Two"]
    elif invalid in {"view", "ligand"}:
        arguments += [f"--{invalid}", str(tmp_path / "missing.txt")]
    else:
        variant = tmp_path / "variant.cif"
        if invalid == "cif":
            variant.write_text("not mmcif", encoding="ascii")
        # Positional inputs precede options so argparse's '*' grouping is unambiguous.
        arguments.insert(1, str(variant))
    assert main(arguments) == 2
    fetch.assert_not_called()
