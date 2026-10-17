import json
import os
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from phmap.desktop_runtime import DesktopRuntime
from phmap.errors import ConfigurationError


def _python(root: Path, environment: str) -> Path:
    return root / ".pixi/envs" / environment / ("python.exe" if os.name == "nt" else "bin/python")


def _payload(tmp_path: Path) -> Path:
    resources = tmp_path / "resources"
    (resources / "runtime/src").mkdir(parents=True)
    (resources / "runtime/pixi.toml").write_text("locked fixture", encoding="utf-8")
    (resources / "runtime-id.txt").write_text("a" * 64, encoding="utf-8")
    return resources


def _activation(monkeypatch: pytest.MonkeyPatch, *, returncode: int = 0) -> Mock:
    command = Mock(return_value=subprocess.CompletedProcess(
        [], returncode, stdout=json.dumps({"environment_variables": {"TOOLCHAIN": "ready"}}),
        stderr="Activation failure" if returncode else "",
    ))
    monkeypatch.setattr("phmap.desktop_runtime.subprocess.run", command)
    return command


def test_bundled_runtime_installs_once_and_restarts_without_installing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    resources = _payload(tmp_path)
    activation = _activation(monkeypatch)

    def install(argv, env, cwd):  # type: ignore[no-untyped-def]
        python = _python(cwd, "runtime")
        python.parent.mkdir(parents=True)
        python.touch()
        return Mock(wait=Mock(return_value=0))

    spawn = Mock(side_effect=install)
    runtime = DesktopRuntime(
        tmp_path / "storage", resources, tmp_path / "desktop.log", spawn, Mock(),
    )
    root, python, env = runtime.prepare({"PATH": "original"})
    assert python == _python(root, "runtime")
    assert (root / ".desktop-ready").is_file()
    assert (root / "pixi.toml").read_text(encoding="utf-8") == "locked fixture"
    assert env["TOOLCHAIN"] == "ready"
    assert env["PYTHONPATH"] == str(root / "src")
    assert spawn.call_args.args[0][1:4] == ["install", "--locked", "--environment"]
    assert runtime.prepare({})[0] == root
    assert spawn.call_count == 1
    assert activation.call_count == 2
    assert "--as-is" in activation.call_args.args[0]


def test_project_runtime_activates_existing_environment_without_installing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    python = _python(project, "default")
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setattr("phmap.desktop_runtime.shutil.which", lambda _: "pixi")
    activation = _activation(monkeypatch)
    spawn = Mock(side_effect=AssertionError("Must not install the project environment"))
    runtime = DesktopRuntime(tmp_path, tmp_path, tmp_path / "desktop.log", spawn, Mock(), project)
    assert runtime.prepare({})[:2] == (project, python)
    assert "default" in activation.call_args.args[0]
    spawn.assert_not_called()


def test_failed_install_does_not_mark_runtime_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    resources = _payload(tmp_path)
    activation = _activation(monkeypatch)
    spawn = Mock(return_value=Mock(wait=Mock(return_value=1)))
    runtime = DesktopRuntime(
        tmp_path / "storage", resources, tmp_path / "desktop.log", spawn, Mock(),
    )
    with pytest.raises(ConfigurationError, match="Environment setup failed"):
        runtime.prepare({})
    assert not list((tmp_path / "storage").rglob(".desktop-ready"))
    activation.assert_not_called()


def test_invalid_runtime_identity_is_rejected_without_installing(tmp_path: Path) -> None:
    resources = _payload(tmp_path)
    (resources / "runtime-id.txt").write_text("../../invalid", encoding="utf-8")
    spawn = Mock()
    runtime = DesktopRuntime(
        tmp_path / "storage", resources, tmp_path / "desktop.log", spawn, Mock(),
    )
    with pytest.raises(ConfigurationError, match="runtime identity is invalid"):
        runtime.prepare({})
    spawn.assert_not_called()
    assert not (tmp_path / "storage").exists()


def test_activation_failure_is_written_to_the_desktop_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    python = _python(project, "default")
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setattr("phmap.desktop_runtime.shutil.which", lambda _: "pixi")
    _activation(monkeypatch, returncode=1)
    log = tmp_path / "desktop.log"
    runtime = DesktopRuntime(tmp_path, tmp_path, log, Mock(), Mock(), project)
    with pytest.raises(ConfigurationError, match="Environment activation failed"):
        runtime.prepare({})
    assert log.read_text(encoding="utf-8") == "Activation failure"
