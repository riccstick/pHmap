"""Exercise advanced controls with native APBS/PyMOL and a prepared PQR fixture.

The small synthetic fixture isolates rendering from experimental MOL2 charge
assignment. Its arbitrary charges/radii are not a scientific reference system.
"""

from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import asdict
from pathlib import Path

from phmap.backends import (
    ApbsBackend,
    ApbsRequest,
    CommandRunner,
    PyMOLBackend,
    PyMOLRenderOptions,
    PyMOLRenderRequest,
)
from phmap.backends.pymol import LigandRepresentation
from phmap.manifest import sha256_file
from phmap.scientific import summarize_opendx, summarize_pqr
from phmap.versions import version_line
from phmap.workspace import RunWorkspace

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-dir", type=Path, default=ROOT / "runs/Advanced rendering smoke")
    args = parser.parse_args()
    workspace = RunWorkspace.create(args.runs_dir)
    fixture = ROOT / "tests/fixtures/rendering"
    pqr = workspace.work / "fixture.pqr"
    protein = workspace.work / "protein.pqr"
    apbs_input = workspace.work / "potential.in"
    shutil.copy2(fixture / "protein-ligand.pqr", pqr)
    protein.write_text(
        "".join(line for line in pqr.read_text().splitlines(keepends=True)
                if not line.startswith("HETATM")), encoding="ascii",
    )
    shutil.copy2(fixture / "potential.in", apbs_input)
    runner = CommandRunner(workspace.logs)
    apbs = ApbsBackend(runner)
    pymol = PyMOLBackend(runner)
    dx = workspace.work / "potential.dx"
    apbs.run(ApbsRequest(apbs_input, dx), timeout=120)
    versions_dir = workspace.work / "versions"
    versions_dir.mkdir()
    report: dict[str, object] = {
        "fixture": "synthetic prepared PQR; not a ligand parameterization validation",
        "pqr": summarize_pqr(pqr).as_dict(), "dx": summarize_opendx(dx).as_dict(),
        "tools": {
            "apbs": version_line(apbs.version(cwd=versions_dir).output),
            "pymol": version_line(pymol.version(cwd=versions_dir).output),
        },
        "sha256": {"pqr": sha256_file(pqr), "dx": sha256_file(dx)},
    }
    cases: list[tuple[str, Path, PyMOLRenderOptions]] = [
        ("protein-default", protein, PyMOLRenderOptions(width=160, height=160)),
    ]
    representations: tuple[LigandRepresentation | None, ...] = (
        "sticks", "lines", "dots", "spheres", None,
    )
    for mode, representation in enumerate(representations):
        cases.append((f"ligand-mode-{mode}-{representation or 'hidden'}", pqr, PyMOLRenderOptions(
            width=160, height=160, surface_mode=mode,
            surface_solvent=bool(mode % 2), surface_ramp_above_mode=not bool(mode % 2),
            ligand_representation=representation,
        )))
    rendered = []
    for name, source, options in cases:
        png = workspace.renders / f"{name}.png"
        result = pymol.render(
            PyMOLRenderRequest(source, dx, png, options=options), stage=name, timeout=120,
        )
        # PyMOL can exit successfully after reporting a PML error. Do not allow
        # a later PNG command to conceal an invalid setting or representation.
        output = result.command.read_stdout() + result.command.read_stderr()
        if "Error:" in output or "Traceback" in output:
            raise RuntimeError(f"PyMOL reported an error in {name}: {output}")
        rendered.append({"case": name, "png": str(png), "options": asdict(options)})
    report["renders"] = rendered
    (workspace.root / "rendering-smoke.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8",
    )
    print(f"Verified {len(cases)} native render cases: {workspace.root}")


if __name__ == "__main__":
    main()
