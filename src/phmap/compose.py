"""Deterministic Python-native composition of rendered surface tiles."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFont

from phmap.errors import ArtifactError, ConfigurationError


@dataclass(frozen=True, slots=True)
class RenderedTile:
    """One rendered protein/pH cell."""

    protein_id: str
    ph: Decimal
    path: Path


@dataclass(frozen=True, slots=True)
class CompositionSettings:
    """Presentation settings for the first Python compositor."""

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

    def __post_init__(self) -> None:
        if self.column_gap < 0 or self.row_gap < 0 or self.outer_margin < 0:
            raise ConfigurationError("Composition gaps and margins cannot be negative")
        if self.font_size <= 0:
            raise ConfigurationError("Composition font size must be positive")
        if self.potential_level <= 0:
            raise ConfigurationError("Potential level must be positive")
        for color in (*self.ramp_colors, self.foreground):
            _rgba(color)
        if self.background is not None:
            _rgba(self.background)


def _rgba(color: str) -> tuple[int, int, int, int]:
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
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # pragma: no cover - compatibility with old Pillow
        return ImageFont.load_default()


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


def _draw_colorbar(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    settings: CompositionSettings,
    font: ImageFont.ImageFont | ImageFont.FreeTypeFont,
) -> None:
    left, top, right, bottom = box
    width = right - left
    negative, neutral, positive = (_rgba(color) for color in settings.ramp_colors)
    for offset in range(width):
        unit = offset / max(width - 1, 1)
        if unit <= 0.5:
            color = _interpolate_color(negative, neutral, unit * 2)
        else:
            color = _interpolate_color(neutral, positive, (unit - 0.5) * 2)
        draw.line((left + offset, top, left + offset, bottom), fill=color)
    draw.rectangle(box, outline=_rgba(settings.foreground), width=1)

    level = f"{settings.potential_level:g}"
    labels = (f"-{level}", "0", f"+{level}")
    anchors = (left, left + width // 2, right)
    for index, (label, anchor) in enumerate(zip(labels, anchors, strict=True)):
        text_width, _ = _text_size(draw, label, font)
        if index == 0:
            x = anchor
        elif index == 1:
            x = anchor - text_width // 2
        else:
            x = anchor - text_width
        draw.text((x, bottom + 4), label, font=font, fill=_rgba(settings.foreground))


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

    opened: list[Image.Image] = []
    try:
        for protein_id, _ in protein_order:
            for ph in ph_order:
                try:
                    opened.append(Image.open(tile_map[(protein_id, ph)]).convert("RGBA"))
                except OSError as exc:
                    raise ArtifactError(
                        f"Could not read rendered tile {tile_map[(protein_id, ph)]}: {exc}"
                    ) from exc
        tile_width, tile_height = opened[0].size
        if tile_width <= 0 or tile_height <= 0:
            raise ArtifactError("Rendered tile dimensions must be positive")
        if any(image.size != (tile_width, tile_height) for image in opened):
            raise ArtifactError("All rendered tiles must have identical dimensions")

        font = _font(settings.font_size)
        scratch = Image.new("RGBA", (1, 1))
        scratch_draw = ImageDraw.Draw(scratch)
        label_width = max(
            _text_size(scratch_draw, label, font)[0] for _, label in protein_order
        )
        left_margin = settings.outer_margin + label_width + settings.label_gap
        grid_width = len(ph_order) * tile_width + (len(ph_order) - 1) * settings.column_gap
        grid_height = (
            len(protein_order) * tile_height
            + (len(protein_order) - 1) * settings.row_gap
        )
        ph_label_height = settings.font_size + settings.label_gap
        colorbar_height = settings.font_size * 2 + 22 if settings.show_colorbar else 0
        canvas_width = left_margin + grid_width + settings.outer_margin
        canvas_height = (
            settings.outer_margin
            + grid_height
            + ph_label_height
            + colorbar_height
            + settings.outer_margin
        )
        background = (0, 0, 0, 0) if settings.background is None else _rgba(settings.background)
        canvas = Image.new("RGBA", (canvas_width, canvas_height), background)
        draw = ImageDraw.Draw(canvas)

        image_index = 0
        for row, (_, label) in enumerate(protein_order):
            y = settings.outer_margin + row * (tile_height + settings.row_gap)
            text_width, text_height = _text_size(draw, label, font)
            label_x = settings.outer_margin + label_width - text_width
            label_y = y + (tile_height - text_height) // 2
            draw.text(
                (label_x, label_y),
                label,
                font=font,
                fill=_rgba(settings.foreground),
            )
            for column, _ in enumerate(ph_order):
                x = left_margin + column * (tile_width + settings.column_gap)
                canvas.alpha_composite(opened[image_index], (x, y))
                image_index += 1

        labels_y = settings.outer_margin + grid_height + settings.label_gap // 2
        for column, ph in enumerate(ph_order):
            label = str(ph)
            text_width, _ = _text_size(draw, label, font)
            tile_x = left_margin + column * (tile_width + settings.column_gap)
            draw.text(
                (tile_x + (tile_width - text_width) // 2, labels_y),
                label,
                font=font,
                fill=_rgba(settings.foreground),
            )

        if settings.show_colorbar:
            bar_width = min(360, max(160, grid_width // 2))
            bar_left = left_margin + (grid_width - bar_width) // 2
            bar_top = labels_y + settings.font_size + settings.label_gap
            _draw_colorbar(
                canvas,
                draw,
                (bar_left, bar_top, bar_left + bar_width, bar_top + 16),
                settings,
                font,
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(output_path, format="PNG")
    finally:
        for image in opened:
            image.close()

    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise ArtifactError(f"Final image was not created: {output_path}")
    return output_path
