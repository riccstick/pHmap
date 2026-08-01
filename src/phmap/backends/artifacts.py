"""Validation helpers for files exchanged between scientific tools."""

from __future__ import annotations

from pathlib import Path

from .errors import ArtifactError

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def require_nonempty_file(
    path: str | Path,
    description: str,
    *,
    minimum_size: int = 1,
) -> Path:
    """Return *path* when it is a regular file of at least ``minimum_size``.

    The explicit check produces a stage-specific error instead of allowing the
    next scientific tool to fail with a hard-to-interpret parser error.
    """

    candidate = Path(path)
    try:
        is_file = candidate.is_file()
        size = candidate.stat().st_size if is_file else 0
    except OSError as exc:
        raise ArtifactError(candidate, description, str(exc)) from exc

    if not is_file:
        raise ArtifactError(candidate, description, "file does not exist")
    if size < minimum_size:
        raise ArtifactError(
            candidate,
            description,
            f"file is too small ({size} bytes; expected at least {minimum_size})",
        )
    return candidate


def require_png(path: str | Path, description: str = "PNG") -> Path:
    """Validate that *path* is non-empty and begins with the PNG signature."""

    candidate = require_nonempty_file(
        path, description, minimum_size=len(PNG_SIGNATURE)
    )
    try:
        with candidate.open("rb") as stream:
            signature = stream.read(len(PNG_SIGNATURE))
    except OSError as exc:
        raise ArtifactError(candidate, description, str(exc)) from exc
    if signature != PNG_SIGNATURE:
        raise ArtifactError(candidate, description, "missing PNG file signature")
    return candidate
