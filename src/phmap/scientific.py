"""Lightweight numerical validation of scientific workflow artifacts."""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from phmap.errors import ArtifactError

_GRID_COUNTS = re.compile(
    r"^object\s+1\s+class\s+gridpositions\s+counts\s+(\d+)\s+(\d+)\s+(\d+)\s*$",
    re.IGNORECASE,
)
_DATA_START = re.compile(
    r"^object\s+3\s+class\s+array\b.*\bitems\s+(\d+)\s+data\s+follows\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class PQRSummary:
    atom_count: int
    total_charge: float
    minimum_radius: float
    maximum_radius: float
    zero_radius_count: int

    def as_dict(self) -> dict[str, int | float]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class OpenDXSummary:
    grid_counts: tuple[int, int, int]
    value_count: int
    minimum: float
    maximum: float
    mean: float

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def summarize_pqr(path: Path) -> PQRSummary:
    """Validate PQR atom records and return basic numerical provenance."""

    atom_count = 0
    total_charge = 0.0
    minimum_radius = math.inf
    maximum_radius = -math.inf
    zero_radius_count = 0
    try:
        with path.open("rt", encoding="utf-8", errors="replace") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.startswith(("ATOM", "HETATM")):
                    continue
                fields = line.split()
                if len(fields) < 10:
                    raise ArtifactError(
                        f"Malformed PQR atom record at {path}:{line_number}"
                    )
                try:
                    x, y, z, charge, radius = (float(value) for value in fields[-5:])
                except ValueError as exc:
                    raise ArtifactError(
                        f"Non-numeric PQR atom data at {path}:{line_number}"
                    ) from exc
                if not all(math.isfinite(value) for value in (x, y, z, charge, radius)):
                    raise ArtifactError(
                        f"Non-finite PQR atom data at {path}:{line_number}"
                    )
                if radius < 0:
                    raise ArtifactError(
                        f"PQR radius must not be negative at {path}:{line_number}"
                    )
                if radius == 0:
                    zero_radius_count += 1
                atom_count += 1
                total_charge += charge
                minimum_radius = min(minimum_radius, radius)
                maximum_radius = max(maximum_radius, radius)
    except OSError as exc:
        raise ArtifactError(f"Could not read PQR artifact {path}: {exc}") from exc
    if atom_count == 0:
        raise ArtifactError(f"PQR artifact contains no ATOM/HETATM records: {path}")
    if maximum_radius <= 0:
        raise ArtifactError(f"PQR artifact contains no positive atomic radii: {path}")
    return PQRSummary(
        atom_count,
        total_charge,
        minimum_radius,
        maximum_radius,
        zero_radius_count,
    )


def summarize_opendx(path: Path, *, require_variation: bool = True) -> OpenDXSummary:
    """Validate an APBS OpenDX scalar grid without retaining its values."""

    grid_counts: tuple[int, int, int] | None = None
    expected_items: int | None = None
    reading_data = False
    count = 0
    total = 0.0
    minimum = math.inf
    maximum = -math.inf
    try:
        with path.open("rt", encoding="utf-8", errors="replace") as handle:
            for line_number, line in enumerate(handle, start=1):
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if grid_counts is None:
                    match = _GRID_COUNTS.fullmatch(stripped)
                    if match:
                        grid_counts = (
                            int(match.group(1)),
                            int(match.group(2)),
                            int(match.group(3)),
                        )
                        if any(value <= 0 for value in grid_counts):
                            raise ArtifactError(f"OpenDX grid counts must be positive: {path}")
                        continue
                if not reading_data:
                    match = _DATA_START.fullmatch(stripped)
                    if match:
                        expected_items = int(match.group(1))
                        if expected_items <= 0:
                            raise ArtifactError(f"OpenDX item count must be positive: {path}")
                        reading_data = True
                    continue
                assert expected_items is not None
                for token in stripped.split():
                    if count >= expected_items:
                        break
                    try:
                        value = float(token)
                    except ValueError as exc:
                        raise ArtifactError(
                            f"Non-numeric OpenDX scalar at {path}:{line_number}"
                        ) from exc
                    if not math.isfinite(value):
                        raise ArtifactError(
                            f"Non-finite OpenDX scalar at {path}:{line_number}"
                        )
                    count += 1
                    total += value
                    minimum = min(minimum, value)
                    maximum = max(maximum, value)
                if count >= expected_items:
                    break
    except OSError as exc:
        raise ArtifactError(f"Could not read OpenDX artifact {path}: {exc}") from exc

    if grid_counts is None:
        raise ArtifactError(f"OpenDX gridpositions header is missing: {path}")
    if expected_items is None:
        raise ArtifactError(f"OpenDX scalar data header is missing: {path}")
    grid_size = math.prod(grid_counts)
    if expected_items != grid_size:
        raise ArtifactError(
            f"OpenDX item count {expected_items} does not match grid size {grid_size}: {path}"
        )
    if count != expected_items:
        raise ArtifactError(
            f"OpenDX contains {count} scalars but declares {expected_items}: {path}"
        )
    if require_variation and minimum == maximum:
        raise ArtifactError(f"OpenDX potential grid is constant ({minimum:g}): {path}")
    return OpenDXSummary(grid_counts, count, minimum, maximum, total / count)
