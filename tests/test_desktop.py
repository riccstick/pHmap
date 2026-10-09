from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_workflow import _fake_tools

from phmap.desktop import DesktopService, child_environment, matching_service
from phmap.errors import ConfigurationError
from phmap.gui.app import create_app
from phmap.gui.locking import LocalLock


def test_local_lock_is_exclusive_and_recoverable(tmp_path: Path) -> None:
    first = LocalLock(tmp_path / "service.lock")
    second = LocalLock(tmp_path / "service.lock")
    first.acquire()
    try:
        with pytest.raises(ConfigurationError, match="Another pHmap"):
            second.acquire()
    finally:
        first.release()
    second.acquire()
    second.release()


def test_desktop_shutdown_requires_own_secret(tmp_path: Path) -> None:
    app = create_app(tmp_path / "runs", desktop_token="launcher-secret")
    shutdown = []
    app.state.desktop_shutdown = lambda: shutdown.append(True)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get("/api/health").json()["application"] == "phmap"
        assert client.post("/api/desktop/shutdown").status_code == 403
        assert (
            client.post(
                "/api/desktop/shutdown", headers={"X-Phmap-Desktop-Token": "wrong"}
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/desktop/shutdown",
                headers={
                    "X-Phmap-Desktop-Token": "launcher-secret",
                    "Origin": "https://evil.example",
                },
            ).status_code
            == 403
        )
        assert shutdown == []
        assert client.post(
            "/api/desktop/shutdown", headers={"X-Phmap-Desktop-Token": "launcher-secret"}
        ).json() == {"stopping": True}
        assert shutdown == [True]


def test_cli_service_has_no_desktop_shutdown_authority(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path), base_url="http://127.0.0.1") as client:
        assert (
            client.post(
                "/api/desktop/shutdown", headers={"X-Phmap-Desktop-Token": "anything"}
            ).status_code
            == 403
        )


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://localhost:8765",
        "http://127.0.0.1:8765/path",
        "http://user@127.0.0.1:8765",
        "http://127.0.0.1:8765?x=1",
        "garbage",
    ],
)
def test_launcher_rejects_non_local_state_urls(tmp_path: Path, url: str) -> None:
    assert not matching_service(url, tmp_path)


def test_child_environment_restores_native_library_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LD_LIBRARY_PATH", "/frozen/libraries")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/native/libraries")
    monkeypatch.setenv("PYTHONHOME", "/frozen/python")
    monkeypatch.setenv("TCL_LIBRARY", "/frozen/tcl")
    env = child_environment()
    assert env["LD_LIBRARY_PATH"] == "/native/libraries"
    assert "PYTHONHOME" not in env
    assert "TCL_LIBRARY" not in env


def test_launcher_starts_reuses_and_stops_real_local_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_tools(tmp_path)
    project = Path(__file__).resolve().parents[1]

    def runtime(self: DesktopService, env: dict[str, str]):  # type: ignore[no-untyped-def]
        env["PYTHONPATH"] = str(project / "src")
        env["PATH"] = str(tmp_path / "tools") + os.pathsep + env.get("PATH", "")
        return project, Path(sys.executable), env

    monkeypatch.setattr(DesktopService, "_runtime", runtime)
    owner = DesktopService(data_dir=tmp_path / "storage")
    observer = DesktopService(data_dir=tmp_path / "storage")
    try:
        url = owner.start()
        assert matching_service(url, owner.runs_dir)
        assert observer.start() == url
        assert observer.reused
        observer.close()
        assert matching_service(url, owner.runs_dir)
        assert json.loads(owner.state_path.read_text())["url"] == url
    finally:
        observer.close()
        owner.close()
    assert not matching_service(url, owner.runs_dir)
    assert owner.process is not None and owner.process.poll() == 0
