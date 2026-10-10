# Local development

The Python application is developed and tested locally. The preferred
environment uses [Pixi](https://pixi.sh/) so the Python package and the native
scientific programs share one reproducible environment.

The initial verified target is Apple Silicon macOS (`osx-arm64`); the lock now
also includes Intel macOS, Windows x64, and Linux x64. It contains Python
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
  --view example/set_view.txt \
  --runs-dir 'runs/Scientific smoke with spaces'
```

The real smoke workflow requires working PDB2PQR, APBS, and headless PyMOL
executables. Each smoke invocation creates a unique directory below
`runs/Scientific smoke with spaces/`, which is ignored by Git. The spaced path
regresses APBS input/output filename handling, including the macOS desktop's
`Application Support` storage directory. Supply `--output-dir` when a fixed,
new destination is useful; existing destinations are deliberately rejected. Preserve a run outside
`runs/` when it is needed as reviewed validation evidence.

## Current limitations

- The Pixi manifest locks Apple Silicon/Intel macOS, Windows x64, and Linux x64.
  Apple Silicon is locally verified; the desktop Actions matrix gates the other
  platforms with native tests and a real scientific smoke calculation.
- The local workflow supports one or more proteins, explicit pH values or
  an inclusive range, an optional validated camera view, and local execution.
  A single MOL2 ligand can be shared across proteins but remains experimental.
  Activity plots, interactive 3D camera editing, and remote execution are later stages.
- Headless PyMOL rendering can vary slightly across operating-system, graphics,
  font, and PyMOL builds. Treat rendered pixels as presentation output, not as
  the sole scientific regression signal.
- The historical PNG files in `docs/reference/` do not record all input
  parameters or tool versions. They are useful visual references, but not
  authoritative numerical baselines. The retained activity table is a fixture
  for planned activity support, not a currently supported application input.

The [feature parity and implementation roadmap](feature-roadmap.md) distinguishes
missing capabilities from settings already implemented in lower-level Python
components. It records acceptance criteria and the completed legacy cleanup.

## GUI development

`pixi run gui` launches a FastAPI server on `127.0.0.1:8765` and opens the local
page. `pixi run phmap gui --no-browser` starts the same interface without opening
a window. The CLI's existing `run`, `validate`, and `doctor` commands retain
their arguments and behavior.

The interface consists of a packaged Jinja template, CSS, and a small JavaScript
client; no Node build or CDN is required. The client submits multipart uploads
and validated settings, then receives manifest snapshots through Server-Sent
Events. GUI and CLI use the same validation models and scientific workflow.

`JobManager` runs a single isolated `python -m phmap run` worker at a time. It
keeps queue metadata and uploaded input copies below `runs/.gui/`. Each worker
creates its normal `runs/<run-id>/` workspace. Completed CLI manifests are read
directly into history. Cancellation terminates the worker's process group on
macOS/Linux (and its process tree on Windows), including scientific children;
it retains artifacts and marks active tasks as cancelled. Normal server shutdown
cancels remaining GUI jobs. On restart, abandoned GUI records are marked
interrupted rather than silently restarted. Existing CLI calculations can be
viewed but are not owned or cancelled by the GUI.

The Python/Tk desktop launcher is packaged with PyInstaller and a Pixi binary.
It installs a versioned locked runtime on first launch, then starts the same
unfrozen Python GUI process. Its shutdown endpoint requires a separate
launcher-only secret; CLI-started servers do not grant this authority. Both
launcher ownership and scientific queue ownership use cross-platform file
locks. Windows hides child consoles and cancels complete worker trees, rather
than terminating only the Python parent. See [releases](releases.md) for build,
first-run installation, publication, and signing limitations.

The GUI accepts at most 32 proteins, 512 cells, 4096-pixel tiles, and 64 MiB of
uploads per submission. The CLI retains its existing limits. The server binds
only to loopback, checks allowed host and same-origin requests, requires a
per-session token for writes, and confines downloads to the selected run.

Tests cover actual worker subprocesses with fake scientific tools, multi-file
validation, SSE completion, cancellation, restart handling, CLI history, and
artifact containment. Run them through the normal `pixi run test` task.

## Scientific baseline policy

A reproducible pHmap result is defined by more than the final image. For every
accepted baseline, retain or record:

- the pHmap, PDB2PQR, APBS, and PyMOL versions;
- the locked dependency environment and operating-system architecture;
- hashes of the PDB and ligand inputs and the validated camera values;
  include activity inputs when activity support is implemented;
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
