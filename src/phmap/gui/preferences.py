"""Validated, atomic per-user defaults; never store calculation inputs or commands."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from phmap.errors import ConfigurationError
from phmap.json_state import write_json
from phmap.locking import LocalLock
from phmap.paths import data_directory

from .schemas import RunSettings

DEFAULT_FIELDS = frozenset({
    "workers", "size", "dpi", "level", "background", "foreground", "ramp_colors",
    "force_field", "ray_trace", "surface_mode", "surface_solvent",
    "surface_ramp_above_mode", "ligand_representation", "font_size", "outer_margin",
    "column_gap", "row_gap", "label_gap", "show_colorbar", "ph_label_precision",
})


def validate_defaults(values: dict[str, Any]) -> dict[str, Any]:
    unknown = values.keys() - DEFAULT_FIELDS
    if unknown:
        raise ConfigurationError("Not a reusable default: " + ", ".join(sorted(unknown)))
    actual = dict(values)
    automatic = actual.get("foreground", "auto") == "auto"
    if automatic:
        actual["foreground"] = "white" if actual.get("background") == "black" else "black"
    settings = RunSettings.model_validate(actual)
    settings.options(Path("runs"))
    normalized = settings.model_dump(mode="json", include=set(DEFAULT_FIELDS))
    if automatic:
        normalized["foreground"] = "auto"
    return normalized


def factory_defaults() -> dict[str, Any]:
    return validate_defaults({})


class PreferencesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    defaults: dict[str, Any] = Field(default_factory=dict)


class PreferencesStore:
    def __init__(self, path: Path | None = None) -> None:
        configured = os.environ.get("PHMAP_PREFERENCES_PATH")
        self.path = (path or (Path(configured) if configured else data_directory()
                             / "preferences.json")).expanduser().absolute()

    def read(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": 1,
            "onboarding_completed": False,
            "defaults": factory_defaults(),
            "path": str(self.path),
            "warning": None,
        }
        try:
            if self.path.is_symlink():
                raise ConfigurationError("Preferences must not be a symbolic link")
            if not self.path.exists():
                return result
            if self.path.stat().st_size > 64 * 1024:
                raise ConfigurationError("Preferences exceed the 64 KiB limit")
            data = json.loads(self.path.read_bytes())
            if (
                not isinstance(data, dict)
                or data.get("schema_version") != 1
                or not isinstance(data.get("onboarding_completed"), bool)
                or not isinstance(data.get("defaults"), dict)
            ):
                raise ConfigurationError("Unknown or damaged preferences format")
            result.update(
                defaults=validate_defaults(data["defaults"]),
                onboarding_completed=data["onboarding_completed"],
            )
        except (OSError, ValueError, TypeError, ConfigurationError) as exc:
            result["warning"] = f"Could not load saved defaults: {exc}. Using built-in defaults."
        return result

    def save(
        self, defaults: dict[str, Any] | None = None, *, reset: bool = False
    ) -> dict[str, Any]:
        normalized = validate_defaults(defaults) if defaults is not None else None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = LocalLock(self.path.with_suffix(self.path.suffix + ".lock"))
        lock.acquire(timeout=2)
        try:
            state = self.read()
            if self.path.is_symlink():
                raise ConfigurationError("Preferences must not be a symbolic link")
            if state["warning"] and not reset:
                raise ConfigurationError("Saved defaults are unreadable; use Reset defaults first")
            write_json(self.path, {
                "schema_version": 1,
                "onboarding_completed": True,
                "defaults": factory_defaults() if reset else normalized or state["defaults"],
            })
            return self.read()
        finally:
            lock.release()
