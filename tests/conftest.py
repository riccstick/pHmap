"""Keep user-level application preferences isolated from the developer's files."""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_preferences(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHMAP_PREFERENCES_PATH", str(tmp_path / "preferences.json"))
