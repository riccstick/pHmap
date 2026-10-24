import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("script", [
    "run-selection.mjs", "protein-selection.mjs", "render-settings.mjs",
])
def test_frontend_controllers(script: str) -> None:
    node = shutil.which("node")
    if node is None:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            pytest.fail("Node.js is required for frontend regression checks in CI")
        pytest.skip("Install Node.js to run the optional local frontend checks")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [node, str(root / "tests/frontend" / script)],
        cwd=root, capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "checks passed" in result.stdout
