from __future__ import annotations

import json
from pathlib import Path

import pytest
from fake_structure import CIF
from fake_tools import fake_tools as _fake_tools
from PIL import Image

from phmap.backends import (
    ArtifactError,
    CommandExecutionError,
    CommandStartError,
    CommandTimeoutError,
)
from phmap.inputs import validate_run_inputs
from phmap.workflow import LocalWorkflow, WorkflowOptions


def _input_pdb(tmp_path: Path) -> Path:
    pdb = tmp_path / "input protein.pdb"
    pdb.write_text("ATOM\n", encoding="utf-8")
    return pdb


def test_two_ph_vertical_slice_is_isolated_and_manifested(tmp_path: Path) -> None:
    pdb = _input_pdb(tmp_path)
    pdb2pqr, apbs, pymol = _fake_tools(tmp_path)
    inputs = validate_run_inputs(pdb, ph_values=["5.0", "7.0"])
    run_dir = tmp_path / "runs" / "smoke"

    result = LocalWorkflow(
        inputs,
        WorkflowOptions(
            output_dir=run_dir,
            size=64,
            ray_trace=False,
            pdb2pqr_executable=pdb2pqr,
            apbs_executable=apbs,
            pymol_executable=pymol,
        ),
    ).run()

    assert result.workspace.root == run_dir.resolve()
    assert result.figure == run_dir / "output" / "pHmap.png"
    assert result.figure.is_file()
    assert pdb.read_text(encoding="utf-8") == "ATOM\n"
    assert not (tmp_path / "pHmap.png").exists()
    assert not (tmp_path / "render.pml").exists()

    manifest = json.loads(result.manifest.read_text(encoding="utf-8"))
    assert manifest["status"] == "completed"
    assert manifest["configuration"]["ph_values"] == ["5.0", "7.0"]
    assert list(manifest["tools"]) == ["apbs", "pdb2pqr", "pymol"]
    assert [task["ph"] for task in manifest["tasks"]] == ["5.0", "7.0"]
    assert all(
        [stage["name"] for stage in task["stages"]]
        == ["pdb2pqr", "apbs", "pymol"]
        for task in manifest["tasks"]
    )

    for ph in ("5.0", "7.0"):
        cell = run_dir / "work" / "input-protein" / f"ph-{ph}"
        assert (cell / "model.pqr").is_file()
        assert (cell / "apbs.in").is_file()
        assert (cell / "model.pqr.dx").is_file()
        assert (cell / "render.pml").is_file()

    with Image.open(result.figure) as image:
        rendered = image.convert("RGBA")
        red_x = [x for x in range(image.width) if rendered.getpixel((x, 30))[:3] == (240, 0, 0)]
        blue_x = [x for x in range(image.width) if rendered.getpixel((x, 30))[:3] == (0, 0, 240)]
        assert red_x and blue_x
        assert max(red_x) < min(blue_x)


@pytest.mark.parametrize("extension", [".cif", ".mmcif", ".CIF", ".MMCIF"])
def test_mmcif_bytes_are_preserved_and_staged_with_a_backend_compatible_suffix(
    tmp_path: Path, extension: str,
) -> None:
    source = tmp_path / ("variant" + extension)
    source.write_bytes(CIF)
    pdb2pqr, apbs, pymol = _fake_tools(tmp_path)
    result = LocalWorkflow(
        validate_run_inputs(source, ph_values=["7"]),
        WorkflowOptions(
            output_dir=tmp_path / "run", size=64, ray_trace=False,
            pdb2pqr_executable=pdb2pqr, apbs_executable=apbs, pymol_executable=pymol,
        ),
    ).run()
    assert (result.workspace.inputs / "variant.cif").read_bytes() == CIF
    assert source.read_bytes() == CIF
    manifest = json.loads(result.manifest.read_text(encoding="utf-8"))
    assert manifest["configuration"]["proteins"][0]["format"] == "mmcif"
    assert manifest["inputs"][0]["original_path"] == str(source)
    command = manifest["tasks"][0]["stages"][0]["argv"]
    assert command[-2].endswith("variant.backend.pdb")
    assert manifest["inputs"][1]["role"] == "backend-protein:variant"
    assert (result.workspace.inputs / "variant.backend.pdb").is_file()


def test_backend_failure_marks_manifest_and_keeps_logs(tmp_path: Path) -> None:
    pdb = _input_pdb(tmp_path)
    pdb2pqr, apbs, pymol = _fake_tools(tmp_path, fail_apbs=True)
    inputs = validate_run_inputs(pdb, ph_values=["5.0"])
    run_dir = tmp_path / "runs" / "failed"

    with pytest.raises(CommandExecutionError, match="status 4"):
        LocalWorkflow(
            inputs,
            WorkflowOptions(
                output_dir=run_dir,
                size=64,
                pdb2pqr_executable=pdb2pqr,
                apbs_executable=apbs,
                pymol_executable=pymol,
            ),
        ).run()

    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"
    assert manifest["tasks"][0]["status"] == "failed"
    assert [stage["name"] for stage in manifest["tasks"][0]["stages"]] == [
        "pdb2pqr",
        "apbs",
    ]
    failed_stage = manifest["tasks"][0]["stages"][-1]
    assert failed_stage["status"] == "failed"
    assert failed_stage["returncode"] == 4
    assert failed_stage["argv"][-1].endswith("apbs.in")
    stderr = run_dir / "logs" / "input-protein" / "ph-5.0" / "apbs.stderr.log"
    assert stderr.is_file()
    assert "solver failed" in stderr.read_text(encoding="utf-8")
    assert not (run_dir / "output" / "pHmap.png").exists()


@pytest.mark.parametrize("failure", ["timeout", "startup", "artifact", "unexpected"])
def test_every_stage_failure_is_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    pdb = _input_pdb(tmp_path)
    pdb2pqr, apbs, pymol = _fake_tools(tmp_path)
    inputs = validate_run_inputs(pdb, ph_values=["5"])
    run_dir = tmp_path / "runs" / "failed"

    def fail(self, request, *, timeout):  # type: ignore[no-untyped-def]
        if failure == "timeout":
            raise CommandTimeoutError(
                stage="apbs", timeout=timeout,
                stdout_path=self.runner.log_dir / "apbs.stdout.log",
                stderr_path=self.runner.log_dir / "apbs.stderr.log",
            )
        if failure == "startup":
            raise CommandStartError("apbs", str(apbs), OSError("Cannot start"))
        if failure == "artifact":
            raise ArtifactError(request.dx_path, "OpenDX", "Missing potential")
        raise ValueError("Unexpected backend error")

    monkeypatch.setattr("phmap.workflow.ApbsBackend.run", fail)
    with pytest.raises((CommandTimeoutError, CommandStartError, ArtifactError, ValueError)):
        LocalWorkflow(inputs, WorkflowOptions(
            output_dir=run_dir, size=64, pdb2pqr_executable=pdb2pqr,
            apbs_executable=apbs, pymol_executable=pymol,
        )).run()

    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    task = manifest["tasks"][0]
    assert manifest["status"] == task["status"] == "failed"
    assert "current_stage" not in task
    assert [stage["name"] for stage in task["stages"]] == ["pdb2pqr", "apbs"]
    stage = task["stages"][-1]
    assert stage["status"] == "failed"
    assert stage["started_at"] <= stage["finished_at"]
    assert stage["error"] == task["error"]
    assert stage["error_type"] in {
        "CommandTimeoutError", "CommandStartError", "ArtifactError", "ValueError",
    }
    assert stage["stdout"].endswith("apbs.stdout.log")
    assert stage["stderr"].endswith("apbs.stderr.log")
    if failure == "timeout":
        assert stage["timeout_seconds"] == 1800
    elif failure == "startup":
        assert stage["executable"] == str(apbs)
    elif failure == "artifact":
        assert stage["artifact"]["reason"] == "Missing potential"
