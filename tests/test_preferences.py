from __future__ import annotations

import json
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from phmap.errors import ConfigurationError
from phmap.gui.app import create_app
from phmap.gui.preferences import PreferencesStore, factory_defaults, validate_defaults
from phmap.json_state import write_json


def test_preferences_are_global_not_versioned_or_tied_to_a_run_folder(tmp_path: Path) -> None:
    path = tmp_path / "user/preferences.json"
    store = PreferencesStore(path)
    assert not path.exists() and not store.read()["onboarding_completed"]
    defaults = factory_defaults()
    defaults.update(size=1200, workers=4, force_field="AMBER", background="black")
    store.save(defaults)
    for folder in ("project-one", "project-two"):
        with TestClient(create_app(tmp_path / folder, preferences_path=path),
                        base_url="http://127.0.0.1") as client:
            data = client.get("/api/preferences").json()
            assert data["onboarding_completed"]
            assert data["defaults"]["size"] == 1200
            assert data["defaults"]["workers"] == 4
            assert data["defaults"]["foreground"] == "auto"
            assert 'id="preferences-data"' in client.get("/").text


@pytest.mark.parametrize("changes", [
    {"workers": 0}, {"workers": True}, {"size": 99999}, {"force_field": "OTHER"},
    {"level": float("nan")}, {"foreground": "invalid color"}, {"surface_mode": 8},
    {"font_size": 0}, {"view": [0] * 18}, {"structures": []}, {"name": "protein"},
    {"source_run_id": "old"}, {"pymol_executable": "arbitrary"},
])
def test_invalid_or_run_specific_defaults_are_rejected(changes: dict) -> None:
    with pytest.raises((ConfigurationError, ValueError)):
        validate_defaults(changes)


@pytest.mark.parametrize("contents", [
    "not json", "[]", '{"schema_version":99}',
    '{"schema_version":1,"onboarding_completed":true,"defaults":{"workers":0}}',
])
def test_damaged_or_newer_preferences_are_not_silently_overwritten(
    tmp_path: Path, contents: str,
) -> None:
    path = tmp_path / "preferences.json"
    path.write_text(contents)
    store = PreferencesStore(path)
    assert store.read()["warning"]
    assert store.read()["defaults"] == factory_defaults()
    with pytest.raises(ConfigurationError, match="Reset"):
        store.save({"workers": 2})
    assert path.read_text() == contents
    assert not store.save(reset=True)["warning"]


def test_preferences_api_is_protected_and_skip_is_persistent(tmp_path: Path) -> None:
    path = tmp_path / "preferences.json"
    with TestClient(create_app(tmp_path / "runs", preferences_path=path),
                    base_url="http://127.0.0.1") as client:
        import re

        token = re.search(r'name="phmap-token" content="([^"]+)"', client.get("/").text)[1]
        headers = {"X-Phmap-Token": token}
        assert client.put("/api/preferences", json={"defaults": {"workers": 4}}).status_code == 403
        assert client.put("/api/preferences", headers=headers,
                          json={"defaults": {"workers": 0}}).status_code == 422
        assert not path.exists()
        assert client.post("/api/preferences/skip", headers=headers).status_code == 200
        assert PreferencesStore(path).read()["onboarding_completed"]
        saved = client.put("/api/preferences", headers=headers,
                           json={"defaults": {"workers": 4, "size": 1000}})
        assert saved.status_code == 200
        assert saved.json()["defaults"]["workers"] == 4
        assert client.post("/api/preferences/reset", headers=headers).json()["defaults"] == (
            factory_defaults()
        )


def test_preferences_reject_symlink_files(tmp_path: Path) -> None:
    target = tmp_path / "original.json"
    write_json(target, {"keep": "untouched"})
    link = tmp_path / "preferences.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("Symlinks are unavailable")
    store = PreferencesStore(link)
    assert store.read()["warning"]
    with pytest.raises(ConfigurationError, match="symbolic"):
        store.save(reset=True)
    assert json.loads(target.read_bytes()) == {"keep": "untouched"}


def test_wizard_groups_run_inputs_and_keeps_defaults_outside_the_calculation_form(
    tmp_path: Path,
) -> None:
    class Structure(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.stack: list[tuple[str, str | None]] = []
            self.elements: dict[str, list[str | None]] = {}

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            identity = dict(attrs).get("id")
            if identity:
                assert identity not in self.elements, f"Duplicate control ID: {identity}"
                self.elements[identity] = [item[1] for item in self.stack]
            if tag not in {"meta", "link", "input", "img", "br", "hr"}:
                self.stack.append((tag, identity))

        def handle_endtag(self, tag: str) -> None:
            assert self.stack and self.stack[-1][0] == tag, f"Unbalanced {tag}"
            self.stack.pop()

    with TestClient(create_app(tmp_path / "runs"), base_url="http://127.0.0.1") as client:
        parser = Structure()
        parser.feed(client.get("/").text)
        assert not parser.stack
        for identity in ("proteins", "pdb-code", "view", "ligand"):
            assert "wizard-proteins" in parser.elements[identity]
        for identity in ("ph-start", "ph-stop", "ph-step", "ph-values"):
            assert "wizard-ph" in parser.elements[identity]
        for identity in ("workers", "size", "dpi", "force-field", "font-size", "surface-mode"):
            assert "calculation-overrides" in parser.elements[identity]
            assert "wizard-review" in parser.elements[identity]
        assert "run-form" not in parser.elements["preferences-form"]
        assert "calculation-dialog" in parser.elements["run-form"]
        assert "run-form" not in parser.elements["done-editing"]
