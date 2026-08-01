# Local development

The first Python implementation is developed and tested locally. The preferred
environment uses [Pixi](https://pixi.sh/) so the Python package and the native
scientific programs share one reproducible environment.

The initial Pixi target is Apple Silicon macOS (`osx-arm64`). It contains Python
3.12, [APBS 3.4.1](https://anaconda.org/conda-forge/apbs), and
[PyMOL Open Source 3.1.0](https://anaconda.org/conda-forge/pymol-open-source)
from conda-forge. pHmap itself is installed in editable mode, including its
runtime dependencies. The environment installs
[PDB2PQR 3.7.x](https://pypi.org/project/pdb2pqr/) from PyPI. This split is
intentional: upstream currently publishes newer PDB2PQR releases through PyPI
than conda-forge does.

## Set up the environment

Install Pixi by following its
[official installation instructions](https://pixi.sh/latest/installation/),
then run the following commands from the repository root:

```console
pixi install
pixi run test
pixi run lint
pixi run typecheck
```

`pixi install` creates `.pixi/` locally and installs this checkout in editable
mode. Source changes are therefore picked up without reinstalling the package.
The generated `pixi.lock` should be committed whenever dependencies are first
resolved or intentionally updated; it is part of the scientific baseline.

If you are only changing Python code and already provide compatible scientific
executables yourself, the lightweight alternative is:

```console
uv sync --group dev
uv run phmap doctor
```

That route does not install PDB2PQR, APBS, or PyMOL. All three must already be
discoverable on `PATH`, so Pixi is the supported route for the complete local
workflow.

## Check the toolchain

Run the diagnostic before starting an expensive calculation:

```console
pixi run doctor
```

This invokes `phmap doctor`, which checks the Python runtime and the external
programs used by the pipeline. Resolve every reported missing or incompatible
backend before attempting the real smoke run. A successful Python-only unit
test run does not prove that PDB2PQR, APBS, and PyMOL can execute locally.

## Validate and run the smoke workflow

First validate the inputs without launching a scientific backend:

```console
pixi run validate-smoke
```

The expanded command is:

```console
phmap validate example/mutant2.pdb \
  --ph 5.0 \
  --ph 7.0 \
  --view example/set_view.txt
```

Then run the same two-pH input through the local pipeline:

```console
pixi run smoke
```

Equivalent direct invocation:

```console
phmap run example/mutant2.pdb \
  --ph 5.0 \
  --ph 7.0 \
  --view example/set_view.txt
```

The real smoke workflow requires working PDB2PQR, APBS, and headless PyMOL
executables. Each smoke invocation creates a unique directory below `runs/`,
which is ignored by Git. Supply `--output-dir` when a fixed, new destination is
useful; existing destinations are deliberately rejected. Preserve a run outside
`runs/` when it is needed as reviewed validation evidence.

## Current limitations

- The reproducible Pixi manifest currently targets native Apple Silicon macOS.
  Other platforms may work when compatible backends are installed manually,
  but they are not yet part of the supported baseline.
- The first vertical slice supports one or more proteins, explicit pH values or
  an inclusive range, an optional validated camera view, and local execution.
  A single MOL2 ligand can be shared across proteins but remains experimental.
  Activity plots, a graphical interface, and remote execution are later stages.
- Headless PyMOL rendering can vary slightly across operating-system, graphics,
  font, and PyMOL builds. Treat rendered pixels as presentation output, not as
  the sole scientific regression signal.
- The legacy reference PNG files do not record all input parameters or tool
  versions. They are useful visual references, but not authoritative numerical
  baselines.

## Scientific baseline policy

A reproducible pHmap result is defined by more than the final image. For every
accepted baseline, retain or record:

- the pHmap, PDB2PQR, APBS, and PyMOL versions;
- the locked dependency environment and operating-system architecture;
- hashes of the PDB, ligand, camera, and activity inputs that were used;
- all validated run parameters and the exact pH ordering;
- the generated PQR, APBS input, OpenDX map, stage logs, and run manifest; and
- the final tiles and composed image.

Baseline changes must be deliberate and reviewed. Re-run the small smoke fixture
after any backend or parameter update. Compare numerical artifacts first (for
example atom counts, assigned charges, grid dimensions, and potential summary
statistics), then use tolerant image comparison plus visual inspection for the
rendering. Do not require pixel identity across backend or platform changes, and
do not approve a scientific change solely because the final PNG looks similar.

When the baseline is intentionally updated, commit the new lock file and document
the changed tool versions, parameters, expected numerical differences, and the
reason for accepting them.
