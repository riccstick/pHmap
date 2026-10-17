from pathlib import Path

import pytest

from phmap.configuration import WorkflowOptions
from phmap.errors import ConfigurationError
from phmap.gui.app import RunSettings
from phmap.workflow import WorkflowOptions as LegacyWorkflowOptions


@pytest.mark.parametrize("settings", [
    {"size": 64.5}, {"dpi": 300.5}, {"size": True}, {"timeout": float("nan")},
    {"potential_level": float("inf")}, {"ramp_colors": ("red", "white")},
    {"ramp_colors": ("unknown-color!", "white", "blue")},
    {"ramp_colors": ("notacolor", "white", "blue")},
    {"background": ""}, {"background": "white; quit"}, {"foreground": "notacolor"},
    {"force_field": "PARSE; quit"},
])
def test_invalid_settings_are_rejected_at_construction(settings: dict) -> None:
    with pytest.raises(ConfigurationError):
        WorkflowOptions(**settings)


def test_settings_have_one_core_validation_boundary(tmp_path: Path) -> None:
    assert LegacyWorkflowOptions is WorkflowOptions
    settings = RunSettings(size=64, background=None, ray_trace=False)
    options = settings.options(tmp_path)
    assert options == WorkflowOptions(runs_dir=tmp_path, size=64, ray_trace=False)
    render = options.render_options(view=tuple(float(n) for n in range(18)))
    assert render.width == render.height == 64
    assert render.transparent_background
    assert render.background == "white"
    assert render.view == tuple(float(n) for n in range(18))
    assert not render.ray_trace
    assert options.composition_settings().background is None


@pytest.mark.parametrize("settings", [
    {"ramp_colors": ("notacolor", "white", "blue")}, {"foreground": "notacolor"},
    {"background": ""},
])
def test_browser_settings_use_core_presentation_validation(
    tmp_path: Path, settings: dict,
) -> None:
    with pytest.raises(ConfigurationError):
        RunSettings(**settings).options(tmp_path)
