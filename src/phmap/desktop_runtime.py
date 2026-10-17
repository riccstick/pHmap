"""Install and activate a desktop scientific environment independently of the UI."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from phmap import __version__
from phmap.errors import ConfigurationError
from phmap.processes import hidden_process_options

Spawn = Callable[[Sequence[str], dict[str, str], Path], subprocess.Popen[bytes]]


@dataclass(frozen=True, slots=True)
class DesktopRuntime:
    """Keep runtime preparation testable without Tk, HTTP, or network downloads."""

    data_dir: Path
    resources: Path
    log_path: Path
    spawn: Spawn
    progress: Callable[[str], None]
    project: Path | None = None

    def prepare(self, env: dict[str, str]) -> tuple[Path, Path, dict[str, str]]:
        if self.project:
            root, environment = self.project, "default"
            pixi = shutil.which("pixi")
            for candidate in (Path("/opt/homebrew/bin/pixi"), Path.home() / ".pixi/bin/pixi"):
                if not pixi and candidate.is_file():
                    pixi = str(candidate)
        else:
            root = self._copy_payload()
            environment = "runtime"
            pixi = str(self.resources / "tools" / ("pixi.exe" if os.name == "nt" else "pixi"))
        prefix = root / ".pixi" / "envs" / environment
        python = prefix / ("python.exe" if os.name == "nt" else "bin/python")
        installed = root / ".desktop-ready"
        if not self.project and not installed.exists():
            self.progress("First-time setup: downloading Python, PDB2PQR, APBS and PyMOL…")
            assert pixi is not None
            process = self.spawn(
                [pixi, "install", "--locked", "--environment", environment,
                 "--manifest-path", str(root / "pixi.toml")],
                env, root,
            )
            if process.wait() != 0:
                raise ConfigurationError(f"Environment setup failed. See {self.log_path}")
            installed.touch()
        if not python.is_file():
            raise ConfigurationError(
                "The scientific environment is missing. Run the project setup."
            )
        if pixi:
            self._activate(pixi, root, environment, env)
        else:
            paths = [prefix / "bin"]
            if os.name == "nt":
                paths = [prefix, prefix / "Library/bin", prefix / "Scripts"]
            env["PATH"] = (
                os.pathsep.join(str(path) for path in paths) + os.pathsep + env.get("PATH", "")
            )
        env["PYTHONPATH"] = str(root / "src")
        return root, python, env

    def _copy_payload(self) -> Path:
        identity = (self.resources / "runtime-id.txt").read_text().strip()
        if not identity or any(c not in "0123456789abcdef" for c in identity):
            raise ConfigurationError("The release's runtime identity is invalid")
        root = self.data_dir / "runtimes" / f"{__version__}-{identity[:16]}"
        if not root.exists():
            self.progress("Preparing your local pHmap installation…")
            root.parent.mkdir(parents=True, exist_ok=True)
            staging = root.with_name(root.name + ".staging")
            # Recover interrupted copying without touching installed runtimes.
            staging.mkdir(exist_ok=True)
            shutil.copytree(self.resources / "runtime", staging, dirs_exist_ok=True)
            staging.rename(root)
        return root

    def _activate(
        self, pixi: str, root: Path, environment: str, env: dict[str, str],
    ) -> None:
        # --as-is forbids installs or lock changes, including offline restarts.
        activated = subprocess.run(
            [pixi, "shell-hook", "--json", "--as-is", "--environment", environment,
             "--manifest-path", str(root / "pixi.toml")],
            cwd=root, env=env, capture_output=True, text=True, check=False, timeout=30,
            **hidden_process_options(),
        )
        if activated.returncode:
            with self.log_path.open("a", encoding="utf-8") as log:
                log.write(activated.stderr)
            raise ConfigurationError(f"Environment activation failed. See {self.log_path}")
        variables = json.loads(activated.stdout)["environment_variables"]
        env.update({key: value for key, value in variables.items() if isinstance(value, str)})
