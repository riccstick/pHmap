from decimal import ROUND_UP, Decimal, localcontext
from pathlib import Path

import pytest
from PIL import Image

from phmap.compose import CompositionSettings, RenderedTile, compose_grid, format_ph_labels
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


@pytest.mark.parametrize("settings", [
    {"font_size": 0}, {"font_size": 257}, {"font_size": 12.5}, {"font_size": True},
    {"column_gap": -1}, {"row_gap": 1025}, {"outer_margin": 1.5}, {"label_gap": -1},
    {"label_gap": True}, {"show_colorbar": "false"}, {"ph_label_precision": -1},
    {"ph_label_precision": 7}, {"ph_label_precision": 1.5}, {"ph_label_precision": True},
    {"potential_level": float("nan")}, {"potential_level": float("inf")},
    {"foreground": None}, {"ramp_colors": ("red", "blue")},
])
def test_invalid_publication_settings_fail_at_construction(settings: dict) -> None:
    with pytest.raises(ConfigurationError):
        CompositionSettings(**settings)


def test_ph_precision_is_display_only_and_uses_deterministic_half_even_rounding() -> None:
    values = (Decimal("5.125"), Decimal("7.0"))
    assert format_ph_labels(values, None) == ("5.125", "7.0")
    with localcontext() as context:
        context.rounding = ROUND_UP
        assert format_ph_labels(values, 2) == ("5.12", "7.00")
    assert values == (Decimal("5.125"), Decimal("7.0"))
    with pytest.raises(ConfigurationError, match="same label"):
        format_ph_labels((Decimal("5.11"), Decimal("5.14")), 1)


def _color_bounds(
    image: Image.Image, color: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    matches = [(x, y) for y in range(image.height) for x in range(image.width)
               if image.getpixel((x, y)) == color]
    assert matches
    return (min(x for x, _ in matches), min(y for _, y in matches),
            max(x for x, _ in matches), max(y for _, y in matches))


def test_publication_spacing_preserves_tile_pixels_and_both_axis_orders(tmp_path: Path) -> None:
    colors = [(250, 0, 0, 255), (0, 0, 250, 255), (0, 250, 0, 255), (250, 0, 250, 255)]
    tiles = []
    for (protein, ph), color in zip(
        [("wt", "5"), ("wt", "7"), ("mut", "5"), ("mut", "7")], colors, strict=True,
    ):
        path = tmp_path / f"{protein}-{ph}.png"
        _tile(path, color)
        tiles.append(RenderedTile(protein, Decimal(ph), path))
    output = compose_grid(
        protein_order=[("wt", "WT"), ("mut", "Mutant")],
        ph_order=[Decimal("5"), Decimal("7")], tiles=list(reversed(tiles)),
        output_path=tmp_path / "styled.png",
        settings=CompositionSettings(
            font_size=10, outer_margin=5, column_gap=7, row_gap=9, label_gap=3,
            show_colorbar=False,
        ),
    )
    with Image.open(output) as image:
        red, blue, green, pink = [_color_bounds(image, color) for color in colors]
        assert red[1] == blue[1] == 5
        assert blue[0] - red[2] - 1 == pink[0] - green[2] - 1 == 7
        assert green[1] - red[3] - 1 == pink[1] - blue[3] - 1 == 9
        assert all(box[2] - box[0] + 1 == 20 and box[3] - box[1] + 1 == 12
                   for box in (red, blue, green, pink))
        assert image.getpixel((0, 0)) == (0, 0, 0, 0)


def test_large_labels_and_legend_fit_even_with_tiny_tiles_and_zero_margins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import phmap.compose as composer

    placements = []
    original = composer._draw_text

    def record(draw, position, text, font, color):  # type: ignore[no-untyped-def]
        placements.append((position, composer._text_size(draw, text, font), text))
        original(draw, position, text, font, color)

    monkeypatch.setattr(composer, "_draw_text", record)
    tile = tmp_path / "tile.png"
    _tile(tile, (100, 100, 100, 255))
    output = compose_grid(
        protein_order=[("wt", "Wide protein label")], ph_order=[Decimal("7.1234")],
        tiles=[RenderedTile("wt", Decimal("7.1234"), tile)], output_path=tmp_path / "large.png",
        settings=CompositionSettings(
            font_size=96, outer_margin=0, label_gap=0, ph_label_precision=6,
        ),
    )
    with Image.open(output) as image:
        for (x, y), (width, height), _ in placements:
            assert x >= 0 and x + width <= image.width
            assert y >= 0 and y + height <= image.height
    assert [text for _, _, text in placements] == [
        "Wide protein label", "pH", "7.123400", "-5", "0", "+5",
    ]


@pytest.mark.parametrize("font_size", [10, 96])
@pytest.mark.parametrize("background,foreground", [(None, "black"), ("white", "black"),
                                                 ("black", "white")])
@pytest.mark.parametrize("show_colorbar", [False, True])
def test_ph_axis_label_aligns_with_values_in_the_protein_name_column(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, font_size: int,
    background: str | None, foreground: str, show_colorbar: bool,
) -> None:
    import phmap.compose as composer

    placements = []
    original = composer._draw_text

    def record(draw, position, text, font, color):  # type: ignore[no-untyped-def]
        placements.append((position, composer._text_size(draw, text, font), text, color))
        original(draw, position, text, font, color)

    monkeypatch.setattr(composer, "_draw_text", record)
    tile = tmp_path / "tile.png"
    _tile(tile, (100, 100, 100, 255))
    ph_values = [Decimal("0"), Decimal("14")]
    output = compose_grid(
        protein_order=[("first", "I"), ("second", "M")], ph_order=ph_values,
        tiles=[RenderedTile(protein, ph, tile)
               for protein in ("first", "second") for ph in ph_values],
        output_path=tmp_path / "ph-axis.png",
        settings=CompositionSettings(
            font_size=font_size, outer_margin=0, label_gap=0, background=background,
            foreground=foreground, show_colorbar=show_colorbar,
        ),
    )
    rows = placements[:2]
    axis, *values = placements[2:5]
    assert axis[2] == "pH"
    assert [value[2] for value in values] == ["0", "14"]
    assert axis[1][0] > max(size[0] for _, size, _, _ in rows)
    assert all(position[0] + size[0] == axis[0][0] + axis[1][0]
               for position, size, _, _ in rows)
    assert all(value[0][1] == axis[0][1] for value in values)
    assert all(value[0][0] >= axis[0][0] + axis[1][0] for value in values)
    assert all(color == foreground for _, _, _, color in placements)
    with Image.open(output) as image:
        for (x, y), (width, height), _, _ in placements:
            assert x >= 0 and x + width <= image.width
            assert y >= 0 and y + height <= image.height


def test_legend_toggle_and_colors_stay_consistent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import phmap.compose as composer

    tile = tmp_path / "tile.png"
    _tile(tile, (100, 100, 100, 255))
    calls = []
    original = composer._draw_colorbar

    def record(draw, box, settings, font):  # type: ignore[no-untyped-def]
        calls.append((box, settings))
        original(draw, box, settings, font)

    monkeypatch.setattr(composer, "_draw_colorbar", record)
    outputs = []
    for show in (False, True):
        outputs.append(compose_grid(
            protein_order=[("wt", "WT")], ph_order=[Decimal("7")],
            tiles=[RenderedTile("wt", Decimal("7"), tile)],
            output_path=tmp_path / f"legend-{show}.png",
            settings=CompositionSettings(
                show_colorbar=show, ramp_colors=("blue", "white", "red"), potential_level=3,
            ),
        ))
    assert len(calls) == 1
    box, settings = calls[0]
    assert settings.potential_level == 3
    with Image.open(outputs[0]) as hidden, Image.open(outputs[1]) as shown:
        assert shown.height > hidden.height
        x1, y1, x2, y2 = box
        assert 0 <= x1 < x2 < shown.width and 0 <= y1 < y2 < shown.height
        left = shown.getpixel((x1 + (x2 - x1) // 4, y1 + 5))
        right = shown.getpixel((x1 + 3 * (x2 - x1) // 4, y1 + 5))
        assert left[2] > left[0] and right[0] > right[2]


def test_oversized_composition_fails_before_allocating_canvas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("phmap.compose.MAX_COMPOSITION_PIXELS", 10)
    tile = tmp_path / "tile.png"
    _tile(tile, (100, 100, 100, 255))
    with pytest.raises(ConfigurationError, match="million pixels"):
        compose_grid(
            protein_order=[("wt", "WT")], ph_order=[Decimal("7")],
            tiles=[RenderedTile("wt", Decimal("7"), tile)], output_path=tmp_path / "too-big.png",
        )
    assert not (tmp_path / "too-big.png").exists()


@pytest.mark.parametrize("kind", ["dimensions", "not-png", "corrupt"])
def test_incompatible_tiles_are_rejected_without_writing_a_figure(
    tmp_path: Path, kind: str,
) -> None:
    first, second = tmp_path / "first.png", tmp_path / "second.png"
    _tile(first, (100, 100, 100, 255))
    if kind == "dimensions":
        Image.new("RGB", (5, 5)).save(second)
    elif kind == "not-png":
        Image.new("RGB", (20, 12)).save(second, format="JPEG")
    else:
        second.write_bytes(b"not an image")
    with pytest.raises(ArtifactError):
        compose_grid(
            protein_order=[("wt", "WT")], ph_order=[Decimal("5"), Decimal("7")],
            tiles=[RenderedTile("wt", Decimal("5"), first),
                   RenderedTile("wt", Decimal("7"), second)],
            output_path=tmp_path / "invalid.png",
        )
    assert not (tmp_path / "invalid.png").exists()
