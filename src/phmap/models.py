"""Validated domain objects used by the local pHmap workflow.

The objects in this module deliberately depend only on the Python standard
library.  They form a small boundary between untrusted CLI/configuration input
and the code that invokes scientific tools.
"""

from __future__ import annotations

import math
import os
import re
import unicodedata
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import TypeAlias

PathLike: TypeAlias = str | os.PathLike[str]
DecimalLike: TypeAlias = str | int | float | Decimal

MIN_PH = Decimal("0")
MAX_PH = Decimal("14")
MAX_PH_POINTS = 10_000
MAX_PROTEIN_ID_LENGTH = 64
MAX_PROTEIN_LABEL_LENGTH = 128

_SAFE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_UNSAFE_LABEL_CHARACTERS = frozenset('<>:"/\\|?*')
_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{number}" for number in range(1, 10)}
    | {f"LPT{number}" for number in range(1, 10)}
)


class InputValidationError(ValueError):
    """Raised when user-provided run input cannot be used safely."""


class PHInputMode(StrEnum):
    """How a set of pH values was supplied by the user."""

    EXPLICIT = "explicit"
    RANGE = "range"


def coerce_ph(value: DecimalLike, *, field_name: str = "pH") -> Decimal:
    """Convert a scalar to a finite, biologically useful pH ``Decimal``.

    Floats are converted through their textual representation rather than from
    their binary value.  Thus ``0.1`` becomes ``Decimal("0.1")`` rather than a
    long binary approximation.
    """

    if isinstance(value, bool):
        raise InputValidationError(f"{field_name} must be a number, not a boolean")

    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, (str, int, float)):
        text = str(value).strip()
        if not text:
            raise InputValidationError(f"{field_name} must not be empty")
        try:
            result = Decimal(text)
        except InvalidOperation as exc:
            raise InputValidationError(
                f"{field_name} must be a decimal number; got {value!r}"
            ) from exc
    else:
        raise InputValidationError(
            f"{field_name} must be a string or number; got {type(value).__name__}"
        )

    if not result.is_finite():
        raise InputValidationError(f"{field_name} must be finite; got {value!r}")
    if result < MIN_PH or result > MAX_PH:
        raise InputValidationError(
            f"{field_name} must be between {MIN_PH} and {MAX_PH}; got {result}"
        )
    # PDB2PQR ultimately parses pH as an IEEE-754 double. Reject a decimal that
    # cannot make that round trip so the manifest can never distinguish values
    # that the scientific backend would silently treat as identical.
    if Decimal(str(float(result))) != result:
        raise InputValidationError(
            f"{field_name} has more precision than PDB2PQR can represent; got {result}"
        )
    return result


def validate_protein_id(protein_id: str) -> str:
    """Validate and return a filesystem-safe protein identifier."""

    if not isinstance(protein_id, str):
        raise InputValidationError("protein_id must be a string")
    if not protein_id:
        raise InputValidationError("protein_id must not be empty")
    if len(protein_id) > MAX_PROTEIN_ID_LENGTH:
        raise InputValidationError(
            f"protein_id must contain at most {MAX_PROTEIN_ID_LENGTH} characters"
        )
    if not _SAFE_ID.fullmatch(protein_id):
        raise InputValidationError(
            "protein_id must start with an ASCII letter and contain only "
            "ASCII letters, digits, '-' and '_'"
        )
    if protein_id.upper() in _WINDOWS_RESERVED_NAMES:
        raise InputValidationError(f"protein_id {protein_id!r} is a reserved filename")
    return protein_id


def slugify_protein_id(value: str, *, fallback: str = "protein") -> str:
    """Create a deterministic, safe protein identifier from a filename/label."""

    if not isinstance(value, str):
        raise InputValidationError("protein identifier source must be a string")
    ascii_value = (
        unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    )
    slug = re.sub(r"[^A-Za-z0-9]+", "-", ascii_value).strip("-").lower()
    if not slug:
        slug = fallback
    if not slug[0].isalpha():
        slug = f"protein-{slug}"
    slug = slug[:MAX_PROTEIN_ID_LENGTH].rstrip("-_")
    if slug.upper() in _WINDOWS_RESERVED_NAMES:
        slug = f"protein-{slug}"
    return validate_protein_id(slug)


def validate_protein_label(label: str) -> str:
    """Validate a display label that is also safe to place in artifact names."""

    if not isinstance(label, str):
        raise InputValidationError("protein label must be a string")
    if not label or label != label.strip():
        raise InputValidationError(
            "protein label must be non-empty and have no surrounding whitespace"
        )
    if len(label) > MAX_PROTEIN_LABEL_LENGTH:
        raise InputValidationError(
            f"protein label must contain at most {MAX_PROTEIN_LABEL_LENGTH} characters"
        )
    if label in {".", ".."}:
        raise InputValidationError("protein label must not be '.' or '..'")
    if not label.isprintable():
        raise InputValidationError("protein label must not contain control characters")
    unsafe = sorted(set(label) & _UNSAFE_LABEL_CHARACTERS)
    if unsafe:
        raise InputValidationError(
            "protein label contains unsafe filename character(s): " + " ".join(unsafe)
        )
    if label.endswith("."):
        raise InputValidationError("protein label must not end with a period")
    if label.upper() in _WINDOWS_RESERVED_NAMES:
        raise InputValidationError(f"protein label {label!r} is a reserved filename")
    return label


def safe_protein_label(value: str, *, fallback: str = "protein") -> str:
    """Normalize arbitrary filename text into a safe human-readable label."""

    if not isinstance(value, str):
        raise InputValidationError("protein label source must be a string")
    normalized = unicodedata.normalize("NFKC", value)
    normalized = "".join(
        "-" if char in _UNSAFE_LABEL_CHARACTERS or not char.isprintable() else char
        for char in normalized
    )
    normalized = re.sub(r"\s+", " ", normalized).strip(" .")
    normalized = normalized[:MAX_PROTEIN_LABEL_LENGTH].rstrip(" .")
    if not normalized:
        normalized = fallback
    if normalized.upper() in _WINDOWS_RESERVED_NAMES:
        normalized = f"Protein {normalized}"
    return validate_protein_label(normalized)


def _validated_file(path: PathLike, *, suffix: str, description: str) -> Path:
    if isinstance(path, bool) or not isinstance(path, (str, os.PathLike)):
        raise InputValidationError(f"{description} path must be a path-like value")
    candidate = Path(path).expanduser()
    if candidate.suffix.lower() != suffix:
        raise InputValidationError(
            f"{description} must have a {suffix} extension; got {candidate.name!r}"
        )
    if not candidate.exists():
        raise InputValidationError(f"{description} does not exist: {candidate}")
    if not candidate.is_file():
        raise InputValidationError(f"{description} is not a regular file: {candidate}")
    return candidate.resolve()


@dataclass(frozen=True, init=False)
class PHRange:
    """An inclusive, ascending pH range represented without float drift."""

    start: Decimal
    stop: Decimal
    step: Decimal

    def __init__(
        self, start: DecimalLike, stop: DecimalLike, step: DecimalLike
    ) -> None:
        parsed_start = coerce_ph(start, field_name="pH range start")
        parsed_stop = coerce_ph(stop, field_name="pH range stop")
        parsed_step = coerce_ph(step, field_name="pH range step")
        if parsed_step == 0:
            raise InputValidationError("pH range step must be greater than zero")
        if parsed_start > parsed_stop:
            raise InputValidationError("pH range start must not be greater than stop")
        object.__setattr__(self, "start", parsed_start)
        object.__setattr__(self, "stop", parsed_stop)
        object.__setattr__(self, "step", parsed_step)

    def values(self) -> tuple[Decimal, ...]:
        """Expand the range, including ``stop`` only when the step lands on it."""

        values: list[Decimal] = []
        current = self.start
        while current <= self.stop:
            values.append(current)
            if len(values) > MAX_PH_POINTS:
                raise InputValidationError(
                    f"pH range produces more than {MAX_PH_POINTS} values"
                )
            next_value = current + self.step
            if next_value <= current:
                # Protect against a caller changing the global Decimal context to
                # a precision at which adding the step no longer makes progress.
                raise InputValidationError("pH range step is too small to make progress")
            current = next_value
        return tuple(values)

    def __iter__(self) -> Iterator[Decimal]:
        return iter(self.values())


@dataclass(frozen=True, init=False)
class PyMOLView:
    """The 18 finite floats emitted by PyMOL's ``get_view`` command."""

    values: tuple[float, ...]

    def __init__(self, values: Sequence[float]) -> None:
        if isinstance(values, (str, bytes)):
            raise InputValidationError("PyMOL view must be a sequence of 18 numbers")
        try:
            raw_values = tuple(values)
        except TypeError as exc:
            raise InputValidationError("PyMOL view must be a sequence of 18 numbers") from exc
        if len(raw_values) != 18:
            raise InputValidationError(
                f"PyMOL view must contain exactly 18 values; got {len(raw_values)}"
            )

        converted: list[float] = []
        for index, value in enumerate(raw_values, start=1):
            if isinstance(value, bool):
                raise InputValidationError(
                    f"PyMOL view value {index} must be a finite float"
                )
            try:
                number = float(value)
            except (TypeError, ValueError, OverflowError) as exc:
                raise InputValidationError(
                    f"PyMOL view value {index} must be a finite float; got {value!r}"
                ) from exc
            if not math.isfinite(number):
                raise InputValidationError(
                    f"PyMOL view value {index} must be finite; got {value!r}"
                )
            converted.append(number)
        object.__setattr__(self, "values", tuple(converted))

    def __iter__(self) -> Iterator[float]:
        return iter(self.values)

    def __len__(self) -> int:
        return 18


@dataclass(frozen=True, init=False)
class ProteinInput:
    """A protein structure and optional ligand ready for processing."""

    path: Path
    protein_id: str
    label: str
    ligand: Path | None

    def __init__(
        self,
        path: PathLike,
        protein_id: str | None = None,
        label: str | None = None,
        ligand: PathLike | None = None,
    ) -> None:
        validated_path = _validated_file(path, suffix=".pdb", description="PDB input")
        validated_id = (
            slugify_protein_id(validated_path.stem)
            if protein_id is None
            else validate_protein_id(protein_id)
        )
        validated_label = (
            safe_protein_label(validated_path.stem)
            if label is None
            else validate_protein_label(label)
        )
        validated_ligand = (
            None
            if ligand is None
            else _validated_file(ligand, suffix=".mol2", description="MOL2 ligand")
        )
        object.__setattr__(self, "path", validated_path)
        object.__setattr__(self, "protein_id", validated_id)
        object.__setattr__(self, "label", validated_label)
        object.__setattr__(self, "ligand", validated_ligand)


@dataclass(frozen=True, init=False)
class RunInputs:
    """Fully validated inputs for one pHmap run."""

    proteins: tuple[ProteinInput, ...]
    ph_values: tuple[Decimal, ...]
    view: PyMOLView | None
    ph_mode: PHInputMode

    def __init__(
        self,
        proteins: Iterable[ProteinInput],
        ph_values: Iterable[DecimalLike],
        view: PyMOLView | None = None,
        ph_mode: PHInputMode | str = PHInputMode.EXPLICIT,
    ) -> None:
        parsed_proteins = tuple(proteins)
        if not parsed_proteins:
            raise InputValidationError("at least one PDB input is required")
        if not all(isinstance(protein, ProteinInput) for protein in parsed_proteins):
            raise InputValidationError("proteins must contain only ProteinInput objects")

        ids = [protein.protein_id.casefold() for protein in parsed_proteins]
        labels = [protein.label.casefold() for protein in parsed_proteins]
        if len(ids) != len(set(ids)):
            raise InputValidationError("protein IDs must be unique (case-insensitive)")
        if len(labels) != len(set(labels)):
            raise InputValidationError("protein labels must be unique (case-insensitive)")

        raw_ph_values = tuple(ph_values)
        if not raw_ph_values:
            raise InputValidationError("at least one pH value is required")
        if len(raw_ph_values) > MAX_PH_POINTS:
            raise InputValidationError(
                f"a run may contain at most {MAX_PH_POINTS} pH values"
            )
        parsed_ph_values = tuple(
            coerce_ph(value, field_name=f"pH value {index}")
            for index, value in enumerate(raw_ph_values, start=1)
        )
        if len(set(parsed_ph_values)) != len(parsed_ph_values):
            raise InputValidationError("pH values must be unique")

        try:
            parsed_mode = PHInputMode(ph_mode)
        except ValueError as exc:
            allowed = ", ".join(mode.value for mode in PHInputMode)
            raise InputValidationError(f"ph_mode must be one of: {allowed}") from exc
        if view is not None and not isinstance(view, PyMOLView):
            raise InputValidationError("view must be a PyMOLView object or None")

        object.__setattr__(self, "proteins", parsed_proteins)
        object.__setattr__(self, "ph_values", parsed_ph_values)
        object.__setattr__(self, "view", view)
        object.__setattr__(self, "ph_mode", parsed_mode)
