from __future__ import annotations

import json
import stat
import sys
import textwrap
from pathlib import Path

import pytest
from PIL import Image

from phmap.backends import CommandExecutionError
from phmap.inputs import validate_run_inputs
from phmap.workflow import LocalWorkflow, WorkflowOptions


def _executable(tmp_path: Path, name: str, source: str) -> Path:
    path = tmp_path / "tools" / name
    path.parent.mkdir(exist_ok=True)
    path.write_text(
        f"#!{sys.executable}\n{textwrap.dedent(source)}", encoding="utf-8"
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _fake_tools(tmp_path: Path, *, fail_apbs: bool = False) -> tuple[Path, Path, Path]:
    pdb2pqr = _executable(
        tmp_path,
        "pdb2pqr30",
        r'''
        import pathlib
        import sys

        if sys.argv[1:] == ["--version"]:
            print("pdb2pqr 3.7.1")
            raise SystemExit(0)
        args = sys.argv[1:]
        apbs_input = pathlib.Path(
            next(arg.split("=", 1)[1] for arg in args if arg.startswith("--apbs-input="))
        )
        pqr = pathlib.Path(args[-1])
        pqr.write_text("ATOM 1 N ALA A 1 0 0 0 -0.3 1.5\n", encoding="utf-8")
        apbs_input.write_text(str(pathlib.Path(f"{pqr}.dx")), encoding="utf-8")
        print("prepared", pqr)
        ''',
    )
    apbs_body = (
        r'''
        import sys
        if sys.argv[1:] == ["--version"]:
            print("APBS 3.4.1")
            raise SystemExit(0)
        print("solver failed", file=sys.stderr)
        raise SystemExit(4)
        '''
        if fail_apbs
        else r'''
        import pathlib
        import sys

        if sys.argv[1:] == ["--version"]:
            print("APBS 3.4.1")
            raise SystemExit(0)
        input_path = pathlib.Path(sys.argv[-1])
        dx = pathlib.Path(input_path.read_text(encoding="utf-8").strip())
        dx.write_text(
            "object 1 class gridpositions counts 1 1 2\n"
            "origin 0 0 0\n"
            "delta 1 0 0\n"
            "delta 0 1 0\n"
            "delta 0 0 1\n"
            "object 2 class gridconnections counts 1 1 2\n"
            "object 3 class array type double rank 0 items 2 data follows\n"
            "-1.0 1.0\n",
            encoding="utf-8",
        )
        print("solved", dx)
        '''
    )
    apbs = _executable(tmp_path, "apbs", apbs_body)
    pymol = _executable(
        tmp_path,
        "pymol",
        r'''
        import json
        import pathlib
        import re
        import sys
        from PIL import Image

        if sys.argv[1:] == ["--version"]:
            print("PyMOL 3.1.0")
            raise SystemExit(0)
        script = pathlib.Path(sys.argv[-1]).read_text(encoding="utf-8")
        output_match = re.search(
            r'^cmd\.png\(("(?:\\.|[^"\\])*")', script, re.MULTILINE
        )
        assert output_match is not None
        output = pathlib.Path(json.loads(output_match.group(1)))
        width = int(re.search(r'width=(\d+)', script).group(1))
        height = int(re.search(r'height=(\d+)', script).group(1))
        color = (240, 0, 0, 255) if "ph-5.0" in str(output) else (0, 0, 240, 255)
        Image.new("RGBA", (width, height), color).save(output)
        print("rendered", output)
        ''',
    )
    return pdb2pqr, apbs, pymol


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
