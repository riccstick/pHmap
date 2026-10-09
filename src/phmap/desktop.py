"""Double-click launcher; the scientific workflow runs in ordinary Python.

The frozen launcher contains Tk and Pixi, not a frozen scientific interpreter.
Pixi installs the locked native environment on first use, so GUI workers keep
using the same ``python -m phmap`` entry point as the command-line application.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any

from phmap import __version__
from phmap.errors import ConfigurationError
from phmap.gui.locking import LocalLock
from phmap.processes import hidden_process_options, terminate_tree


def data_directory() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "pHmap"
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "pHmap"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "phmap"


def resources() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).parents[2]))


def child_environment() -> dict[str, str]:
    """Do not pass the frozen launcher's libraries into scientific executables."""
    env = os.environ.copy()
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        # PyInstaller also changes the Windows DLL directory, which is inherited
        # independently of PATH. All launcher modules are loaded before setup.
        import ctypes

        ctypes.windll.kernel32.SetDllDirectoryW(None)
    for key in ("PYTHONHOME", "PYTHONPATH", "TCL_LIBRARY", "TK_LIBRARY"):
        env.pop(key, None)
    for key in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
        original = env.pop(f"{key}_ORIG", None)
        if original is None:
            env.pop(key, None)
        else:
            env[key] = original
    env.update(PYTHONNOUSERSITE="1", PYTHONUNBUFFERED="1", PIXI_NO_PROGRESS="true")
    return env


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def _request(url: str, *, token: str | None = None, timeout: float = 1) -> Any:
    # Local connections must not go through a corporate/system HTTP proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    request = urllib.request.Request(url)
    if token is not None:
        request.method = "POST"
        request.add_header("X-Phmap-Desktop-Token", token)
        request.data = b""
    with opener.open(request, timeout=timeout) as response:
        return json.loads(response.read(64 * 1024))


def matching_service(url: str, runs_dir: Path) -> bool:
    # Never open a URL read from a state file unless it is our loopback service.
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url)
        if (
            parts.scheme != "http"
            or parts.hostname != "127.0.0.1"
            or parts.port is None
            or parts.path
            or parts.query
            or parts.fragment
            or parts.username is not None
        ):
            return False
        data = _request(f"{url}/api/health")
        return bool(
            data.get("application") == "phmap"
            and Path(data.get("runs_dir", "")).resolve() == runs_dir.resolve()
        )
    except (OSError, ValueError, AttributeError):
        return False


def free_port() -> int:
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind(("127.0.0.1", 8765))
        except OSError:
            listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


class DesktopService:
    """Own setup, one HTTP child, and a launcher lock without touching Tk."""

    def __init__(
        self,
        *,
        project: Path | None = None,
        data_dir: Path | None = None,
        progress: Callable[[str], None] = lambda message: None,
    ) -> None:
        self.project = project.resolve() if project else None
        self.data_dir = (data_dir or data_directory()).expanduser().resolve()
        self.runs_dir = (
            self.project / "runs" if self.project and data_dir is None else self.data_dir / "runs"
        )
        self.log_path = self.runs_dir / ".gui" / "desktop.log"
        self.state_path = self.runs_dir / ".gui" / "desktop.json"
        self.lock = LocalLock(self.runs_dir / ".gui" / "desktop.lock")
        self.progress = progress
        self.process: subprocess.Popen[bytes] | None = None
        self.url = ""
        self.token = secrets.token_urlsafe(32)
        self.reused = False
        self._cancelled = threading.Event()

    def _spawn(self, argv: Sequence[str], env: dict[str, str], cwd: Path) -> None:
        if self._cancelled.is_set():
            raise ConfigurationError("Startup cancelled")
        with self.log_path.open("ab") as log:
            self.process = subprocess.Popen(
                argv,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                shell=False,
                start_new_session=os.name == "posix",
                **hidden_process_options(),
            )
        # Close can run while Popen is being constructed.
        if self._cancelled.is_set():
            terminate_tree(self.process, force=True)
            raise ConfigurationError("Startup cancelled")

    def _runtime(self, env: dict[str, str]) -> tuple[Path, Path, dict[str, str]]:
        if self.project:
            root, environment = self.project, "default"
            pixi = shutil.which("pixi")
            for candidate in (Path("/opt/homebrew/bin/pixi"), Path.home() / ".pixi/bin/pixi"):
                if not pixi and candidate.is_file():
                    pixi = str(candidate)
        else:
            payload = resources() / "runtime"
            identity = (resources() / "runtime-id.txt").read_text().strip()
            if not identity or any(c not in "0123456789abcdef" for c in identity):
                raise ConfigurationError("The release's runtime identity is invalid")
            root = self.data_dir / "runtimes" / f"{__version__}-{identity[:16]}"
            if not root.exists():
                self.progress("Preparing your local pHmap installation…")
                root.parent.mkdir(parents=True, exist_ok=True)
                staging = root.with_name(root.name + ".staging")
                # Recover from interrupted copying; never remove installed runtimes.
                staging.mkdir(exist_ok=True)
                shutil.copytree(payload, staging, dirs_exist_ok=True)
                staging.rename(root)
            environment = "runtime"
            pixi = str(resources() / "tools" / ("pixi.exe" if os.name == "nt" else "pixi"))
        prefix = root / ".pixi" / "envs" / environment
        python = prefix / ("python.exe" if os.name == "nt" else "bin/python")
        installed = root / ".desktop-ready"
        if not self.project and not installed.exists():
            self.progress("First-time setup: downloading Python, PDB2PQR, APBS and PyMOL…")
            assert pixi is not None
            self._spawn(
                [
                    pixi,
                    "install",
                    "--locked",
                    "--environment",
                    environment,
                    "--manifest-path",
                    str(root / "pixi.toml"),
                ],
                env,
                root,
            )
            assert self.process is not None
            if self.process.wait() != 0:
                raise ConfigurationError(f"Environment setup failed. See {self.log_path}")
            installed.touch()
        if not python.is_file():
            raise ConfigurationError(
                "The scientific environment is missing. Run the project setup."
            )
        if pixi:
            # --as-is forbids installs or lock changes and works offline after setup.
            activated = subprocess.run(
                [
                    pixi,
                    "shell-hook",
                    "--json",
                    "--as-is",
                    "--environment",
                    environment,
                    "--manifest-path",
                    str(root / "pixi.toml"),
                ],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
                **hidden_process_options(),
            )
            if activated.returncode:
                with self.log_path.open("a", encoding="utf-8") as log:
                    log.write(activated.stderr)
                raise ConfigurationError(f"Environment activation failed. See {self.log_path}")
            variables = json.loads(activated.stdout)["environment_variables"]
            env.update({key: value for key, value in variables.items() if isinstance(value, str)})
        else:
            paths = [prefix / "bin"]
            if os.name == "nt":
                paths = [prefix, prefix / "Library/bin", prefix / "Scripts"]
            env["PATH"] = (
                os.pathsep.join(str(path) for path in paths) + os.pathsep + env.get("PATH", "")
            )
        env["PYTHONPATH"] = str(root / "src")
        return root, python, env

    def start(self) -> str:
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        try:
            old_url = str(json.loads(self.state_path.read_text())["url"])
        except (OSError, ValueError, KeyError, TypeError):
            old_url = "http://127.0.0.1:8765"
        if matching_service(old_url, self.runs_dir):
            self.url, self.reused = old_url, True
            return self.url
        self.lock.acquire()
        try:
            self.log_path.touch()
            root, python, env = self._runtime(child_environment())
            self.progress("Starting the local interface…")
            self.url = f"http://127.0.0.1:{free_port()}"
            env["PHMAP_DESKTOP_TOKEN"] = self.token
            self._spawn(
                [
                    str(python),
                    "-m",
                    "phmap",
                    "gui",
                    "--no-browser",
                    "--runs-dir",
                    str(self.runs_dir),
                    "--port",
                    self.url.rsplit(":", 1)[1],
                ],
                env,
                root,
            )
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline and not self._cancelled.is_set():
                assert self.process is not None
                if self.process.poll() is not None:
                    raise ConfigurationError(f"The interface could not start. See {self.log_path}")
                if matching_service(self.url, self.runs_dir):
                    self.state_path.write_text(json.dumps({"url": self.url}) + "\n")
                    return self.url
                self._cancelled.wait(0.2)
            raise ConfigurationError(f"The interface did not become ready. See {self.log_path}")
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        self._cancelled.set()
        process = self.process
        try:
            if process is not None and process.poll() is None:
                if self.url:
                    with suppress(OSError, ValueError):
                        _request(f"{self.url}/api/desktop/shutdown", token=self.token, timeout=3)
                    with suppress(subprocess.TimeoutExpired):
                        process.wait(timeout=12)
                if process.poll() is None:
                    terminate_tree(process, force=True)
                    process.wait(timeout=5)
        finally:
            self.lock.release()


def _show_path(path: Path | str) -> None:
    env = child_environment()
    if sys.platform == "win32":
        os.startfile(path)
    else:
        subprocess.Popen(
            ["open" if sys.platform == "darwin" else "xdg-open", str(path)],
            env=env,
            **hidden_process_options(),
        )


def _window(service: DesktopService, *, smoke_test: bool = False) -> int:
    import tkinter as tk
    from tkinter import messagebox, ttk

    root = tk.Tk()
    root.title("pHmap")
    root.geometry("600x370")
    root.minsize(600, 370)
    frame = ttk.Frame(root, padding=24)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="pHmap", font=("Helvetica", 26, "bold")).pack(anchor="w")
    ttk.Label(frame, text="Local protein electrostatic maps").pack(anchor="w", pady=(2, 20))
    status = tk.StringVar(value="Starting pHmap…")
    ttk.Label(frame, textvariable=status, wraplength=552).pack(anchor="w")
    progress = ttk.Progressbar(frame, mode="indeterminate")
    progress.pack(fill="x", pady=12)
    progress.start()
    events: queue.Queue[tuple[str, str]] = queue.Queue()
    service.progress = lambda message: events.put(("status", message))
    buttons = ttk.Frame(frame)
    buttons.pack(fill="x", pady=(8, 4))
    browser = ttk.Button(
        buttons,
        text="Open interface",
        state="disabled",
        command=lambda: _show_path(service.url),
    )
    browser.pack(side="left")
    ttk.Button(buttons, text="Show results", command=lambda: _show_path(service.runs_dir)).pack(
        side="left", padx=8
    )
    ttk.Button(buttons, text="View log", command=lambda: _show_path(service.log_path)).pack(
        side="left"
    )
    footer = ttk.Label(
        frame,
        text="Files stay on your computer. Quit stops unfinished calculations.",
        font=("Helvetica", 10),
    )
    footer.pack(anchor="w", pady=(16, 0))

    def start() -> None:
        try:
            events.put(("ready", service.start()))
        except Exception as exc:
            events.put(("error", str(exc)))

    closing = False
    result = 0

    def close() -> None:
        nonlocal closing
        if closing:
            return
        if (
            service.process is not None
            and service.process.poll() is None
            and not smoke_test
            and not messagebox.askokcancel(
                "Quit pHmap?",
                "Unfinished calculations or setup will stop. Saved results are kept.",
            )
        ):
            return
        closing = True
        browser.configure(state="disabled")
        status.set("Stopping pHmap…")
        progress.start()

        def stop() -> None:
            try:
                service.close()
            finally:
                events.put(("closed", ""))

        threading.Thread(target=stop, daemon=True).start()

    ttk.Button(buttons, text="Quit", command=close).pack(side="right")
    root.protocol("WM_DELETE_WINDOW", close)
    if sys.platform == "darwin":
        root.createcommand("::tk::mac::Quit", close)
    root.bind("<Command-q>" if sys.platform == "darwin" else "<Control-q>", lambda event: close())

    def poll() -> None:
        nonlocal result
        while not events.empty():
            kind, text = events.get_nowait()
            if kind == "closed":
                root.destroy()
                return
            if closing:
                continue
            status.set(text)
            if kind == "ready":
                progress.stop()
                browser.configure(state="normal")
                if not smoke_test:
                    _show_path(text)
                status.set(f"Running locally at {text}")
                if smoke_test:
                    root.update_idletasks()
                    visible = frame.winfo_y() + footer.winfo_y() + footer.winfo_height()
                    result = 0 if visible <= root.winfo_height() else 1
                    (service.runs_dir / ".gui" / "window-smoke.json").write_text(
                        json.dumps(
                            {
                                "ready": result == 0,
                                "tk_version": tk.TkVersion,
                                "height": root.winfo_height(),
                                "content_bottom": visible,
                            }
                        )
                    )
                    root.after(500, close)
                if service.reused:
                    root.destroy()
                    return
            elif kind == "error":
                progress.stop()
                if smoke_test:
                    result = 1
                    close()
                else:
                    messagebox.showerror("pHmap could not start", text)
        if (
            not closing
            and service.url
            and not service.reused
            and service.process is not None
            and service.process.poll() is not None
        ):
            status.set("The local service stopped. View the log for details, then reopen pHmap.")
            browser.configure(state="disabled")
        root.after(200, poll)

    threading.Thread(target=start, daemon=True).start()
    root.after(200, poll)
    try:
        root.mainloop()
    finally:
        service.close()
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="pHmap desktop launcher")
    parser.add_argument(
        "--project", type=Path, help="use an existing checkout and its Pixi environment"
    )
    parser.add_argument("--data-dir", type=Path, help="override persistent app storage")
    parser.add_argument(
        "--smoke-test", action="store_true", help="test startup and shutdown without Tk"
    )
    parser.add_argument("--window-smoke-test", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    project = args.project
    local = resources() / "local-project.json"
    if project is None and local.is_file():
        project = Path(json.loads(local.read_text())["project"])
    service = DesktopService(project=project, data_dir=args.data_dir)
    if args.smoke_test:
        try:
            url = service.start()
            if not _request(f"{url}/api/doctor", timeout=60)["ready"]:
                raise ConfigurationError("The packaged scientific environment failed its preflight")
            # Also verify packaged HTML/static assets, not just a health endpoint.
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            for path in ("/", "/static/app.js", "/static/app.css"):
                with opener.open(url + path, timeout=5) as response:
                    if not response.read():
                        raise ConfigurationError(f"Empty GUI asset: {path}")
            print(json.dumps({"ready": True, "url": url, "runs_dir": str(service.runs_dir)}))
            return 0
        finally:
            service.close()
    return _window(service, smoke_test=args.window_smoke_test)


if __name__ == "__main__":
    raise SystemExit(main())
