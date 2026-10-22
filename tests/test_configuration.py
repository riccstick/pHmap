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
    {"surface_mode": -1}, {"surface_mode": 5}, {"surface_mode": 2.0},
    {"surface_mode": True}, {"surface_solvent": "false"}, {"surface_solvent": 1},
    {"surface_ramp_above_mode": "off"}, {"surface_ramp_above_mode": 0},
    {"ligand_representation": "cartoon"}, {"ligand_representation": "sticks; quit"},
    {"ligand_representation": []},
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


def test_advanced_render_defaults_are_unchanged() -> None:
    options = WorkflowOptions()
    render = options.render_options()
    assert render.surface_mode == 0
    assert render.surface_solvent is False
    assert render.surface_ramp_above_mode is True
    assert render.ligand_representation == "sticks"


@pytest.mark.parametrize("representation", ["sticks", "lines", "dots", "spheres", None])
def test_browser_and_workflow_share_advanced_render_settings(
    tmp_path: Path, representation,
) -> None:  # type: ignore[no-untyped-def]
    settings = RunSettings(
        surface_mode=4, surface_solvent=True, surface_ramp_above_mode=False,
        ligand_representation=representation,
    )
    options = settings.options(tmp_path)
    render = options.render_options()
    assert render.surface_mode == options.render_configuration()["surface_mode"] == 4
    assert render.surface_solvent is True
    assert render.surface_ramp_above_mode is False
    assert render.ligand_representation == representation
    assert options.render_configuration()["ligand_representation"] == representation


@pytest.mark.parametrize("settings", [
    {"ramp_colors": ("notacolor", "white", "blue")}, {"foreground": "notacolor"},
    {"background": ""},
])
def test_browser_settings_use_core_presentation_validation(
    tmp_path: Path, settings: dict,
) -> None:
    with pytest.raises(ConfigurationError):
        RunSettings(**settings).options(tmp_path)
