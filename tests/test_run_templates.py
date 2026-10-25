from pathlib import Path

import pytest
from fake_structure import CIF

from phmap.errors import ConfigurationError
from phmap.gui.run_templates import RunTemplate
from phmap.json_state import read_json, write_json
from phmap.manifest import sha256_file


@pytest.fixture
def saved_run(tmp_path: Path) -> Path:
    root = tmp_path / "source-run"
    (root / "inputs").mkdir(parents=True)
    structure = root / "inputs" / "reference.cif"
    structure.write_bytes(CIF)
    write_json(root / "manifest.json", {
        "status": "completed", "configuration": {
            "proteins": [{"id": "reference", "label": "Reference", "ligand": None}],
            "ph_values": ["7.125", "5.0"], "ph_mode": "explicit", "view": None,
            "render": {"size": 128, "potential_level": 3, "background": "red",
                       "foreground": "blue", "ramp_colors": ["blue", "white", "red"],
                       "surface_mode": 4, "surface_solvent": True, "ray_trace": False},
            "composition": {"font_size": 32, "show_colorbar": False, "ph_label_precision": 1},
            "scientific": {"force_field": "AMBER"},
        },
        "inputs": [{"role": "protein:reference", "staged_path": str(structure),
                    "original_path": "/never/read/an/original/file.cif",
                    "sha256": sha256_file(structure)}],
    })
    return root


def test_template_restores_all_recorded_settings_and_original_ph_order(saved_run: Path) -> None:
    response = RunTemplate(saved_run).response(name="Reference experiment")
    settings = response["settings"]
    assert settings["name"] == "Reference experiment (copy)"
    assert settings["ph_mode"] == "explicit"
    assert settings["ph_values"] == "7.125, 5.0"
    assert settings["source_run_id"] == "source-run"
    assert settings["structures"] == [{"upload_index": None, "pdb_id": None,
                                       "saved_protein_id": "reference"}]
    assert settings["labels"] == ["Reference"]
    assert settings["size"] == 128 and settings["level"] == 3
    assert settings["background"] == "red" and settings["foreground"] == "blue"
    assert settings["ramp_colors"] == ("blue", "white", "red")
    assert settings["surface_mode"] == 4 and settings["surface_solvent"] is True
    assert settings["font_size"] == 32 and settings["show_colorbar"] is False
    assert settings["ph_label_precision"] == 1 and settings["force_field"] == "AMBER"
    assert response["has_view"] is False and response["has_ligand"] is False


def test_uniform_range_and_legacy_defaults_are_restored(saved_run: Path) -> None:
    manifest = read_json(saved_run / "manifest.json")
    manifest["configuration"]["ph_values"] = ["3.0", "3.5", "4.0"]
    manifest["configuration"].pop("composition")
    write_json(saved_run / "manifest.json", manifest)
    manifest["configuration"]["ph_mode"] = "range"
    write_json(saved_run / "manifest.json", manifest)
    settings = RunTemplate(saved_run).response(name="Range")["settings"]
    assert settings["ph_mode"] == "explicit"
    assert settings["ph_values"] == "3.0, 3.5, 4.0"
    assert settings["font_size"] == 22 and settings["show_colorbar"] is True


@pytest.mark.parametrize(("values", "start", "stop", "step"), [
    (["3", "5", "7"], "3", "8", "2"),
    (["3"], "3", "3", "0.5"),
])
def test_original_range_is_restored_without_guessing_from_values(
    saved_run: Path, values: list[str], start: str, stop: str, step: str,
) -> None:
    manifest = read_json(saved_run / "manifest.json")
    manifest["configuration"].update(
        ph_mode="range", ph_values=values, ph_range={"start": start, "stop": stop, "step": step},
    )
    write_json(saved_run / "manifest.json", manifest)
    settings = RunTemplate(saved_run).response(name="Range")["settings"]
    assert settings["ph_mode"] == "range"
    assert (settings["ph_start"], settings["ph_stop"], settings["ph_step"]) == (start, stop, step)


def test_mismatched_range_metadata_is_rejected(saved_run: Path) -> None:
    manifest = read_json(saved_run / "manifest.json")
    manifest["configuration"].update(
        ph_mode="range", ph_range={"start": "3", "stop": "8", "step": "2"},
    )
    write_json(saved_run / "manifest.json", manifest)
    with pytest.raises(ConfigurationError, match="does not match"):
        RunTemplate(saved_run).response(name="Wrong range")


@pytest.mark.parametrize("fault", ["missing", "checksum", "duplicate", "external", "symlink"])
def test_templates_do_not_trust_original_paths_or_unverified_saved_files(
    saved_run: Path, tmp_path: Path, fault: str,
) -> None:
    manifest = read_json(saved_run / "manifest.json")
    path = saved_run / "inputs/reference.cif"
    if fault == "missing":
        path.unlink()
    elif fault == "checksum":
        path.write_bytes(CIF + b"# changed\n")
    elif fault == "duplicate":
        manifest["inputs"].append(manifest["inputs"][0].copy())
    elif fault == "external":
        manifest["inputs"][0]["staged_path"] = str(tmp_path / "secret.cif")
    else:
        path.unlink()
        outside = tmp_path / "outside.cif"
        outside.write_bytes(CIF)
        try:
            path.symlink_to(outside)
        except OSError:
            pytest.skip("Creating symlinks is unavailable on this Windows runner")
    write_json(saved_run / "manifest.json", manifest)
    with pytest.raises(ConfigurationError):
        RunTemplate(saved_run).response(name="Unsafe")


def test_moved_run_uses_only_its_local_saved_inputs(saved_run: Path) -> None:
    manifest = read_json(saved_run / "manifest.json")
    manifest["inputs"][0]["staged_path"] = "/old/computer/source-run/inputs/reference.cif"
    write_json(saved_run / "manifest.json", manifest)
    saved = RunTemplate(saved_run).protein("reference")
    assert saved.path == saved_run / "inputs/reference.cif"


def test_changed_files_cannot_be_silently_copied_after_validation(
    saved_run: Path, tmp_path: Path,
) -> None:
    source = RunTemplate(saved_run).protein("reference")
    source.path.write_bytes(CIF + b"# changed after validation\n")
    with pytest.raises(ConfigurationError, match="changed while copying"):
        source.copy_to(tmp_path / "incoming/reference.cif")


def test_camera_and_shared_ligand_are_restored_without_original_files(saved_run: Path) -> None:
    ligand = saved_run / "inputs/reference.mol2"
    ligand.write_text("shared ligand\n", encoding="utf-8")
    manifest = read_json(saved_run / "manifest.json")
    manifest["configuration"]["view"] = list(range(18))
    manifest["configuration"]["proteins"][0]["ligand"] = "/missing/original/ligand.mol2"
    manifest["inputs"].append({"role": "ligand:reference", "staged_path": str(ligand),
                               "sha256": sha256_file(ligand)})
    write_json(saved_run / "manifest.json", manifest)
    template = RunTemplate(saved_run)
    response = template.response(name="With camera")
    assert response["settings"]["reuse_view"] is True
    assert response["settings"]["reuse_ligand"] is True
    assert template.view() is not None
    assert template.view().values == tuple(range(18))  # type: ignore[union-attr]
    assert template.ligand() is not None


def test_active_runs_and_gui_incompatible_settings_are_rejected(saved_run: Path) -> None:
    manifest = read_json(saved_run / "manifest.json")
    manifest["status"] = "running"
    write_json(saved_run / "manifest.json", manifest)
    with pytest.raises(ConfigurationError, match="finish"):
        RunTemplate(saved_run)
    manifest["status"] = "completed"
    manifest["configuration"]["render"]["size"] = 8192
    write_json(saved_run / "manifest.json", manifest)
    with pytest.raises(ConfigurationError, match="GUI"):
        RunTemplate(saved_run).response(name="Too large")
