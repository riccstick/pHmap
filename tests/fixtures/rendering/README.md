# Advanced rendering smoke fixture

`protein-ligand.pqr` is a synthetic alanine-shaped five-atom fragment plus a
three-atom `LIG` group represented by `HETATM` records. Charges and radii are
arbitrary test values, **not validated force-field parameters**. This tests
surface/representation handling, not ligand parameterization or molecular science.

`potential.in` computes a small, nonuniform APBS grid for that prepared PQR.
The native smoke script checks a protein-only render and a ligand-bearing render
for every surface mode and every supported representation, including hidden.
It uses the existing backend adapters, validates the DX and PNG products, and
retains settings, images, scripts, and logs in a fresh ignored run directory:

```console
pixi run --locked smoke-rendering
```

No PDB2PQR/MOL2 assignment is performed here. In particular, this does not claim
support for the legacy `example/ligand.mol2` FAD: PDB2PQR 3.7.1 currently fails
while assigning its parameters (`ValueError: 'P' is not in list`).
