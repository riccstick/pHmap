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

Type checking targets the package's Python 3.11 minimum, even though the full
scientific runtime uses Python 3.12. Gemmi's type stubs transitively import NumPy
stubs with Python 3.12 syntax. The mypy configuration skips only `numpy` and its
stub submodules using `follow_imports_for_stubs`; pHmap does not use the NumPy API
directly. Strict checks of pHmap and normal import checking for other libraries
remain enabled.

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

Also exercise all five surface modes and all ligand representations using
native APBS/PyMOL with a small prepared protein/ligand PQR fixture:

```console
pixi run --locked smoke-rendering
```

This checks a protein-only render plus five ligand-bearing renders and retains
PNGs, PML, logs, and effective settings under `runs/Advanced rendering smoke/`.
It runs in desktop CI on all four target platforms. The fixture has synthetic
charges/radii and isolates rendering from experimental MOL2 assignment; it is
not a force-field or ligand-parameterization reference. See
`tests/fixtures/rendering/README.md` for its scope.

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

Publication styling is validated by `CompositionSettings` and passed through
`WorkflowOptions`, GUI requests, worker arguments, and `configuration.composition`
in manifests. `format_ph_labels` uses decimal half-even rounding and rejects
ambiguous labels before network requests or scientific execution. This affects
presentation only: numerical pH values and PyMOL render options are unchanged.
The compositor uses Pillow's bundled default font, measures actual text bounds,
expands cells/legend space when necessary, and composites one tile at a time.
It rejects inconsistent/non-PNG tiles and figures above 100 million pixels
before allocating the canvas. The final PNG retains the selected DPI metadata.
Additional font choices remain follow-up work. Saved-run editing is implemented
by `saved_runs.SavedRun`: `compose` verifies/copies PNG tiles and runs only the
Python compositor; `render` verifies/copies PQR/DX and executes only PyMOL plus
composition. Expected per-cell paths are rebound inside the source directory,
never to original/external paths. Hashes are checked at preflight and while
copying; queued CLI workers pin the source manifest digest and verify again.
No archived scripts/argv are executed. Presentation-only options preserve tile
render metadata (including transparent backgrounds), preventing later edits
from mistaking the figure background for a baked-in tile background.

`gui.jobs.submit_saved` uses the same serial subprocess queue, progress and
cancellation as full runs. GUI validation previews a stage plan. GUI edits refine
the existing calculation; duplication is explicit. `run_updates.RunUpdate` holds
a per-calculation lock and owns a marked `.phmap-updates/<id>/` transaction with
private worker output. Successful updates preserve applicable verified raw and
scientific artifacts, relocate manifest artifact pointers, and replace the run
directory using journaled renames. Prior outputs are discarded, not versioned.
Cancellation/failure leaves the old run untouched. Startup recovers abandoned
transactions; a completed install with interrupted cleanup is finalized instead
of rolled back. Exact executed argv are retained as provenance, never replayed.

Queue records overlay pending configuration/progress on the existing history ID;
downloads keep showing the last good result until commit. Completion is exposed
only after transaction cleanup and lock release. Manifest digests and preview
revision URLs refresh so repeated updates do not use stale settings/images.
CLI `compose`/`render --in-place` share the same replacement primitive; omission
retains explicit derivative behavior. Selecting saved rows/pH columns is explicit; automatic failed-stage
resume and stage-specific APBS recovery remain future work.

## Cell-level parallel execution

`parallel.run_cells` uses a bounded `ThreadPoolExecutor` to orchestrate separate
scientific subprocesses, not to run CPU-heavy Python calculations. The full
workflow and saved-artifact renderer share this scheduler. Each protein/pH
cell owns its working directory, logs, artifacts and runner; stages within a
cell remain sequential. Results retain input order. Composition stays serial
to preserve the compositor's bounded tile-loading behavior.

`WorkflowOptions.workers` validates 1–32 workers. Effective concurrency is the
minimum of the requested budget, available logical CPUs/affinity, and cell
count. The CLI defaults to 1; `auto` and the GUI recommend at most 2 because
concurrent APBS instances multiply memory demand. This policy does not reserve
cores or estimate APBS memory. `configuration.execution` records requested and
effective concurrency separately from rendering compatibility settings.

Only parallel executions apply child-local OpenMP/BLAS/Accelerate thread
limits and emit PyMOL `set max_threads, 1`. `CommandRunner` merges environment
overrides into a copy, never mutating the parent environment. Single-worker
execution preserves existing backend threading behavior. The PyMOL setting is
defined in its [upstream setting registry](https://github.com/schrodinger/pymol-open-source/blob/master/layer1/SettingInfo.h).

`RunManifest` predeclares ordered tasks and serializes mutations/writes with a
reentrant lock. On failure the scheduler stops submission, signals sibling
cells to stop between stages, drains running commands, and propagates the
original exception. No tool continues after an in-place update is discarded
or committed. GUI cancellation keeps the existing process-group/tree policy
and terminates all parallel children together.

Regression tests use barriers to prove overlap, check deterministic ordering
and isolated files, stress concurrent manifest writes, preserve original
failures, and exercise saved rendering, worker arguments, GUI restoration,
and cancellation. A native four-pH smoke run and an in-place parallel re-render
have also been verified locally on Apple Silicon; this does not establish
native parallel verification on other target platforms.

## GUI development

`pixi run gui` launches a FastAPI server on `127.0.0.1:8765` and opens the local
page. `pixi run phmap gui --no-browser` starts the same interface without opening
a window. The CLI's existing `run`, `validate`, and `doctor` commands retain
their arguments and behavior.

The interface consists of a packaged Jinja template, CSS, and a small JavaScript
client; no Node build or CDN is required. The client submits multipart uploads
and validated settings, then receives manifest snapshots through Server-Sent
Events. GUI and CLI use the same validation models and scientific workflow.

HTML, CSS and script URLs share a content-derived revision, including during
unreleased source edits. HTML/API/static responses use `Cache-Control: no-store`
so an updated template does not reuse pre-wizard scripts or styles at unchanged
URLs. The revision covers the template and all static files, not just the Python
release version. A small first-loaded startup guard shows a reload/restart
message when scripts fail to initialize or current styles are missing, instead
of leaving apparently live buttons without handlers. Native reload links remain
usable without the main script. Restart the server after changing Python code;
refreshing cannot reload an already-running Python process.

`gui.preferences.PreferencesStore` stores an explicit allowlist of reusable
settings in one per-user, schema-versioned JSON file. `phmap.paths` shares the
stable application-data directory with the desktop launcher. Atomic replacement
and a cross-process lock protect saves across GUI workspaces. Invalid or future
schemas fall back visibly without being overwritten except after explicit reset.
`PHMAP_PREFERENCES_PATH` overrides the location; tests isolate it automatically.
The same request models and core options validate preferences, excluding input
files, pH selections, session identity, and executable paths. Automatic foreground
contrast is a GUI preference resolved into a concrete color at submission.

The first-launch dialog is optional and remembers save/skip. The three-step
wizard separates per-calculation structures/pH from reusable rendering defaults.
`static/preferences.js` shares control serialization between the per-run and
global editors; the global editor clones only override controls, never files or
session fields. New forms use global defaults, while history editors restore
their own complete settings. Saving defaults does not rewrite an existing form
or run. The CLI does not implicitly load GUI preferences.

An independent progress controller follows the editing calculation while the
selected history stream can browse another result. Cancelled unstarted GUI jobs
use a pinned submission digest and a fresh private input snapshot to resume
under the same ID, without requiring a nonexistent scientific manifest.
Resuming retires the previous snapshot only after the replacement queue record
is durably saved. Cleanup checks ownership, hashes, provenance and camera bytes;
additional user files, changed inputs and symbolic links are left untouched.
Small cleanup tickets survive temporary deletion failures and are retried on
restart. Failed staging retains the old setup and removes unpublished copies.
Startup discovers owned orphan transaction journals as well as GUI records;
non-plan CLI artifact operations recover before attempting to load a missing
source. Active writer locks prevent recovery from discarding another process's
pending work. No recovery runs for a CLI `--plan` request.

Core execution and presentation settings live in `phmap.configuration`; both
interfaces validate them before creating a run or launching scientific tools.
Browser request models live in `phmap.gui.schemas`, separate from HTTP handlers.
Both interfaces reject invalid local files, names, and camera settings before
fetching remote inputs. The browser keeps its own upload limits and form defaults;
file validation, hashing, and job staging run outside the async request loop. Stage records are
created when work starts and finalized on success, failure, cancellation, or
interruption, including timeout and invalid-artifact failures.

Structure inputs accept `.pdb`, `.cif`, and `.mmcif`. `phmap.structures` fetches
only validated PDB IDs over HTTPS from the official RCSB archive, bounds response
size/time, parses complete coordinate documents, and verifies cached files against
their requested ID and source metadata. Shared `phmap.locking` file locks serialize
each cache entry across threads and processes without blocking unrelated IDs.
Refreshes stage both content and provenance before publication and restore the
previous content if provenance publication fails.
`phmap.structure_formats` uses the pinned Gemmi dependency to validate mmCIF
and prepare a checked PDB bridge for PDB2PQR 3.7.1; this avoids its native CIF
reader's missing-header-field failures. Original documents and derived backend
inputs are hashed and retained separately. A round-trip audit rejects changes to
atom identities, counts, or coordinates beyond PDB precision before writing the
bridge. No chain renaming or model merging is implicit.
The [tutorial](tutorial.md#current-scientific-backend-limits)
documents the remaining backend representation limits.

The DOM-independent progress controller lives in `static/run-selection.js`.
Its regression checks cover rapid selection changes, stale responses/events,
and reconnect handling. With Node.js installed, `pytest` runs these checks too;
Node.js is optional for local Python-only development and is not needed to build
or run pHmap. Run just the controller checks with:

```console
node tests/frontend/run-selection.mjs
node tests/frontend/protein-selection.mjs
node tests/frontend/render-settings.mjs
node tests/frontend/template-settings.mjs
node tests/frontend/saved-runs.mjs
node tests/frontend/request-races.mjs
node tests/frontend/preferences.mjs
```

For full-page DOM/API regression tests, install the development-only JavaScript
dependencies with a supported Node.js version (CI uses Node 26):

```console
npm ci --ignore-scripts --no-audit --no-fund
pixi run --locked test tests/test_ui_assets.py tests/test_ui_integration.py
```

These checks load the actual rendered template, styles and complete scripts in
their real order. Button clicks make requests to the actual FastAPI application
and queue, with offline scientific-tool and RCSB fixtures. They cover initial
setup save/skip/close/Escape, preferences/reset, wizard navigation, PDB fetching,
uploads/drop/reordering/removal, camera/ligand reuse, validation, calculation,
figure edits, re-rendering, duplication, queued/active cancellation, setup resume,
history, logs, downloads, startup failure, stale scripts, missing styles and
outdated-server recovery.
Native dialog presentation, file selection, scrolling and EventSource transport
are simulated; these are not screenshot or real-browser layout tests. Pixel,
keyboard/focus, native file-dialog and browser-engine checks still need an actual
browser. The full-page tests run in desktop CI on every platform; local Python-only
development can omit them when Node/development packages are unavailable.
The npm packages are not required to build or run pHmap and are not bundled in
desktop releases. No production frontend build or CDN dependency was introduced.

History reads lightweight summaries, while detailed run views discover images
and logs only when requested. Desktop environment installation and activation
live in `phmap.desktop_runtime`, separately from the launcher window and local
service lifecycle. Its tests simulate setup without downloading a toolchain.

History templates restore validated settings through `gui.run_templates`, never
by replaying saved worker arguments. `RunInputs` retains optional validated
`PHRange` metadata; the CLI worker and shared `build_run_configuration` preserve
the original range triple as well as the calculated values. Older metadata falls
back to explicit values. GUI submissions persist a full typed configuration and
input checksums before queueing, permitting templates of unstarted cancelled jobs.
Execution manifests take precedence over submission snapshots; damaged manifests
do not silently fall back to another source. All input reads are confined to the
run's saved input folder (or its private queued-input folder), with hashes checked
again during bounded staging copies. Full recalculation uses a private workspace
and never reuses calculated artifacts. **Edit calculation** commits it into the
same ID; **Duplicate calculation** creates a new ID and records `based_on`.

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

## Preview the documentation website

The public documentation is built with [Zensical](https://zensical.org/) from
Markdown files in `docs/`. To preview changes locally without installing it
into the project environment, run from the repository root:

```console
uvx --from "zensical==0.0.69" zensical serve
```

Then open <http://127.0.0.1:8000>. Build the same site as the GitHub Actions
workflow with:

```console
uvx --from "zensical==0.0.69" zensical build --clean --strict
```

The generated `site/` directory is local build output and is not committed.
The workflow builds every pull request and merge queue group, and publishes
successful pushes to `main` or `master` through GitHub Pages.

Documentation-only updates skip the scientific tests and desktop builds in the
separate desktop workflow. Its four required check names complete using
lightweight Ubuntu jobs. Application, test, build, and dependency changes still
run full native CI; version tags and manual desktop workflow runs always do too.
See the [release guide](releases.md#publish-through-github) for the exact rules.

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
