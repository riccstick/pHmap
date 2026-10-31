from __future__ import annotations

import importlib
import re
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from phmap.gui.app import create_app


def _assets(page: str) -> list[str]:
    return re.findall(r'(?:src|href)="(/static/[^"]+)"', page)


def test_html_and_all_assets_use_one_content_revision_and_are_not_cached(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "runs"), base_url="http://127.0.0.1") as client:
        page = client.get("/")
        assert '<p class="brand-caption">Surface charge across pH</p>' in page.text
        revision = re.search(r'name="phmap-ui-version" content="([0-9a-f]+)"', page.text)[1]
        assert len(revision) == 16
        assert page.headers["cache-control"] == "no-store"
        assert page.text.index("/static/bootstrap.js") < page.text.index("/static/app.js")
        for url in _assets(page.text):
            assert url.endswith(f"?v={revision}")
            response = client.get(url)
            assert response.status_code == 200, url
            assert response.headers["cache-control"] == "no-store"
        assert client.get(f"/?ui={revision}").status_code == 200


@pytest.mark.parametrize("changed", ["static/app.css", "static/app.js", "templates/index.html"])
def test_changes_invalidate_asset_urls_even_without_a_release_version_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed: str,
) -> None:
    module = importlib.import_module("phmap.gui.app")
    copied = tmp_path / "assets"
    shutil.copytree(module._ASSETS / "static", copied / "static")
    shutil.copytree(module._ASSETS / "templates", copied / "templates")
    monkeypatch.setattr(module, "_ASSETS", copied)
    with TestClient(create_app(tmp_path / "runs"), base_url="http://127.0.0.1") as client:
        old = _assets(client.get("/").text)
        file = copied / changed
        file.write_text(
            file.read_text(encoding="utf-8") + "\n/* Test change */\n", encoding="utf-8",
        )
        new = _assets(client.get("/").text)
        assert old != new
        assert all(before != after for before, after in zip(old, new, strict=True))
        assert _assets(client.get("/").text) == new
        for url in new:
            assert client.get(url).status_code == 200
