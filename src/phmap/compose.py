"""Deterministic Python-native composition of rendered surface tiles."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFont

from phmap.errors import ArtifactError, ConfigurationError

MAX_FONT_SIZE = 256
MAX_SPACING = 1024
MAX_COMPOSITION_PIXELS = 100_000_000


def format_ph_labels(values: Sequence[Decimal], precision: int | None) -> tuple[str, ...]:
    """Format presentation labels without changing the scientific pH values."""

    if precision is not None and (
        isinstance(precision, bool) or not isinstance(precision, int) or not 0 <= precision <= 6
    ):
        raise ConfigurationError("pH label precision must be an integer from 0 to 6 or omitted")
    with localcontext() as context:
        context.rounding = ROUND_HALF_EVEN
        labels = tuple(str(value) if precision is None else f"{value:.{precision}f}"
                       for value in values)
    if len(set(labels)) != len(labels):
        raise ConfigurationError(
            "pH label precision would give multiple columns the same label; "
            "choose more decimal places or preserve input precision"
        )
    return labels


@dataclass(frozen=True, slots=True)
class RenderedTile:
    """One rendered protein/pH cell."""

    protein_id: str
    ph: Decimal
    path: Path


@dataclass(frozen=True, slots=True)
class CompositionSettings:
    """Presentation settings for the Python compositor."""

    background: str | None = None
    foreground: str = "black"
    column_gap: int = 16
    row_gap: int = 16
    outer_margin: int = 24
    label_gap: int = 16
    font_size: int = 22
    show_colorbar: bool = True
    potential_level: float = 5.0
    ramp_colors: tuple[str, str, str] = ("red", "white", "blue")
    ph_label_precision: int | None = None
    dpi: int = 300

    def __post_init__(self) -> None:
        for name in ("column_gap", "row_gap", "outer_margin", "label_gap"):
            value = getattr(self, name)
            if (
                isinstance(value, bool) or not isinstance(value, int)
                or not 0 <= value <= MAX_SPACING
            ):
                raise ConfigurationError(f"Composition {name} must be an integer from 0 to 1024")
        if (
            isinstance(self.font_size, bool) or not isinstance(self.font_size, int)
            or not 1 <= self.font_size <= MAX_FONT_SIZE
        ):
            raise ConfigurationError("Composition font size must be an integer from 1 to 256")
        if not isinstance(self.show_colorbar, bool):
            raise ConfigurationError("Legend visibility must be true or false")
        if (
            isinstance(self.dpi, bool) or not isinstance(self.dpi, int)
            or not 1 <= self.dpi <= 4800
        ):
            raise ConfigurationError("Composition DPI must be an integer from 1 to 4800")
        format_ph_labels((), self.ph_label_precision)
        if (
            isinstance(self.potential_level, bool) or not math.isfinite(self.potential_level)
            or self.potential_level <= 0
        ):
            raise ConfigurationError("Potential level must be finite and positive")
        if len(self.ramp_colors) != 3:
            raise ConfigurationError("The legend requires exactly three ramp colors")
        for color in (*self.ramp_colors, self.foreground):
            _rgba(color)
        if self.background is not None:
            _rgba(self.background)


def _rgba(color: str) -> tuple[int, int, int, int]:
    if not isinstance(color, str):
        raise ConfigurationError("Composition colors must be color names or strings")
    try:
        components = tuple(int(component) for component in ImageColor.getrgb(color))
    except ValueError as exc:
        raise ConfigurationError(f"Unknown color: {color}") from exc
    if len(components) == 4:
        return (components[0], components[1], components[2], components[3])
    if len(components) == 3:
        return (components[0], components[1], components[2], 255)
    raise ConfigurationError(f"Unsupported color representation: {color}")


def _font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    # Pillow >= 11 is required; use its bundled font, never a system font path.
    return ImageFont.load_default(size=size)


def _text_size(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont | ImageFont.FreeTypeFont,
) -> tuple[int, int]:
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    return round(right - left), round(bottom - top)


def _interpolate_color(
    left: tuple[int, int, int, int],
    right: tuple[int, int, int, int],
    fraction: float,
) -> tuple[int, int, int, int]:
    return tuple(round(a + (b - a) * fraction) for a, b in zip(left, right, strict=True))  # type: ignore[return-value]


def _draw_text(
    draw: ImageDraw.ImageDraw, position: tuple[int, int], text: str,
    font: ImageFont.ImageFont | ImageFont.FreeTypeFont, color: str,
) -> None:
    """Position the ink bounding box, not the font's ascender anchor."""

    left, top, _, _ = draw.textbbox((0, 0), text, font=font)
    draw.text((position[0] - left, position[1] - top), text, font=font, fill=_rgba(color))


def _legend_labels(settings: CompositionSettings) -> tuple[str, str, str]:
    level = f"{settings.potential_level:g}"
    return f"-{level}", "0", f"+{level}"


def _draw_colorbar(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    settings: CompositionSettings,
    font: ImageFont.ImageFont | ImageFont.FreeTypeFont,
) -> None:
    left, top, right, bottom = box
    width = right - left + 1
    negative, neutral, positive = (_rgba(color) for color in settings.ramp_colors)
    for offset in range(width):
        unit = offset / max(width - 1, 1)
        if unit <= 0.5:
            color = _interpolate_color(negative, neutral, unit * 2)
        else:
            color = _interpolate_color(neutral, positive, (unit - 0.5) * 2)
        draw.line((left + offset, top, left + offset, bottom), fill=color)
    draw.rectangle(box, outline=_rgba(settings.foreground), width=1)

    labels = _legend_labels(settings)
    anchors = (left, left + width // 2, right)
    for index, (label, anchor) in enumerate(zip(labels, anchors, strict=True)):
        text_width, _ = _text_size(draw, label, font)
        if index == 0:
            x = anchor
        elif index == 1:
            x = anchor - text_width // 2
        else:
            x = anchor - text_width
        _draw_text(draw, (x, bottom + 4), label, font, settings.foreground)


def compose_grid(
    *,
    protein_order: Sequence[tuple[str, str]],
    ph_order: Sequence[Decimal],
    tiles: Sequence[RenderedTile],
    output_path: Path,
    settings: CompositionSettings | None = None,
) -> Path:
    """Compose a complete, explicitly ordered tile matrix.

    ``protein_order`` contains ``(protein_id, display_label)`` pairs. Every
    protein/pH combination must have exactly one tile; no filesystem globbing or
    timestamp-based discovery is performed.
    """

    settings = settings or CompositionSettings()
    if not protein_order:
        raise ConfigurationError("At least one protein is required for composition")
    if not ph_order:
        raise ConfigurationError("At least one pH value is required for composition")

    tile_map: dict[tuple[str, Decimal], Path] = {}
    for tile in tiles:
        key = (tile.protein_id, tile.ph)
        if key in tile_map:
            raise ArtifactError(f"Duplicate rendered tile for {tile.protein_id} at pH {tile.ph}")
        if not tile.path.is_file() or tile.path.stat().st_size == 0:
            raise ArtifactError(f"Rendered tile is missing or empty: {tile.path}")
        tile_map[key] = tile.path

    expected = {(protein_id, ph) for protein_id, _ in protein_order for ph in ph_order}
    missing = expected - set(tile_map)
    extra = set(tile_map) - expected
    if missing:
        details = ", ".join(f"{protein}@{ph}" for protein, ph in sorted(missing))
        raise ArtifactError(f"Missing rendered tiles: {details}")
    if extra:
        details = ", ".join(f"{protein}@{ph}" for protein, ph in sorted(extra))
        raise ArtifactError(f"Unexpected rendered tiles: {details}")

    ph_labels = format_ph_labels(ph_order, settings.ph_label_precision)
    dimensions = set()
    for path in tile_map.values():
        try:
            with Image.open(path) as image:
                if image.format != "PNG":
                    raise ArtifactError(f"Rendered tile is not a PNG: {path}")
                dimensions.add(image.size)
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            raise ArtifactError(f"Could not read rendered tile {path}: {exc}") from exc
    if len(dimensions) != 1:
        raise ArtifactError("All rendered tiles must have identical dimensions")
    tile_width, tile_height = dimensions.pop()
    font = _font(settings.font_size)
    with Image.new("RGBA", (1, 1)) as scratch:
        scratch_draw = ImageDraw.Draw(scratch)
        row_text = [_text_size(scratch_draw, label, font) for _, label in protein_order]
        ph_text = [_text_size(scratch_draw, label, font) for label in ph_labels]
        legend_text = [_text_size(scratch_draw, label, font) for label in _legend_labels(settings)]
    label_width = max(width for width, _ in row_text)
    # Grow cells to fit text, but never rescale the scientific render tiles.
    cell_width = max(tile_width, max(width for width, _ in ph_text))
    cell_height = max(tile_height, max(height for _, height in row_text))
    grid_width = len(ph_order) * cell_width + (len(ph_order) - 1) * settings.column_gap
    grid_height = len(protein_order) * cell_height + (len(protein_order) - 1) * settings.row_gap
    bar_width = 0
    legend_height = 0
    if settings.show_colorbar:
        bar_width = max(
            min(360, max(160, grid_width // 2)),
            2 * max(legend_text[0][0], legend_text[2][0])
            + legend_text[1][0] + 2 * settings.label_gap,
        )
        legend_height = settings.label_gap + 16 + 4 + max(height for _, height in legend_text)
    content_width = max(grid_width, bar_width)
    left_margin = settings.outer_margin + label_width + settings.label_gap
    grid_left = left_margin + (content_width - grid_width) // 2
    labels_y = settings.outer_margin + grid_height + settings.label_gap
    ph_height = max(height for _, height in ph_text)
    canvas_width = left_margin + content_width + settings.outer_margin
    canvas_height = labels_y + ph_height + legend_height + settings.outer_margin
    if canvas_width * canvas_height > MAX_COMPOSITION_PIXELS:
        raise ConfigurationError(
            "Final figure exceeds 100 million pixels; reduce tile size, spacing, "
            "font size, or the number of calculation cells"
        )
    background = (0, 0, 0, 0) if settings.background is None else _rgba(settings.background)
    with Image.new("RGBA", (canvas_width, canvas_height), background) as canvas:
        draw = ImageDraw.Draw(canvas)
        for row, (protein_id, label) in enumerate(protein_order):
            y = settings.outer_margin + row * (cell_height + settings.row_gap)
            text_width, text_height = row_text[row]
            _draw_text(
                draw, (grid_left - settings.label_gap - text_width,
                       y + (cell_height - text_height) // 2), label, font, settings.foreground,
            )
            for column, ph in enumerate(ph_order):
                x = grid_left + column * (cell_width + settings.column_gap)
                path = tile_map[(protein_id, ph)]
                try:
                    with Image.open(path) as image, image.convert("RGBA") as tile_image:
                        canvas.alpha_composite(tile_image, (
                            x + (cell_width - tile_width) // 2,
                            y + (cell_height - tile_height) // 2,
                        ))
                except (OSError, ValueError, Image.DecompressionBombError) as exc:
                    raise ArtifactError(f"Could not read rendered tile {path}: {exc}") from exc

        for column, label in enumerate(ph_labels):
            x = grid_left + column * (cell_width + settings.column_gap)
            _draw_text(draw, (x + (cell_width - ph_text[column][0]) // 2, labels_y),
                       label, font, settings.foreground)

        if settings.show_colorbar:
            bar_left = left_margin + (content_width - bar_width) // 2
            bar_top = labels_y + ph_height + settings.label_gap
            _draw_colorbar(draw, (bar_left, bar_top, bar_left + bar_width - 1, bar_top + 16),
                           settings, font)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(output_path, format="PNG", dpi=(settings.dpi, settings.dpi))

    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise ArtifactError(f"Final image was not created: {output_path}")
    return output_path
