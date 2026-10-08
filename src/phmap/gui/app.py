"""Loopback-only HTTP interface with local uploads, progress, and run artifacts."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import socket
import tempfile
import threading
import webbrowser
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.datastructures import UploadFile
from starlette.middleware.trustedhost import TrustedHostMiddleware

from phmap import __version__
from phmap.backends import PyMOLRenderOptions
from phmap.compose import CompositionSettings
from phmap.doctor import run_doctor
from phmap.errors import ConfigurationError
from phmap.inputs import validate_run_inputs
from phmap.models import InputValidationError, RunInputs
from phmap.workflow import WorkflowOptions

from .jobs import TERMINAL, JobManager

_ASSETS = Path(__file__).parent
_MAX_UPLOAD = 64 * 1024 * 1024


class RunSettings(BaseModel):
    """Browser inputs have the same validation boundary as command-line inputs."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    name: str = Field(default="", max_length=120)
    ph_mode: Literal["range", "explicit"] = "range"
    ph_start: str = "3"
    ph_stop: str = "8"
    ph_step: str = "1"
    ph_values: str = ""
    labels: list[str] = Field(default_factory=list, max_length=32)
    size: int = Field(default=500, ge=64, le=4096)
    dpi: int = Field(default=300, ge=1, le=4800)
    level: float = Field(default=5.0, gt=0)
    background: str | None = "white"
    foreground: str = "black"
    ramp_colors: tuple[str, str, str] = ("red", "white", "blue")
    force_field: Literal["PARSE", "AMBER", "CHARMM"] = "PARSE"
    ray_trace: bool = True

    def options(self, runs_dir: Path) -> WorkflowOptions:
        # Validate presentation settings before queuing an expensive calculation.
        CompositionSettings(
            background=self.background, foreground=self.foreground,
            ramp_colors=self.ramp_colors, potential_level=self.level,
        )
        PyMOLRenderOptions(
            width=self.size, height=self.size, dpi=self.dpi, level=self.level,
            background=self.background or "white", ramp_colors=self.ramp_colors,
        )
        return WorkflowOptions(
            runs_dir=runs_dir, size=self.size, dpi=self.dpi, potential_level=self.level,
            ramp_colors=self.ramp_colors, background=self.background,
            foreground=self.foreground, force_field=self.force_field, ray_trace=self.ray_trace,
        )


def _summary(inputs: RunInputs) -> dict[str, Any]:
    return {
        "proteins": [{"id": p.protein_id, "label": p.label} for p in inputs.proteins],
        "ph_values": [str(value) for value in inputs.ph_values],
        "total": len(inputs.proteins) * len(inputs.ph_values),
    }


async def _save_upload(upload: UploadFile, parent: Path, *, suffix: str) -> Path:
    filename = Path((upload.filename or "").replace("\\", "/")).name
    if not filename or Path(filename).suffix.lower() != suffix:
        raise InputValidationError(f"Choose a {suffix} file; received {filename!r}")
    if not filename.isprintable() or filename.startswith("."):
        raise InputValidationError("File names must be printable and must not start with '.'")
    parent.mkdir(parents=True, exist_ok=True)
    path = parent / filename
    size = 0
    with path.open("xb") as handle:
        while chunk := await upload.read(1024 * 1024):
            size += len(chunk)
            if size > _MAX_UPLOAD:
                raise InputValidationError("Each input file must be smaller than 64 MiB")
            handle.write(chunk)
    if size == 0:
        raise InputValidationError(f"Input file is empty: {filename}")
    return path


def create_app(
    runs_dir: Path = Path("runs"), *, manager: JobManager | None = None,
    desktop_token: str = "",
) -> FastAPI:
    """Construct the application without starting a server or worker on import."""

    jobs = manager or JobManager(runs_dir)
    token = secrets.token_urlsafe(32)
    templates = Jinja2Templates(directory=str(_ASSETS / "templates"))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        jobs.start()
        try:
            yield
        finally:
            await asyncio.to_thread(jobs.close)

    app = FastAPI(title="pHmap local", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.jobs = jobs
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

    @app.middleware("http")
    async def local_boundary(request: Request, call_next: Any) -> Response:
        origin = request.headers.get("origin")
        expected_origin = f"{request.url.scheme}://{request.url.netloc}"
        if origin and origin != expected_origin:
            return JSONResponse({"detail": "Requests must come from this local interface"}, 403)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            provided = request.headers.get("x-phmap-token", "")
            desktop_shutdown = (
                request.url.path == "/api/desktop/shutdown" and bool(desktop_token)
                and secrets.compare_digest(
                    request.headers.get("x-phmap-desktop-token", ""), desktop_token
                )
            )
            if not desktop_shutdown and not secrets.compare_digest(provided, token):
                return JSONResponse({"detail": "Refresh the interface before submitting"}, 403)
            try:
                length = int(request.headers.get("content-length", "0"))
            except ValueError:
                return JSONResponse({"detail": "Invalid request size"}, 400)
            if length > _MAX_UPLOAD:
                return JSONResponse({"detail": "Total upload must be smaller than 64 MiB"}, 413)
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self'; connect-src 'self'; frame-ancestors 'none'; form-action 'self'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.mount("/static", StaticFiles(directory=_ASSETS / "static"), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> Response:
        return templates.TemplateResponse(
            request=request, name="index.html",
            context={"token": token, "version": __version__, "runs_dir": str(jobs.runs_dir)},
        )

    @app.get("/api/doctor")
    def doctor() -> dict[str, Any]:
        results = run_doctor(jobs.runs_dir)
        return {"ready": all(r.ok for r in results), "checks": [r.as_dict() for r in results]}

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"application": "phmap", "version": __version__, "runs_dir": str(jobs.runs_dir)}

    @app.post("/api/desktop/shutdown")
    async def desktop_shutdown(request: Request) -> dict[str, bool]:
        if not desktop_token or not secrets.compare_digest(
            request.headers.get("x-phmap-desktop-token", ""), desktop_token
        ):
            raise HTTPException(403, "Only the owning desktop launcher can stop this service")
        shutdown = getattr(app.state, "desktop_shutdown", None)
        if shutdown is None:
            raise HTTPException(409, "This service was not started by the desktop launcher")
        shutdown()
        return {"stopping": True}

    @app.get("/api/runs")
    def history() -> list[dict[str, Any]]:
        return jobs.history()

    async def receive(request: Request, *, validate_only: bool) -> dict[str, Any]:
        try:
            async with request.form(max_files=34, max_fields=4, max_part_size=_MAX_UPLOAD) as form:
                settings = RunSettings.model_validate_json(str(form.get("settings", "{}")))
                uploads = form.getlist("proteins")
                if not 1 <= len(uploads) <= 32 or not all(
                    isinstance(upload, UploadFile) for upload in uploads
                ):
                    raise InputValidationError("Choose between 1 and 32 PDB files")
                with tempfile.TemporaryDirectory(
                    prefix="selection-", dir=jobs.uploads
                ) as temporary:
                    root = Path(temporary)
                    paths = []
                    for index, upload in enumerate(uploads):
                        assert isinstance(upload, UploadFile)
                        paths.append(await _save_upload(upload, root / str(index), suffix=".pdb"))
                    view = form.get("view")
                    view_path = None
                    if isinstance(view, UploadFile) and view.filename:
                        view_path = await _save_upload(view, root / "camera", suffix=".txt")
                    ligand = form.get("ligand")
                    ligand_path = None
                    if isinstance(ligand, UploadFile) and ligand.filename:
                        ligand_path = await _save_upload(ligand, root / "ligand", suffix=".mol2")
                    if sum(path.stat().st_size for path in root.rglob("*") if path.is_file()) > (
                        _MAX_UPLOAD
                    ):
                        raise InputValidationError("Total upload must be smaller than 64 MiB")
                    inputs = validate_run_inputs(
                        paths, labels=settings.labels or None, view_file=view_path,
                        ligand=ligand_path,
                        ph_values=settings.ph_values if settings.ph_mode == "explicit" else None,
                        ph_range=(settings.ph_start, settings.ph_stop, settings.ph_step)
                        if settings.ph_mode == "range" else None,
                    )
                    if len(inputs.ph_values) * len(inputs.proteins) > 512:
                        raise InputValidationError("Use at most 512 calculation cells per GUI run")
                    options = settings.options(jobs.runs_dir)
                    summary = _summary(inputs)
                    if validate_only:
                        return {"valid": True, **summary}
                    run_id = jobs.submit(inputs, options, name=settings.name)
                    return {"run_id": run_id, **summary}
        except ValidationError as exc:
            messages = "; ".join(
                f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
                for error in exc.errors()
            )
            raise HTTPException(422, messages) from exc
        except (InputValidationError, ConfigurationError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/validate")
    async def validate(request: Request) -> dict[str, Any]:
        return await receive(request, validate_only=True)

    @app.post("/api/runs", status_code=202)
    async def submit(request: Request) -> dict[str, Any]:
        return await receive(request, validate_only=False)

    @app.get("/api/runs/{run_id}")
    def detail(run_id: str) -> dict[str, Any]:
        try:
            return jobs.snapshot(run_id)
        except (FileNotFoundError, KeyError, TypeError) as exc:
            raise HTTPException(404, "Run not found or its manifest is unreadable") from exc

    @app.post("/api/runs/{run_id}/cancel")
    def cancel(run_id: str) -> dict[str, Any]:
        detail(run_id)
        try:
            jobs.cancel(run_id)
        except ConfigurationError as exc:
            raise HTTPException(409, str(exc)) from exc
        return jobs.snapshot(run_id)

    @app.get("/api/runs/{run_id}/events")
    async def events(run_id: str, request: Request) -> StreamingResponse:
        detail(run_id)

        async def updates() -> AsyncIterator[str]:
            previous = ""
            while not await request.is_disconnected():
                data = jobs.snapshot(run_id)
                payload = json.dumps(data, separators=(",", ":"))
                if payload != previous:
                    yield f"event: progress\ndata: {payload}\n\n"
                    previous = payload
                else:
                    yield ": heartbeat\n\n"
                if data["status"] in TERMINAL:
                    return
                await asyncio.sleep(0.75)

        return StreamingResponse(
            updates(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    def artifact(run_id: str, relative_path: str) -> Path:
        try:
            root = jobs.run_directory(run_id)
            path = (root / relative_path).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise FileNotFoundError
            if path.suffix.lower() not in {
                ".png", ".json", ".log", ".pqr", ".dx", ".in", ".pml", ".pdb", ".mol2"
            }:
                raise FileNotFoundError
            return path
        except FileNotFoundError as exc:
            raise HTTPException(404, "Artifact not found") from exc

    @app.get("/api/runs/{run_id}/files/{relative_path:path}")
    def file(run_id: str, relative_path: str, download: bool = False) -> FileResponse:
        path = artifact(run_id, relative_path)
        return FileResponse(path, filename=path.name if download else None)

    @app.get("/api/runs/{run_id}/log/{relative_path:path}")
    def log(run_id: str, relative_path: str) -> dict[str, str]:
        path = artifact(run_id, relative_path)
        if path.suffix != ".log":
            raise HTTPException(404, "Log not found")
        return {"text": jobs.log_tail(path)}

    return app


class _LocalServer(uvicorn.Server):
    """Stop calculations before waiting for live progress connections to end."""

    def __init__(self, config: uvicorn.Config, jobs: JobManager) -> None:
        super().__init__(config)
        self.jobs = jobs

    async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
        await asyncio.to_thread(self.jobs.close)
        await super().shutdown(sockets=sockets)


def serve(*, runs_dir: Path, port: int = 8765, open_browser: bool = True) -> None:
    """Start one loopback server; opening a window never starts calculations."""

    if not 1 <= port <= 65535:
        raise ConfigurationError("GUI port must be between 1 and 65535")
    # Reserve the socket before opening a browser, so a busy port is a clean
    # error instead of accidentally opening another application's page.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind(("127.0.0.1", port))
            listener.listen(128)
        except OSError as exc:
            raise ConfigurationError(f"Cannot start GUI on port {port}: {exc}") from exc
        app = create_app(runs_dir, desktop_token=os.environ.pop("PHMAP_DESKTOP_TOKEN", ""))
        url = f"http://127.0.0.1:{port}"
        print(f"pHmap GUI: {url}\nRun directory: {runs_dir.expanduser().resolve()}", flush=True)
        timer = None
        if open_browser:
            timer = threading.Timer(1.0, webbrowser.open, args=(url,))
            timer.daemon = True
            timer.start()
        try:
            # An open SSE stream must not postpone lifespan cleanup (and job
            # cancellation) indefinitely when the user presses Ctrl+C.
            server = _LocalServer(
                uvicorn.Config(app, log_level="info", timeout_graceful_shutdown=3),
                app.state.jobs,
            )
            app.state.desktop_shutdown = lambda: setattr(server, "should_exit", True)
            server.run(sockets=[listener])
        finally:
            if timer is not None:
                timer.cancel()
