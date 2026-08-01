"""Parsing and construction helpers for validated pHmap domain objects."""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Sequence
from decimal import Decimal
from pathlib import Path
from typing import TypeAlias

from .models import (
    MAX_PH_POINTS,
    MAX_PROTEIN_ID_LENGTH,
    MAX_PROTEIN_LABEL_LENGTH,
    DecimalLike,
    InputValidationError,
    PathLike,
    PHInputMode,
    PHRange,
    ProteinInput,
    PyMOLView,
    RunInputs,
    coerce_ph,
    safe_protein_label,
    slugify_protein_id,
    validate_protein_id,
    validate_protein_label,
)

PHValuesInput: TypeAlias = str | Iterable[DecimalLike]
PHRangeInput: TypeAlias = str | Iterable[DecimalLike] | PHRange
PDBPathsInput: TypeAlias = PathLike | Iterable[PathLike]


_FLOAT_LITERAL = re.compile(
    r"^[+-]?(?:(?:[0-9]+(?:\.[0-9]*)?)|(?:\.[0-9]+))(?:[eE][+-]?[0-9]+)?$"
)
_SET_VIEW = re.compile(r"^\s*set_view\s*\((?P<body>.*)\)\s*$", re.DOTALL)
_PARENTHESIZED_VIEW = re.compile(r"^\s*\((?P<body>.*)\)\s*$", re.DOTALL)


def _tokens(value: str, *, description: str) -> tuple[str, ...]:
    stripped = value.strip()
    if not stripped:
        raise InputValidationError(f"{description} must not be empty")
    if re.search(r"(^\s*,)|(,\s*$)|(,\s*,)", value):
        raise InputValidationError(f"{description} contains an empty value")
    return tuple(token for token in re.split(r"[\s,]+", stripped) if token)


def parse_ph_value(value: DecimalLike) -> Decimal:
    """Parse one pH value into a finite ``Decimal`` between 0 and 14."""

    return coerce_ph(value)


def parse_ph_values(values: PHValuesInput) -> tuple[Decimal, ...]:
    """Parse an explicit, ordered list of pH values.

    Strings may use commas, whitespace, or both.  Iterable order is retained;
    values are never sorted implicitly.
    """

    raw_values: tuple[DecimalLike, ...]
    if isinstance(values, str):
        raw_values = _tokens(values, description="pH values")
    else:
        try:
            supplied = tuple(values)
        except TypeError as exc:
            raise InputValidationError("pH values must be a string or iterable") from exc
        expanded: list[DecimalLike] = []
        for value in supplied:
            if isinstance(value, str) and ("," in value or len(value.split()) > 1):
                expanded.extend(_tokens(value, description="pH values"))
            else:
                expanded.append(value)
        raw_values = tuple(expanded)

    if not raw_values:
        raise InputValidationError("at least one pH value is required")
    if len(raw_values) > MAX_PH_POINTS:
        raise InputValidationError(
            f"a run may contain at most {MAX_PH_POINTS} pH values"
        )
    parsed = tuple(
        coerce_ph(value, field_name=f"pH value {index}")
        for index, value in enumerate(raw_values, start=1)
    )
    if len(set(parsed)) != len(parsed):
        raise InputValidationError("pH values must be unique")
    return parsed


def parse_ph_range(
    start: DecimalLike | Iterable[DecimalLike],
    stop: DecimalLike | None = None,
    step: DecimalLike | None = None,
) -> PHRange:
    """Parse a pH range from three arguments or a ``"start stop step"`` value."""

    if stop is None and step is None:
        parts: tuple[DecimalLike, ...]
        if isinstance(start, str):
            parts = _tokens(start, description="pH range")
        elif isinstance(start, Iterable):
            parts = tuple(start)
        else:
            raise InputValidationError("pH range must contain start, stop, and step")
        if len(parts) != 3:
            raise InputValidationError(
                f"pH range must contain exactly start, stop, and step; got {len(parts)} values"
            )
        return PHRange(parts[0], parts[1], parts[2])

    if stop is None or step is None:
        raise InputValidationError("pH range requires start, stop, and step")
    if not isinstance(start, (str, int, float, Decimal)):
        raise InputValidationError("pH range start must be a decimal number")
    return PHRange(start, stop, step)


def parse_ph_selection(
    *,
    ph_values: PHValuesInput | None = None,
    ph_range: PHRangeInput | None = None,
) -> tuple[tuple[Decimal, ...], PHInputMode]:
    """Resolve exactly one explicit-list or range pH input source."""

    if (ph_values is None) == (ph_range is None):
        raise InputValidationError(
            "provide exactly one of explicit pH values or a pH range"
        )
    if ph_values is not None:
        return parse_ph_values(ph_values), PHInputMode.EXPLICIT
    assert ph_range is not None
    parsed_range = ph_range if isinstance(ph_range, PHRange) else parse_ph_range(ph_range)
    return parsed_range.values(), PHInputMode.RANGE


def _resolve_file(path: PathLike, *, suffix: str, description: str) -> Path:
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


def validate_pdb_path(path: PathLike) -> Path:
    """Return the absolute path to an existing ``.pdb`` file."""

    return _resolve_file(path, suffix=".pdb", description="PDB input")


def validate_mol2_path(path: PathLike) -> Path:
    """Return the absolute path to an existing ``.mol2`` ligand file."""

    return _resolve_file(path, suffix=".mol2", description="MOL2 ligand")


def parse_pymol_view(value: str | Sequence[float]) -> PyMOLView:
    """Parse only the exact 18-number form accepted by PyMOL ``set_view``.

    The parser accepts the output copied from ``get_view``, a parenthesized
    tuple, a comma-separated numeric body, or an already numeric sequence.  It
    uses a full-token numeric grammar so command text, comments, NaN, and
    infinity cannot be smuggled into a generated PyMOL script.
    """

    if not isinstance(value, str):
        return PyMOLView(value)

    text = value.lstrip("\ufeff")
    wrapper = _SET_VIEW.fullmatch(text)
    if wrapper:
        body = wrapper.group("body")
    else:
        if re.match(r"^\s*set_view\b", text):
            raise InputValidationError("invalid PyMOL set_view syntax")
        parenthesized = _PARENTHESIZED_VIEW.fullmatch(text)
        body = parenthesized.group("body") if parenthesized else text.strip()

    # PyMOL's copy/paste output uses backslashes as line continuations.  A
    # backslash is accepted only when followed by whitespace; any remaining
    # punctuation is rejected by the numeric token grammar below.
    body = re.sub(r"\\\s+", " ", body)
    parts = tuple(part.strip() for part in body.split(","))
    if len(parts) != 18:
        raise InputValidationError(
            f"PyMOL view must contain exactly 18 comma-separated values; got {len(parts)}"
        )

    numbers = []
    for index, part in enumerate(parts, start=1):
        if not _FLOAT_LITERAL.fullmatch(part):
            raise InputValidationError(
                f"PyMOL view value {index} is not a valid finite float: {part!r}"
            )
        numbers.append(float(part))
    return PyMOLView(numbers)


def load_pymol_view(path: PathLike) -> PyMOLView:
    """Read and strictly parse a UTF-8 text file containing a PyMOL view."""

    if isinstance(path, bool) or not isinstance(path, (str, os.PathLike)):
        raise InputValidationError("PyMOL view path must be a path-like value")
    candidate = Path(path).expanduser()
    if not candidate.exists():
        raise InputValidationError(f"PyMOL view file does not exist: {candidate}")
    if not candidate.is_file():
        raise InputValidationError(f"PyMOL view path is not a file: {candidate}")
    try:
        text = candidate.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InputValidationError(
            f"PyMOL view file must be UTF-8 text: {candidate}"
        ) from exc
    return parse_pymol_view(text)


def _optional_values(
    values: Iterable[str | None] | None, count: int, *, description: str
) -> tuple[str | None, ...]:
    if values is None:
        return (None,) * count
    supplied: tuple[str | None, ...] = (
        (values,) if isinstance(values, str) else tuple(values)
    )
    if len(supplied) != count:
        raise InputValidationError(
            f"{description} count ({len(supplied)}) must match PDB count ({count})"
        )
    return supplied


def _unique_id(base: str, used: set[str]) -> str:
    candidate = base
    suffix_number = 2
    while candidate.casefold() in used:
        suffix = f"-{suffix_number}"
        candidate = f"{base[: MAX_PROTEIN_ID_LENGTH - len(suffix)].rstrip('-_')}{suffix}"
        suffix_number += 1
    used.add(candidate.casefold())
    return candidate


def _unique_label(base: str, used: set[str]) -> str:
    candidate = base
    suffix_number = 2
    while candidate.casefold() in used:
        suffix = f" ({suffix_number})"
        candidate = f"{base[: MAX_PROTEIN_LABEL_LENGTH - len(suffix)].rstrip(' .')}{suffix}"
        suffix_number += 1
    used.add(candidate.casefold())
    return candidate


def build_protein_inputs(
    pdb_paths: PDBPathsInput,
    *,
    protein_ids: Iterable[str | None] | None = None,
    labels: Iterable[str | None] | None = None,
    ligand: PathLike | None = None,
) -> tuple[ProteinInput, ...]:
    """Validate PDBs and create proteins with stable, unique IDs and labels.

    A single optional MOL2 ligand is associated with every protein, matching
    the original local workflow.  Explicit IDs/labels are never silently
    changed; generated collisions receive ``-2`` / `` (2)`` suffixes.
    """

    raw_paths: tuple[PathLike, ...] = (
        (pdb_paths,)
        if isinstance(pdb_paths, (str, os.PathLike))
        else tuple(pdb_paths)
    )
    if not raw_paths:
        raise InputValidationError("at least one PDB input is required")
    paths = tuple(validate_pdb_path(path) for path in raw_paths)
    supplied_ids = _optional_values(protein_ids, len(paths), description="protein ID")
    supplied_labels = _optional_values(labels, len(paths), description="protein label")
    ligand_path = None if ligand is None else validate_mol2_path(ligand)

    # Reserve explicit names first so generated names yield to user choices,
    # independent of argument ordering.
    used_ids: set[str] = set()
    for protein_id in supplied_ids:
        if protein_id is None:
            continue
        validated = validate_protein_id(protein_id)
        folded = validated.casefold()
        if folded in used_ids:
            raise InputValidationError("explicit protein IDs must be unique")
        used_ids.add(folded)

    used_labels: set[str] = set()
    for label in supplied_labels:
        if label is None:
            continue
        validated = validate_protein_label(label)
        folded = validated.casefold()
        if folded in used_labels:
            raise InputValidationError("explicit protein labels must be unique")
        used_labels.add(folded)

    resolved_ids = []
    resolved_labels = []
    for path, supplied_id, supplied_label in zip(
        paths, supplied_ids, supplied_labels, strict=True
    ):
        if supplied_id is None:
            resolved_ids.append(_unique_id(slugify_protein_id(path.stem), used_ids))
        else:
            resolved_ids.append(supplied_id)
        if supplied_label is None:
            resolved_labels.append(
                _unique_label(safe_protein_label(path.stem), used_labels)
            )
        else:
            resolved_labels.append(supplied_label)

    return tuple(
        ProteinInput(path, protein_id, label, ligand_path)
        for path, protein_id, label in zip(
            paths, resolved_ids, resolved_labels, strict=True
        )
    )


def validate_run_inputs(
    pdb_paths: PDBPathsInput,
    *,
    ph_values: PHValuesInput | None = None,
    ph_range: PHRangeInput | None = None,
    protein_ids: Iterable[str | None] | None = None,
    labels: Iterable[str | None] | None = None,
    ligand: PathLike | None = None,
    view_file: PathLike | None = None,
) -> RunInputs:
    """Build a complete ``RunInputs`` object from CLI/config-like values."""

    proteins = build_protein_inputs(
        pdb_paths,
        protein_ids=protein_ids,
        labels=labels,
        ligand=ligand,
    )
    values, mode = parse_ph_selection(ph_values=ph_values, ph_range=ph_range)
    view = None if view_file is None else load_pymol_view(view_file)
    return RunInputs(proteins, values, view=view, ph_mode=mode)
