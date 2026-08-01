from decimal import Decimal
from pathlib import Path

import pytest
from PIL import Image

from phmap.compose import CompositionSettings, RenderedTile, compose_grid
from phmap.errors import ArtifactError, ConfigurationError


def _tile(path: Path, color: tuple[int, int, int, int]) -> None:
    Image.new("RGBA", (20, 12), color).save(path)


def test_compose_grid_preserves_explicit_column_order(tmp_path: Path) -> None:
    red = tmp_path / "ph-5.png"
    blue = tmp_path / "ph-7.png"
    _tile(red, (250, 0, 0, 255))
    _tile(blue, (0, 0, 250, 255))
    output = tmp_path / "result.png"

    compose_grid(
        protein_order=[("mutant2", "mutant2")],
        ph_order=[Decimal("5.0"), Decimal("7.0")],
        tiles=[
            RenderedTile("mutant2", Decimal("7.0"), blue),
            RenderedTile("mutant2", Decimal("5.0"), red),
        ],
        output_path=output,
        settings=CompositionSettings(
            outer_margin=4,
            label_gap=4,
            font_size=10,
            show_colorbar=False,
        ),
    )

    with Image.open(output) as image:
        pixels = image.convert("RGBA")
        red_positions = [
            x for x in range(image.width) if pixels.getpixel((x, 5))[:3] == (250, 0, 0)
        ]
        blue_positions = [
            x for x in range(image.width) if pixels.getpixel((x, 5))[:3] == (0, 0, 250)
        ]
        assert red_positions
        assert blue_positions
        assert max(red_positions) < min(blue_positions)


def test_compose_grid_rejects_missing_tile(tmp_path: Path) -> None:
    red = tmp_path / "ph-5.png"
    _tile(red, (250, 0, 0, 255))

    with pytest.raises(ArtifactError, match="Missing rendered tiles"):
        compose_grid(
            protein_order=[("mutant2", "mutant2")],
            ph_order=[Decimal("5.0"), Decimal("7.0")],
            tiles=[RenderedTile("mutant2", Decimal("5.0"), red)],
            output_path=tmp_path / "result.png",
        )


def test_composition_settings_validate_colors() -> None:
    with pytest.raises(ConfigurationError, match="Unknown color"):
        CompositionSettings(background="definitely-not-a-color")
