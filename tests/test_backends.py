from __future__ import annotations

import json
import stat
import sys
import textwrap
from pathlib import Path

import pytest

from phmap.backends import (
    ApbsBackend,
    ApbsRequest,
    ArtifactError,
    BackendValidationError,
    CommandExecutionError,
    CommandRunner,
    CommandTimeoutError,
    Pdb2pqrBackend,
    Pdb2pqrRequest,
    PyMOLBackend,
    PyMOLRenderOptions,
    PyMOLRenderRequest,
    build_apbs_argv,
    build_pdb2pqr_argv,
    build_pymol_script,
    discover_executable,
    pdb2pqr_dx_path,
)


def _executable(tmp_path: Path, name: str, source: str) -> Path:
    path = tmp_path / name
    path.write_text(
        f"#!{sys.executable}\n{textwrap.dedent(source)}", encoding="utf-8"
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_runner_uses_argv_and_keeps_per_stage_logs(tmp_path: Path) -> None:
    runner = CommandRunner(tmp_path / "logs")
    shell_syntax = "$(touch should-not-exist); literal value"

    result = runner.run(
        "protein-7.0-pdb2pqr",
        (
            sys.executable,
            "-c",
            "import sys; print(sys.argv[1]); print('warning', file=sys.stderr)",
            shell_syntax,
        ),
        cwd=tmp_path,
    )

    assert result.succeeded
    assert result.argv[-1] == shell_syntax
    assert result.read_stdout().strip() == shell_syntax
    assert result.read_stderr().strip() == "warning"
    assert result.stdout_path.name == "protein-7.0-pdb2pqr.stdout.log"
    assert not (tmp_path / "should-not-exist").exists()


def test_runner_reports_nonzero_exit_and_can_return_it_unchecked(
    tmp_path: Path,
) -> None:
    runner = CommandRunner(tmp_path / "logs")
    command = (
        sys.executable,
        "-c",
        "import sys; print('details', file=sys.stderr); raise SystemExit(7)",
    )

    with pytest.raises(CommandExecutionError) as caught:
        runner.run("failed", command, cwd=tmp_path)

    assert caught.value.result.returncode == 7
    assert caught.value.result.read_stderr().strip() == "details"
    unchecked = runner.run("unchecked", command, cwd=tmp_path, check=False)
    assert unchecked.returncode == 7


def test_runner_timeout_and_stage_path_validation(tmp_path: Path) -> None:
    runner = CommandRunner(tmp_path / "logs", default_timeout=0.05)

    with pytest.raises(CommandTimeoutError) as caught:
        runner.run(
            "slow",
            (sys.executable, "-c", "import time; time.sleep(1)"),
            cwd=tmp_path,
        )

    assert "timed out" in caught.value.stderr_path.read_text(encoding="utf-8")
    with pytest.raises(BackendValidationError):
        runner.run("../escape", (sys.executable, "--version"), cwd=tmp_path)
    assert not (tmp_path / "escape.stdout.log").exists()


def test_executable_discovery_and_version_probe(tmp_path: Path) -> None:
    tool = _executable(
        tmp_path,
        "version-tool",
        r"""
        import sys
        assert sys.argv[1:] == ["--version"]
        print("version-tool 2.4.1")
        """,
    )

    assert discover_executable(tool) == tool.resolve()
    version = CommandRunner.probe_version(tool)
    assert version.returncode == 0
    assert version.output == "version-tool 2.4.1"
    assert version.argv == (str(tool.resolve()), "--version")


def test_modern_pdb2pqr_command_is_explicit(tmp_path: Path) -> None:
    request = Pdb2pqrRequest(
        pdb_path=tmp_path / "input protein.pdb",
        pqr_path=tmp_path / "model.pqr",
        apbs_input_path=tmp_path / "model.in",
        ph=7.25,
        ligand_path=tmp_path / "ligand file.mol2",
    )

    argv = build_pdb2pqr_argv(request)

    assert argv[0] == "pdb2pqr30"
    assert "--ff=PARSE" in argv
    assert "--with-ph=7.25" in argv
    assert "--titration-state-method=propka" in argv
    assert f"--apbs-input={request.apbs_input_path.resolve()}" in argv
    assert request.ligand_path is not None
    assert f"--ligand={request.ligand_path.resolve()}" in argv
    assert argv[-2:] == (
        str(request.pdb_path.resolve()),
        str(request.pqr_path.resolve()),
    )
    assert pdb2pqr_dx_path(request.pqr_path) == tmp_path / "model.pqr.dx"

    close_values = [
        Pdb2pqrRequest(
            pdb_path=tmp_path / "input.pdb",
            pqr_path=tmp_path / f"model-{index}.pqr",
            apbs_input_path=tmp_path / f"model-{index}.in",
            ph=value,
        )
        for index, value in enumerate(
            ("7.0000000000001", "7.0000000000002"), start=1
        )
    ]
    assert [build_pdb2pqr_argv(item)[1] for item in close_values] == [
        "--ff=PARSE",
        "--ff=PARSE",
    ]
    assert [build_pdb2pqr_argv(item)[2] for item in close_values] == [
        "--with-ph=7.0000000000001",
        "--with-ph=7.0000000000002",
    ]

    with pytest.raises(BackendValidationError, match="must share a directory"):
        Pdb2pqrRequest(
            pdb_path=tmp_path / "input.pdb",
            pqr_path=tmp_path / "pqr" / "model.pqr",
            apbs_input_path=tmp_path / "apbs" / "model.in",
            ph=7.0,
        )


def test_pdb2pqr_backend_validates_both_generated_artifacts(tmp_path: Path) -> None:
    tool = _executable(
        tmp_path,
        "pdb2pqr30",
        r"""
        import pathlib
        import sys

        args = sys.argv[1:]
        apbs_input = next(arg.split("=", 1)[1] for arg in args if arg.startswith("--apbs-input="))
        pathlib.Path(apbs_input).write_text("read\nend\n", encoding="utf-8")
        pathlib.Path(args[-1]).write_text("ATOM 1 N ALA 1 0 0 0 0 1.5\n", encoding="utf-8")
        print("pdb2pqr complete")
        """,
    )
    pdb = tmp_path / "inputs" / "protein.pdb"
    pdb.parent.mkdir()
    pdb.write_text("ATOM\n", encoding="utf-8")
    work = tmp_path / "work with spaces"
    request = Pdb2pqrRequest(
        pdb_path=pdb,
        pqr_path=work / "model.pqr",
        apbs_input_path=work / "apbs.in",
        ph=5.0,
        work_dir=work,
    )
    backend = Pdb2pqrBackend(CommandRunner(tmp_path / "logs"), tool)

    artifacts = backend.run(request, stage="protein-5.0-pdb2pqr")

    assert artifacts.pqr_path.read_text(encoding="utf-8").startswith("ATOM")
    assert artifacts.apbs_input_path.read_text(encoding="utf-8") == "read\nend\n"
    assert "pdb2pqr complete" in artifacts.command.read_stdout()

    no_output = _executable(tmp_path, "pdb2pqr-no-output", "raise SystemExit(0)\n")
    missing_request = Pdb2pqrRequest(
        pdb_path=pdb,
        pqr_path=tmp_path / "missing" / "model.pqr",
        apbs_input_path=tmp_path / "missing" / "apbs.in",
        ph=6.0,
    )
    with pytest.raises(ArtifactError, match="PQR output"):
        Pdb2pqrBackend(CommandRunner(tmp_path / "other-logs"), no_output).run(
            missing_request
        )


def test_apbs_backend_checks_dx_and_optional_apbs_output(tmp_path: Path) -> None:
    tool = _executable(
        tmp_path,
        "apbs",
        r"""
        import pathlib
        import sys

        args = sys.argv[1:]
        input_path = pathlib.Path(args[-1])
        dx_path = pathlib.Path(input_path.read_text(encoding="utf-8").strip())
        dx_path.write_text("object 1 class gridpositions counts 1 1 1\n", encoding="utf-8")
        for arg in args:
            if arg.startswith("--output-file="):
                pathlib.Path(arg.split("=", 1)[1]).write_text("APBS complete\n", encoding="utf-8")
        print("solver stdout")
        """,
    )
    work = tmp_path / "apbs work"
    work.mkdir()
    dx = work / "potential.dx"
    apbs_input = work / "model.in"
    apbs_input.write_text(str(dx), encoding="utf-8")
    request = ApbsRequest(
        apbs_input_path=apbs_input,
        dx_path=dx,
        apbs_output_path=work / "solver.out",
    )

    assert build_apbs_argv(request, tool)[-1] == str(apbs_input.resolve())
    artifacts = ApbsBackend(CommandRunner(tmp_path / "logs"), tool).run(request)

    assert artifacts.dx_path.read_text(encoding="utf-8").startswith("object")
    assert artifacts.apbs_output_path is not None
    assert artifacts.apbs_output_path.read_text(encoding="utf-8") == "APBS complete\n"


def test_pymol_options_reject_raw_command_content_and_bad_views() -> None:
    with pytest.raises(BackendValidationError):
        PyMOLRenderOptions(background="white\nquit")
    with pytest.raises(BackendValidationError):
        PyMOLRenderOptions(view=(0.0,) * 17)
    with pytest.raises(BackendValidationError):
        PyMOLRenderOptions(level=float("nan"))
    with pytest.raises(BackendValidationError):
        PyMOLRenderOptions(width=500.5)  # type: ignore[arg-type]
    with pytest.raises(BackendValidationError):
        PyMOLRenderOptions(view=(0.0,) * 17 + (True,))


def test_backend_numeric_booleans_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(BackendValidationError):
        CommandRunner(tmp_path / "logs", default_timeout=True)
    with pytest.raises(BackendValidationError):
        Pdb2pqrRequest(
            pdb_path=tmp_path / "model.pdb",
            pqr_path=tmp_path / "model.pqr",
            apbs_input_path=tmp_path / "model.in",
            ph=True,
        )


def test_pymol_script_quotes_paths_and_serialises_numeric_view(tmp_path: Path) -> None:
    request = PyMOLRenderRequest(
        pqr_path=tmp_path / 'protein "alpha"; literal.pqr',
        dx_path=tmp_path / "potential map.dx",
        png_path=tmp_path / "surface image.png",
        options=PyMOLRenderOptions(
            view=tuple(float(value) for value in range(18)),
            transparent_background=True,
            ligand_representation=None,
        ),
    )

    script = build_pymol_script(request)

    quoted_pqr = json.dumps(str(request.pqr_path.resolve()), ensure_ascii=True)
    assert f"load {quoted_pqr}, molecule" in script
    assert "disable electrostatic" in script
    quoted_png = json.dumps(str(request.png_path.resolve()), ensure_ascii=True)
    assert f"cmd.png({quoted_png}, width=500, height=500, dpi=300, ray=1)" in script
    assert "set_view (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17)" in script
    assert "set opaque_background, 0" in script
    assert script[-6:] == "\nquit\n"

    control_path_request = PyMOLRenderRequest(
        pqr_path=Path("bad\nquit.pqr"),
        dx_path=tmp_path / "potential.dx",
        png_path=tmp_path / "surface.png",
    )
    with pytest.raises(BackendValidationError, match="control characters"):
        build_pymol_script(control_path_request)


def test_pymol_backend_writes_script_and_validates_png(tmp_path: Path) -> None:
    tool = _executable(
        tmp_path,
        "pymol",
        r'''
        import json
        import pathlib
        import re
        import sys

        assert sys.argv[1] == "-cq"
        script = pathlib.Path(sys.argv[2]).read_text(encoding="utf-8")
        match = re.search(r'^cmd\.png\(("(?:\\.|[^"\\])*")', script, re.MULTILINE)
        assert match is not None
        output = pathlib.Path(json.loads(match.group(1)))
        output.write_bytes(b"\x89PNG\r\n\x1a\n" + b"fake image bytes")
        print("render complete")
        ''',
    )
    work = tmp_path / "render work"
    work.mkdir()
    pqr = work / "model.pqr"
    dx = work / "potential.dx"
    pqr.write_text("ATOM\n", encoding="utf-8")
    dx.write_text("object\n", encoding="utf-8")
    request = PyMOLRenderRequest(
        pqr_path=pqr,
        dx_path=dx,
        png_path=work / "surface.png",
        script_path=work / "render script.pml",
    )

    artifacts = PyMOLBackend(CommandRunner(tmp_path / "logs"), tool).render(request)

    assert artifacts.png_path.read_bytes().startswith(b"\x89PNG")
    assert artifacts.script_path.is_file()
    assert request.script_path is not None
    assert artifacts.command.argv == (
        str(tool.resolve()),
        "-cq",
        str(request.script_path.resolve()),
    )

    bad_tool = _executable(
        tmp_path,
        "bad-pymol",
        r'''
        import json
        import pathlib
        import re
        import sys

        script = pathlib.Path(sys.argv[2]).read_text(encoding="utf-8")
        path = re.search(r'^cmd\.png\(("(?:\\.|[^"\\])*")', script, re.MULTILINE).group(1)
        pathlib.Path(json.loads(path)).write_text("not a PNG", encoding="utf-8")
        ''',
    )
    bad_request = PyMOLRenderRequest(
        pqr_path=pqr,
        dx_path=dx,
        png_path=work / "invalid.png",
    )
    with pytest.raises(ArtifactError, match="missing PNG file signature"):
        PyMOLBackend(CommandRunner(tmp_path / "bad-logs"), bad_tool).render(
            bad_request
        )
