"""Regress the scientific environment's transitive NumPy stub incompatibility."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(("annotation", "exit_code"), [("int", 0), ("str", 1)])
def test_numpy_stubs_are_skipped_without_disabling_other_library_types(
    tmp_path: Path, annotation: str, exit_code: int,
) -> None:
    root = Path(__file__).resolve().parents[1]
    numpy = tmp_path / "numpy"
    numpy.mkdir()
    # This syntax must not be parsed when checking pHmap's Python 3.11 minimum.
    (numpy / "__init__.pyi").write_text("type ModernAlias = int\n", encoding="utf-8")
    (tmp_path / "typed_dependency.py").write_text(
        "def number() -> int:\n    return 42\n", encoding="utf-8",
    )
    sample = tmp_path / "sample.py"
    sample.write_text(
        "import numpy\nfrom typed_dependency import number\n"
        f"value: {annotation} = number()\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable, "-m", "mypy", "--config-file", str(root / "pyproject.toml"),
            "--no-incremental", "--cache-dir", str(tmp_path / "cache"), str(sample),
        ],
        cwd=root, env={**os.environ, "MYPYPATH": str(tmp_path)},
        capture_output=True, text=True, timeout=60, check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode == exit_code, output
    assert "[syntax]" not in output
    if exit_code:
        assert "[assignment]" in output
    else:
        assert "Success" in output
