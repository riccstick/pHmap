from pathlib import Path

import pytest

from phmap.errors import WorkspaceError
from phmap.workspace import RunWorkspace, safe_path_part


def test_safe_path_part_removes_unsafe_characters() -> None:
    assert safe_path_part("mutant 1 / draft") == "mutant-1-draft"
    assert safe_path_part("...") == "item"


def test_workspace_is_isolated_and_rejects_reuse(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "smoke"
    workspace = RunWorkspace.create(run_dir, exact=True)

    assert workspace.root == run_dir.resolve()
    assert workspace.inputs.is_dir()
    assert workspace.work.is_dir()
    assert workspace.output.is_dir()

    with pytest.raises(WorkspaceError, match="already exists"):
        RunWorkspace.create(run_dir, exact=True)


def test_workspace_stages_inputs_without_overwriting(tmp_path: Path) -> None:
    source = tmp_path / "protein with spaces.pdb"
    source.write_text("ATOM\n", encoding="utf-8")
    workspace = RunWorkspace.create(tmp_path / "run", exact=True)

    staged = workspace.stage_input(source, name="protein.pdb")

    assert staged.read_text(encoding="utf-8") == "ATOM\n"
    assert source.is_file()
    with pytest.raises(WorkspaceError, match="already exists"):
        workspace.stage_input(source, name="protein.pdb")

    with pytest.raises(WorkspaceError, match="one safe path component"):
        workspace.stage_input(source, name="../escaped.pdb")
    assert not (workspace.root / "escaped.pdb").exists()


def test_task_paths_are_explicit_and_nested(tmp_path: Path) -> None:
    workspace = RunWorkspace.create(tmp_path / "run", exact=True)

    paths = workspace.task_paths("mutant one", "5.0")

    assert paths.root == workspace.work / "mutant-one" / "ph-5.0"
    assert paths.pqr.parent == paths.root
    assert paths.tile.parent == workspace.renders / "mutant-one"
    assert paths.pdb2pqr_stdout.parent == workspace.logs / "mutant-one" / "ph-5.0"
