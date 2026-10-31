from __future__ import annotations

import json
import shutil
import threading
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest
from fake_tools import fake_tools
from PIL import Image

from phmap.backends import PyMOLBackend
from phmap.cli import main
from phmap.errors import ConfigurationError
from phmap.inputs import validate_run_inputs
from phmap.manifest import sha256_file
from phmap.models import PyMOLView
from phmap.run_updates import RunUpdate
from phmap.saved_runs import SavedRun
from phmap.workflow import LocalWorkflow, WorkflowOptions


@pytest.fixture
def scientific_tools(tmp_path: Path) -> tuple[Path, Path, Path]:
    pdb2pqr, apbs, pymol = fake_tools(tmp_path)
    # Exercise the Windows filename on every platform so hard-coded,
    # extensionless paths also fail in local macOS/Linux regression runs.
    if pymol.suffix != ".exe":
        pymol = pymol.rename(pymol.with_suffix(".exe"))
    return pdb2pqr, apbs, pymol


@pytest.fixture
def pymol_executable(scientific_tools: tuple[Path, Path, Path]) -> Path:
    return scientific_tools[2]


@pytest.fixture
def saved(tmp_path: Path, scientific_tools: tuple[Path, Path, Path]) -> SavedRun:
    pdb = tmp_path / "protein.pdb"
    pdb.write_text("ATOM\n")
    pdb2pqr, apbs, pymol = scientific_tools
    result = LocalWorkflow(
        validate_run_inputs(pdb, ph_values=["5.0", "7.0"], labels=["Original"]),
        WorkflowOptions(
            output_dir=tmp_path / "source",
            size=64,
            ray_trace=False,
            pdb2pqr_executable=pdb2pqr,
            apbs_executable=apbs,
            pymol_executable=pymol,
        ),
    ).run()
    return SavedRun(result.workspace.root)


def test_compose_uses_only_tiles_and_keeps_source_immutable(
    saved: SavedRun,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No original structures, PQR, DX, PML, or scientific executable is needed.
    for directory in (saved.root / "inputs", saved.root / "work"):
        shutil.rmtree(directory)
    before = {
        p.relative_to(saved.root): p.read_bytes() for p in saved.root.rglob("*") if p.is_file()
    }
    discover = Mock(side_effect=AssertionError("Compose must never discover or probe tools"))
    monkeypatch.setattr("phmap.saved_runs.discover_executable", discover)
    options = replace(
        saved.options,
        output_dir=tmp_path / "edited",
        font_size=36,
        outer_margin=0,
        row_gap=50,
        show_colorbar=False,
        dpi=450,
        background="white",
        ph_label_precision=1,
    )
    workflow = saved.prepare("compose", options, labels=["New label"], ph_values=["7", "5"])
    assert workflow.plan()["run"] == ["composition"]
    assert not options.output_dir.exists()
    result = workflow.run()
    discover.assert_not_called()
    manifest = json.loads(result.manifest.read_bytes())
    assert manifest["based_on"] == {
        "operation": "compose",
        "run_id": "source",
        "manifest_sha256": saved.digest,
    }
    assert manifest["configuration"]["ph_values"] == ["7.0", "5.0"]
    assert manifest["configuration"]["proteins"][0]["label"] == "New label"
    assert manifest["configuration"]["composition"]["ph_labels"] == ["7.0", "5.0"]
    assert all(t["stages"][0]["reused"] for t in manifest["tasks"])
    assert all(len(t["stages"]) == 1 for t in manifest["tasks"])
    for task in manifest["tasks"]:
        output = task["stages"][0]["outputs"][0]
        assert sha256_file(Path(output["path"])) == output["sha256"]
        assert Path(output["path"]).is_relative_to(result.workspace.root)
    with Image.open(result.figure) as figure:
        assert figure.info["dpi"] == pytest.approx((450, 450), abs=0.02)
    assert before == {
        p.relative_to(saved.root): p.read_bytes() for p in saved.root.rglob("*") if p.is_file()
    }
    # The actual tile background stays transparent even when the figure is opaque.
    again = SavedRun(result.workspace.root)
    assert again.options.background == "white"
    assert again.tile_options.background is None
    again.prepare(
        "compose", replace(again.options, output_dir=tmp_path / "again", background="black")
    ).run()


def test_render_reuses_science_without_raw_inputs_and_regenerates_scripts(
    saved: SavedRun,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pymol_executable: Path,
) -> None:
    shutil.rmtree(saved.root / "inputs")
    shutil.rmtree(saved.root / "renders")
    # Never replay saved scripts or commands, even if these are changed.
    for path in saved.root.rglob("render.pml"):
        path.write_text("quit; do not execute this file")
    before = sha256_file(saved.root / "manifest.json")
    options = replace(
        saved.options,
        output_dir=tmp_path / "rerendered",
        size=96,
        background="black",
        ramp_colors=("blue", "white", "red"),
        potential_level=3,
        surface_mode=4,
        surface_solvent=True,
        pymol_executable=pymol_executable,
        pdb2pqr_executable="missing-pdb2pqr",
        apbs_executable="missing-apbs",
    )
    from phmap.saved_runs import discover_executable

    discover = Mock(wraps=discover_executable)
    monkeypatch.setattr("phmap.saved_runs.discover_executable", discover)
    view = PyMOLView(list(range(18)))
    result = saved.prepare("render", options, ph_values=["7"], view=view).run()
    discover.assert_called_once_with(options.pymol_executable)
    data = json.loads(result.manifest.read_bytes())
    assert [t["ph"] for t in data["tasks"]] == ["7.0"]
    stages = data["tasks"][0]["stages"]
    assert [s["name"] for s in stages] == ["pdb2pqr", "apbs", "pymol"]
    assert [s.get("reused", False) for s in stages] == [True, True, False]
    assert stages[0]["metrics"]["pqr"]["total_charge"] == -0.3
    assert data["tools"]["pdb2pqr"]["reused"] and data["tools"]["apbs"]["reused"]
    assert "reused" not in data["tools"]["pymol"]
    assert data["configuration"]["view"] == list(view.values)
    assert data["plan"]["run"] == ["PyMOL", "composition"]
    tile = result.workspace.renders / "protein" / "ph-7.0.png"
    with Image.open(tile) as image:
        assert image.size == (96, 96)
    assert sha256_file(saved.root / "manifest.json") == before
    # A derivative remains a standalone usable PQR/DX source.
    derived = SavedRun(result.workspace.root)
    derived.prepare("render", replace(options, output_dir=tmp_path / "render-again")).run()


@pytest.mark.parametrize(
    "changes",
    [
        {"size": 96},
        {"potential_level": 2},
        {"ramp_colors": ("blue", "white", "red")},
        {"surface_mode": 1},
        {"surface_solvent": True},
        {"surface_ramp_above_mode": False},
        {"ligand_representation": None},
        {"ray_trace": True},
    ],
)
def test_compose_rejects_render_changes_before_creating_output(
    saved: SavedRun,
    tmp_path: Path,
    changes: dict,
) -> None:
    output = tmp_path / "no-output"
    with pytest.raises(ConfigurationError, match="Re-render"):
        saved.prepare("compose", replace(saved.options, output_dir=output, **changes))
    assert not output.exists()


def test_camera_and_force_field_require_correct_operation(saved: SavedRun) -> None:
    with pytest.raises(ConfigurationError, match="camera"):
        saved.prepare("compose", view=PyMOLView(list(range(18))))
    for operation in ("compose", "render"):
        with pytest.raises(ConfigurationError, match="force field"):
            saved.prepare(operation, replace(saved.options, force_field="AMBER"))


@pytest.mark.parametrize(
    "operation,relative",
    [
        ("compose", "renders/protein/ph-5.0.png"),
        ("render", "work/protein/ph-5.0/model.pqr"),
        ("render", "work/protein/ph-5.0/model.pqr.dx"),
    ],
)
@pytest.mark.parametrize("damage", ["missing", "checksum", "symlink"])
def test_missing_corrupt_or_escaping_artifacts_are_rejected(
    saved: SavedRun,
    tmp_path: Path,
    operation: str,
    relative: str,
    damage: str,
) -> None:
    artifact = saved.root / relative
    artifact.unlink()
    if damage == "checksum":
        artifact.write_bytes(b"corrupt")
    elif damage == "symlink":
        outside = tmp_path / "outside"
        outside.write_bytes(b"external bytes")
        try:
            artifact.symlink_to(outside)
        except OSError:
            pytest.skip("Symbolic links are unavailable")
    with pytest.raises(ConfigurationError, match="artifact"):
        saved.prepare(operation, replace(saved.options, output_dir=tmp_path / "invalid"))
    assert not (tmp_path / "invalid").exists()


def test_copy_rechecks_hash_after_preflight(saved: SavedRun, tmp_path: Path) -> None:
    workflow = saved.prepare("compose", replace(saved.options, output_dir=tmp_path / "changed"))
    (saved.root / "renders/protein/ph-5.0.png").write_bytes(b"changed after plan")
    with pytest.raises(ConfigurationError, match="while copying"):
        workflow.run()
    data = json.loads((tmp_path / "changed/manifest.json").read_bytes())
    assert data["status"] == "failed" and data["tasks"][0]["status"] == "failed"
    assert not (tmp_path / "changed/output/pHmap.png").exists()


def test_plan_pins_manifest_and_supports_relocated_folders(saved: SavedRun, tmp_path: Path) -> None:
    moved = tmp_path / "relocated"
    shutil.move(saved.root, moved)
    restored = SavedRun(moved, expected_manifest=saved.digest)
    restored.prepare("compose")
    (moved / "manifest.json").write_text(json.dumps(saved.manifest, indent=3))
    with pytest.raises(ConfigurationError, match="manifest changed"):
        SavedRun(moved, expected_manifest=saved.digest)


@pytest.mark.parametrize(
    "selection",
    [
        {"protein_ids": []},
        {"protein_ids": ["missing"]},
        {"protein_ids": ["protein", "protein"]},
        {"ph_values": ["6"]},
        {"ph_values": ["5", "5.0"]},
        {"labels": ["Invalid/name"]},
    ],
)
def test_invalid_selections_fail_early(saved: SavedRun, selection: dict) -> None:
    with pytest.raises((ConfigurationError, ValueError)):
        saved.prepare("compose", **selection)


def test_selecting_completed_cells_of_failed_run(saved: SavedRun) -> None:
    data = saved.manifest
    data["status"] = "failed"
    data["tasks"][1]["status"] = "failed"
    data["tasks"][1]["stages"][-1]["status"] = "failed"
    (saved.root / "manifest.json").write_text(json.dumps(data))
    failed = SavedRun(saved.root)
    assert failed.prepare("compose", ph_values=["5"]).plan()["total"] == 1
    with pytest.raises(ConfigurationError, match="No completed pymol"):
        failed.prepare("compose")
    # Re-render can recover a cell whose scientific stages completed but PyMOL failed.
    assert failed.prepare("render").plan()["total"] == 2


def test_cli_inherits_settings_and_plan_is_read_only(
    saved: SavedRun, tmp_path: Path, capsys
) -> None:
    output = tmp_path / "cli-edited"
    assert main(["compose", str(saved.root), "--plan", "--output-dir", str(output)]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["render"]["size"] == 64 and plan["render"]["ray_trace"] is False
    assert plan["run"] == ["composition"] and not output.exists()
    assert (
        main(
            [
                "compose",
                str(saved.root),
                "--output-dir",
                str(output),
                "--font-size",
                "30",
                "--no-legend",
                "--ph",
                "7",
                "--label",
                "Changed",
            ]
        )
        == 0
    )
    assert (output / "output/pHmap.png").is_file()
    assert main(["compose", str(saved.root), "--size", "96", "--plan"]) == 2
    assert "Re-render" in capsys.readouterr().err


def test_cli_render_calls_only_pymol(
    saved: SavedRun, tmp_path: Path, pymol_executable: Path,
) -> None:
    output = tmp_path / "cli-render"
    assert (
        main(
            [
                "render",
                str(saved.root),
                "--output-dir",
                str(output),
                "--size",
                "80",
                "--pymol-executable",
                str(pymol_executable),
                "--pdb2pqr-executable",
                "not-installed",
                "--apbs-executable",
                "not-installed",
            ]
        )
        == 0
    )
    data = json.loads((output / "manifest.json").read_bytes())
    assert data["status"] == "completed"
    assert data["configuration"]["render"]["size"] == 80


def test_opaque_backgrounds_and_mismatched_tile_dimensions_are_rejected(saved: SavedRun) -> None:
    data = saved.manifest
    data["configuration"]["render"]["background"] = "white"
    data["configuration"]["composition"]["background"] = "white"
    (saved.root / "manifest.json").write_text(json.dumps(data))
    opaque = SavedRun(saved.root)
    with pytest.raises(ConfigurationError, match="opaque background"):
        opaque.prepare("compose", replace(opaque.options, background="black"))
    tile = saved.root / "renders/protein/ph-5.0.png"
    Image.new("RGBA", (80, 80)).save(tile)
    record = next(
        item for item in data["tasks"][0]["stages"][-1]["outputs"] if item["path"].endswith(".png")
    )
    record.update(sha256=sha256_file(tile), size=tile.stat().st_size)
    (saved.root / "manifest.json").write_text(json.dumps(data))
    with pytest.raises(ConfigurationError, match="dimensions"):
        SavedRun(saved.root).prepare("compose")


def test_windows_recorded_paths_can_be_relocated_on_other_platforms(saved: SavedRun) -> None:
    data = saved.manifest
    for task in data["tasks"]:
        for stage in task["stages"]:
            for output in stage["outputs"]:
                relative = Path(output["path"]).relative_to(saved.root)
                output["path"] = "C:\\old\\source\\" + str(relative).replace("/", "\\")
    (saved.root / "manifest.json").write_text(json.dumps(data))
    restored = SavedRun(saved.root)
    assert restored.prepare("compose").plan()["total"] == 2
    assert restored.prepare("render").plan()["total"] == 2


def test_saved_worker_round_trip_can_clear_precision_and_enable_ray(
    saved: SavedRun,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from phmap.cli import _options_from_args, build_parser
    from phmap.gui.jobs import JobManager

    data = saved.manifest
    data["configuration"]["composition"]["ph_label_precision"] = 1
    (saved.root / "manifest.json").write_text(json.dumps(data))
    saved = SavedRun(saved.root)
    options = replace(saved.options, ph_label_precision=None, ray_trace=True)
    workflow = saved.prepare("render", options, use_saved_view=False)

    def finish_without_tools(manager: JobManager, run_id: str) -> None:
        with manager._condition:
            manager._jobs[run_id]["status"] = "completed"

    monkeypatch.setattr(JobManager, "_execute", finish_without_tools)
    manager = JobManager(tmp_path / "gui-runs")
    manager.start()
    try:
        job = manager.submit_saved(workflow)
        arguments = build_parser().parse_args(manager._jobs[job]["argv"][3:])
        restored = _options_from_args(arguments, saved.options)
        assert restored.ph_label_precision is None
        assert restored.ray_trace is True
        assert arguments.auto_view is True
        assert restored.background is None
    finally:
        manager.close()


@pytest.mark.parametrize("malformation", ["list", "schema", "configuration", "outputs"])
def test_bad_saved_metadata_is_reported_cleanly(saved: SavedRun, malformation: str) -> None:
    data = saved.manifest
    if malformation == "list":
        data = []
    elif malformation == "schema":
        data["schema_version"] = 99
    elif malformation == "configuration":
        data["configuration"] = None
    else:
        data["tasks"][0]["stages"][-1]["outputs"] = None
    (saved.root / "manifest.json").write_text(json.dumps(data))
    with pytest.raises(ConfigurationError, match="manifest|metadata|artifacts"):
        SavedRun(saved.root).prepare("compose")


def test_cli_in_place_preserves_science_for_later_rendering(
    saved: SavedRun, tmp_path: Path, pymol_executable: Path,
) -> None:
    digest = saved.digest
    assert main(["compose", str(saved.root), "--font-size", "35", "--in-place"]) == 0
    edited = SavedRun(saved.root)
    assert edited.digest != digest and edited.manifest["run_id"] == "source"
    assert edited.options.font_size == 35
    assert (
        main(
            [
                "render",
                str(saved.root),
                "--size",
                "80",
                "--in-place",
                "--pymol-executable",
                str(pymol_executable),
            ]
        )
        == 0
    )
    rendered = SavedRun(saved.root)
    assert rendered.options.size == 80 and rendered.options.font_size == 35
    assert rendered.manifest["created_at"] == saved.manifest["created_at"]
    assert (saved.root / "inputs/protein.pdb").is_file()
    assert not (saved.root.parent / ".phmap-updates/source").exists()
    assert (
        main(["compose", str(saved.root), "--in-place", "--output-dir", str(tmp_path / "bad")]) == 2
    )


def test_saved_rendering_runs_in_parallel_and_keeps_input_order(
    saved: SavedRun,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pymol_executable: Path,
) -> None:
    monkeypatch.setattr("phmap.configuration.available_cpus", lambda: 4)
    render = PyMOLBackend.render
    barrier = threading.Barrier(2)

    def overlap(backend, request, **kwargs):  # type: ignore[no-untyped-def]
        assert request.options.max_threads == 1
        assert backend.runner.environment["OMP_NUM_THREADS"] == "1"
        barrier.wait(timeout=5)
        return render(backend, request, **kwargs)

    monkeypatch.setattr(PyMOLBackend, "render", overlap)
    workflow = saved.prepare(
        "render",
        replace(
            saved.options,
            workers=2,
            output_dir=tmp_path / "parallel-render",
            pymol_executable=pymol_executable,
        ),
    )
    assert workflow.plan()["execution"]["effective_workers"] == 2
    result = workflow.run()
    data = json.loads(result.manifest.read_bytes())
    assert [task["ph"] for task in data["tasks"]] == ["5.0", "7.0"]
    assert all(task["status"] == "completed" for task in data["tasks"])
    assert data["configuration"]["execution"]["workers"] == 2


@pytest.mark.parametrize("workers", [1, 2])
def test_interrupted_saved_render_finalizes_all_unfinished_cells(
    saved: SavedRun,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    workers: int,
    pymol_executable: Path,
) -> None:
    monkeypatch.setattr("phmap.configuration.available_cpus", lambda: 4)

    def interrupt(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise KeyboardInterrupt

    monkeypatch.setattr(PyMOLBackend, "render", interrupt)
    workflow = saved.prepare(
        "render",
        replace(
            saved.options,
            workers=workers,
            output_dir=tmp_path / "interrupted-render",
            pymol_executable=pymol_executable,
        ),
    )
    with pytest.raises(KeyboardInterrupt):
        workflow.run()
    data = json.loads((tmp_path / "interrupted-render/manifest.json").read_bytes())
    assert data["status"] == "cancelled"
    assert all(task["status"] == "cancelled" for task in data["tasks"])
    assert all("current_stage" not in task for task in data["tasks"])
    assert sha256_file(saved.root / "manifest.json") == saved.digest


def test_cli_recovers_a_missing_source_before_loading_its_manifest(saved: SavedRun) -> None:
    update = RunUpdate(saved.root)
    update.begin(saved.digest)
    saved.root.rename(update.previous)
    update.lock.release()  # Simulate a process exit between the commit renames.
    assert not saved.root.exists()
    assert main(["compose", str(saved.root), "--in-place", "--font-size", "30"]) == 0
    recovered = SavedRun(saved.root)
    assert recovered.options.font_size == 30
    assert recovered.manifest["created_at"] == saved.manifest["created_at"]
    assert not update.folder.exists()
