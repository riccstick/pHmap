import pytest

from phmap.versions import version_line


@pytest.mark.parametrize(("output", "expected"), [
    ("", None), (" \n \t", None), ("tool 1.2\nextra text", "tool 1.2"),
    ("warning\n APBS 3.4.1\n", "APBS 3.4.1"),
    ("startup notice\npdb2pqr 3.7.1", "pdb2pqr 3.7.1"),
    ("startup notice\nPyMOL 3.1.0", "PyMOL 3.1.0"),
    ("x" * 600, "x" * 500),
])
def test_version_display_is_consistent(output: str, expected: str | None) -> None:
    assert version_line(output) == expected
