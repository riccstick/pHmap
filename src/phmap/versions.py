"""Consistent display of tool versions in preflight checks and provenance."""

import re

_VERSION_LINE = re.compile(r"\b(?:PDB2PQR|pdb2pqr|APBS|PyMOL)\b.*\d")


def version_line(output: str) -> str | None:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        return None
    return next((line for line in lines if _VERSION_LINE.search(line)), lines[0])[:500]
