from pathlib import Path

import pytest

from phmap.errors import ArtifactError
from phmap.scientific import summarize_opendx, summarize_pqr


def test_summarize_pqr_validates_numerical_atom_fields(tmp_path: Path) -> None:
    pqr = tmp_path / "model.pqr"
    pqr.write_text(
        "REMARK example\n"
        "ATOM 1 N ALA A 1 0.0 1.0 2.0 -0.3 1.5\n"
        "HETATM 2 C LIG A 2 3.0 4.0 5.0 0.2 1.7\n"
        "ATOM 3 H ALA A 1 0.5 1.5 2.5 0.1 0.0\n",
        encoding="utf-8",
    )

    summary = summarize_pqr(pqr)

    assert summary.atom_count == 3
    assert summary.total_charge == pytest.approx(0.0)
    assert summary.minimum_radius == 0.0
    assert summary.maximum_radius == 1.7
    assert summary.zero_radius_count == 1


def test_summarize_pqr_rejects_negative_radius(tmp_path: Path) -> None:
    pqr = tmp_path / "model.pqr"
    pqr.write_text("ATOM 1 N ALA A 1 0 0 0 -0.3 -0.1\n", encoding="utf-8")

    with pytest.raises(ArtifactError, match="radius must not be negative"):
        summarize_pqr(pqr)


def test_summarize_pqr_rejects_only_zero_radii(tmp_path: Path) -> None:
    pqr = tmp_path / "model.pqr"
    pqr.write_text("ATOM 1 H ALA A 1 0 0 0 0 0\n", encoding="utf-8")

    with pytest.raises(ArtifactError, match="no positive atomic radii"):
        summarize_pqr(pqr)


def test_summarize_opendx_validates_grid_and_statistics(tmp_path: Path) -> None:
    dx = tmp_path / "potential.dx"
    dx.write_text(
        "object 1 class gridpositions counts 1 1 3\n"
        "origin 0 0 0\n"
        "delta 1 0 0\n"
        "object 2 class gridconnections counts 1 1 3\n"
        "object 3 class array type double rank 0 items 3 data follows\n"
        "-2.0 0.0 1.0\n"
        'attribute "dep" string "positions"\n',
        encoding="utf-8",
    )

    summary = summarize_opendx(dx)

    assert summary.grid_counts == (1, 1, 3)
    assert summary.value_count == 3
    assert summary.minimum == -2.0
    assert summary.maximum == 1.0
    assert summary.mean == pytest.approx(-1 / 3)


def test_summarize_opendx_rejects_constant_or_truncated_grid(tmp_path: Path) -> None:
    dx = tmp_path / "potential.dx"
    dx.write_text(
        "object 1 class gridpositions counts 1 1 2\n"
        "object 3 class array type double rank 0 items 2 data follows\n"
        "1.0 1.0\n",
        encoding="utf-8",
    )
    with pytest.raises(ArtifactError, match="constant"):
        summarize_opendx(dx)

    dx.write_text(
        "object 1 class gridpositions counts 1 1 2\n"
        "object 3 class array type double rank 0 items 2 data follows\n"
        "1.0\n",
        encoding="utf-8",
    )
    with pytest.raises(ArtifactError, match="contains 1 scalars"):
        summarize_opendx(dx)
