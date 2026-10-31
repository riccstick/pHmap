# Tutorial: run a pH series

This example calculates maps for pH 3 through 8, in steps of 1. The desktop
application and command-line interface use the same local workflow.

## With the desktop app

1. Download and extract the build for your operating system from
   [Downloads](downloads.md).
2. Start **pHmap**. The first launch downloads and installs the scientific
   environment; allow a few minutes and keep the computer online.
3. Add local `.pdb`, `.cif`, or `.mmcif` files, or enter a PDB code such as
   `3SQR` and click **Add PDB entry**. Add variant files to the same comparison.
4. Choose a pH range with start `3`, stop `8`, and step `1`.
5. Validate the inputs, then start the calculation. The app shows progress and
   provides the final image and run files when it completes.

Use [release troubleshooting](releases.md#troubleshooting) if first-run setup
fails, especially on a managed company network.

## With the command line

From a checkout with the Pixi environment installed, run one protein:

```console
pixi install
pixi run phmap run example/mutant2.pdb --ph-range 3 8 1 --runs-dir runs
```

Run several proteins in one ordered figure by listing their structure files:

```console
pixi run phmap run \
  example/protein.pdb example/mutant1.pdb example/mutant2.pdb \
  --ph-range 3 8 1 \
  --view example/set_view.txt \
  --runs-dir runs
```

The pH range is inclusive, so this requests pH 3, 4, 5, 6, 7, and 8. Each
invocation creates a separate run directory under `runs/`; the composed image
is saved at `output/pHmap.png` inside that run. The run's `manifest.json` and
logs record the settings and calculation stages.

For an interactive local interface from a checkout, use:

```console
pixi run gui
```

It opens the interface at <http://127.0.0.1:8765>. For environment setup and
more command-line options, see the [development guide](development.md).

## Run calculations and rendering in parallel

The **Parallel cells** field controls how many protein/pH cells can run at once.
For example, two proteins at six pH values create 12 independent cells. Each
cell runs PDB2PQR, APBS, and PyMOL in order, while other cells can run alongside
it. **Re-render** uses the same setting for parallel PyMOL processes. The final
Python composition remains serial, and the GUI still queues one calculation at
a time. Protein rows and pH columns keep their requested order regardless of
which cell finishes first.

The GUI starts with up to **2** workers, limited by available logical CPUs.
Start there, or use **1** for large proteins or limited memory: every concurrent
APBS process needs its own memory. More workers are not always faster. The
requested count accepts 1–32; execution also caps it at the available CPU count
and the number of selected cells. This is a concurrency limit, not CPU pinning.

The CLI keeps its sequential default. Add `--workers` to enable parallel work:

```console
pixi run phmap run example/mutant2.pdb --ph-range 3 8 1 --workers 2
pixi run phmap render runs/SOURCE_RUN --workers 2 --in-place
```

`--workers auto` chooses the same conservative default as the GUI. `--workers 1`
disables cell parallelism. Saved setups restore their requested worker count;
you can change it before recalculating or re-rendering. Validation reports the
effective concurrency. Figure-only edits do not run parallel scientific tools.

When cells run in parallel, pHmap sets common OpenMP/BLAS thread limits and
PyMOL's `max_threads` to 1 in each child process, to reduce nested CPU
competition. A single-cell execution leaves backend threading unchanged.
Requested/effective concurrency and this thread policy are saved separately
from scientific and render settings in `configuration.execution`.

If a cell fails, pHmap stops scheduling new cells and starting later scientific
stages. Commands already running finish under their normal timeouts before the
failure is reported. GUI cancellation terminates the owned worker and its
scientific children. Failed or cancelled in-place updates keep the last good
calculation unchanged.

## Reuse a calculation from history

1. Select a finished calculation in **Run history**.
2. Click **Edit calculation**. This loads the setup without starting any tools.
3. Adjust pH values, labels/order, rendering, or publication styling. You can
   remove saved structure rows and add new files or PDB entries.
4. Saved camera and shared ligand inputs are marked in the form. Upload a new
   file to replace one, or use **remove saved camera/ligand** to omit it.
5. Click **Rerun this calculation** to replace its current state after success.

The app uses hash-verified copies retained with the original run, not the
original upload paths or fresh archive downloads. Every scientific/rendering
stage runs again for **Edit calculation**; this is not scientific-stage resume.
The history entry, calculation ID and creation time stay the same. Previous
figures and outdated scientific files are replaced, not kept as versions.
Your original uploaded files are never modified. **Duplicate calculation**
loads the same setup but explicitly starts a separate calculation instead.

New runs retain the original pH range bounds and step, even when the stop is
not reached: `3–8` at step `2` remains that range, calculating `3, 5, 7`.
Older runs without the original range parameters load their exact ordered pH
values in **Specific values** mode instead of guessing a range.

A newly queued job saves its setup before execution, so it can be used as a
template after cancellation or an interrupted queue restart. Older cancelled
jobs without a saved setup have a disabled action with an explanation. Missing
or modified saved files produce a clear error; the app never silently substitutes
a different structure or replays executable arguments from a saved job. Runs
outside the GUI's current size/count limits cannot be loaded as templates.

**New calculation** clears the form and cancels pending setup/PDB requests;
late responses cannot repopulate the new setup.

## Edit figures and re-render saved runs

Select a terminal run in **Run history**, then choose an action:

| Action | Reuses | Runs again |
| --- | --- | --- |
| **Edit figure** | Saved PNG tiles | Python composition only; no scientific executables required |
| **Re-render** | Saved PQR structures and DX potentials | PyMOL and composition; no PDB2PQR or APBS required |
| **Edit calculation** | Saved raw structures and setup | Full calculation, including PDB2PQR and APBS |

Opening an editor does not start work. Adjust labels, order, font size, margins,
spacing, DPI, pH label precision, or legend visibility. Remove/reorder saved rows
and choose a subset/order of the **existing** pH values if desired. Re-rendering
also permits camera replacement/removal, surface/ligand representations,
resolution, potential colors/limit, ray tracing, and background changes.

The form prominently identifies the calculation being edited. Click **Validate**
to verify artifacts and display the stage plan, then **Update figure**,
**Update surface renders**, or **Rerun this calculation**. Each action updates
the same history item and output paths. The updated preview refreshes automatically.
After your first calculation starts, subsequent submissions also target that
calculation; choose **New calculation** to leave it and start a separate one.
Click **Done editing** when satisfied: the latest result remains visible and
saved, and the form leaves editing mode. You can reopen it later from history.
The confirmation makes clear that unsubmitted form changes are discarded;
apply them with **Update** or **Rerun** first if you want to keep them.

Workers build replacements in private temporary storage. Until success, the
last good figure remains available. A failed or cancelled attempt cannot
replace it. Successful updates discard the previous state; there is no undo
or automatic version history. A per-calculation lock rejects simultaneous edits,
and a small on-disk journal recovers interrupted installation on restart.

Equivalent CLI examples (replace `runs/SOURCE_RUN` with your run folder):

```console
pixi run phmap compose runs/SOURCE_RUN --font-size 32 --outer-margin 40 --plan
pixi run phmap compose runs/SOURCE_RUN --font-size 32 --outer-margin 40 --no-legend --in-place
pixi run phmap render runs/SOURCE_RUN --size 1000 --ramp-colors blue white red --in-place
pixi run phmap render runs/SOURCE_RUN --view example/set_view.txt --surface-mode 1 --in-place
pixi run phmap compose runs/SOURCE_RUN --protein-id mutant2 --label "Variant" --ph 7 --ph 5 --in-place
```

Settings inherit from the source unless overridden. Repeat `--protein-id` and
`--label` once per selected row and `--ph` once per selected column; omitted
selections retain the entire saved order. `--auto-view` removes a saved camera,
`--transparent` requests transparency, and `--preserve-ph-labels` restores exact
pH label spelling instead of fixed decimal places. `--ray` enables ray tracing
if the source disabled it. The GUI updates in place by default. The CLI requires
explicit `--in-place` permission to overwrite a calculation. Omit that flag for
a separate derivative; use `--output-dir NEW_DIR` for an exact destination or
`--runs-dir DIR` for its parent (default `runs`). Existing output destinations
are rejected; destination flags cannot be combined with `--in-place`.

Important limits:

- Composition cannot change tile size, camera, surface settings or ramp/limit:
  the legend must continue to describe the saved surfaces. Use **Re-render**.
- Transparent saved tiles can be placed on a different figure background.
  Opaque tile backgrounds are baked in and need re-rendering to change.
- New structures, ligands, force fields or pH values need a full calculation.
  Reuse never silently recalculates missing data or substitutes new input bytes.
- Artifact reuse does not require the original raw structures to remain present.
  The necessary saved PNG or PQR/DX files must match their manifest hashes.
  Paths are confined to the selected run, and archived PML/commands are never executed.
- Failed/interrupted runs can supply a selected subset of completed artifacts.
  A cell with completed PDB2PQR/APBS but a failed renderer can be re-rendered;
  missing PQR/DX cannot. This is explicit stage/cell selection, not automatic resume.
- In-place edits retain verified raw inputs, PQR/DX and applicable stage logs for
  the selected cells, allowing **Edit figure → Re-render → Edit calculation**
  without losing their scientific data. Removed rows/columns are dropped. A full
  recalculation replaces the scientific state, including obsolete pH files.
- Explicit separate CLI derivatives retain only artifacts they use. To re-render
  a composition-only derivative, use its scientific source instead.

The current manifest records `last_update` (operation and previous manifest hash),
the current configuration, stage plan, artifact checksums and producer versions.
This is current-state provenance, not retained previous versions. Explicit
separate derivatives still record `based_on`. Reused PQR/DX keep their scientific provenance;
the installed PDB2PQR/APBS versions are irrelevant because neither executes.
Re-rendering always invalidates old tiles and records the currently used PyMOL
version. A source changed after editor loading/queueing fails verification.

## PDBx/mmCIF and PDB IDs

Text **PDBx/mmCIF** files normally use `.cif` or `.mmcif`. Both are accepted,
alongside legacy `.pdb` files. Compressed files must first be extracted;
BinaryCIF (`.bcif`) is not currently supported.

For a reference-versus-variants comparison in the GUI:

1. Enter `3SQR` in **Fetch from RCSB PDB** and click **Add PDB entry**.
2. Add your local variant `.pdb`, `.cif`, or `.mmcif` files. Later file selections
   append to the list; they do not remove the fetched reference.
3. Rename the rows, for example **Wild type**, **Variant 1**, and **Variant 2**.
4. Reorder the rows if desired, choose pH `3`–`8`, validate, and calculate.

The equivalent CLI invocation is (substitute your variant file paths):

```console
pixi run phmap run --pdb-id 3SQR variant1.cif variant2.pdb \
  --label "Wild type" --label "Variant 1" --label "Variant 2" \
  --ph-range 3 8 1
```

Repeat `--pdb-id` for multiple reference entries. CLI ordering is fetched entries
in flag order, then local files in positional order; labels and explicit
`--protein-id` values follow that same order. ID-only runs are supported:

```console
pixi run phmap run --pdb-id 1CRN --ph-range 3 8 1
```

Downloads use the official [RCSB file service](https://www.rcsb.org/docs/programmatic-access/file-download-services)
and retrieve the deposited **asymmetric unit**, not an automatically expanded
biological assembly. Four-character IDs such as `3SQR` and extended IDs such as
`pdb_00003sqr` are supported. Only public structure IDs are requested from RCSB;
local variant files and calculations stay on your computer. Adding files does
not generate mutations or align structures automatically.

The CLI cache defaults to `runs/.structures`; the GUI stores downloads under
its run directory's `.gui/structures`. Cache reuse verifies the requested ID and
content checksum and does not need internet access. Downloads are parsed before
caching; failed refreshes retain the previous cached structure and provenance.
Use `--structure-cache-dir PATH` to change
the CLI location, or `--refresh-structures` to download current archive copies.
The run manifest records the ID, download URL, timestamp, and checksum.

### Current scientific-backend limits

PDB2PQR 3.7.1's native mmCIF reader fails on valid files with missing optional
metadata. pHmap uses Gemmi to prepare a **backend-only PDB intermediate**, while
preserving the original mmCIF bytes. The manifest records both files, their
checksums, and the preparation software/version. This is an input compatibility
step, not a change to the protonation or electrostatics method.

The current backend requires a single coordinate model, one-character author
chain IDs, residue names of at most three characters, residue numbers from
`-999` to `9999`, and at most 99,999 atoms with representable atom identifiers
and coordinates. Inputs outside those limits are rejected, not silently
renamed, truncated, or merged. PDB intermediate coordinates use 0.001 Å
precision; the original mmCIF's precision is retained in the saved source file.
Each conversion is checked for unchanged atom identities and coordinates within
that precision. Non-ASCII descriptive headers are replaced with ASCII placeholders
only in the backend intermediate; the saved mmCIF retains the full metadata.
Nonstandard residues, glycans, and metal cofactors may also require additional
preparation or force-field parameters, just as with PDB inputs.

If fetching fails, check the entry code, internet access, VPN/proxy, and trusted
certificates—or download the structure manually and add it as a local file.
Do not disable certificate verification to work around company-network errors.

## Advanced surface and ligand rendering

Open **Advanced surface & ligand rendering** below the figure settings in the
local browser interface. These settings apply to every protein/pH tile and are
saved under `configuration.render` in `manifest.json`, including default values.
Queued GUI jobs retain the same settings when passed to the CLI worker.

The equivalent CLI controls are:

| Option | Default | Purpose |
| --- | --- | --- |
| `--surface-mode 0..4` | `0` | Choose which atoms contribute to the surface. |
| `--surface-solvent` / `--no-surface-solvent` | Off | Switch between solvent-accessible and solvent-excluded geometry. |
| `--surface-ramp-above-mode` / `--no-surface-ramp-above-mode` | On | Control PyMOL's above-surface potential sampling. |
| `--ligand-representation sticks\|lines\|dots\|spheres\|none` | `sticks` | Choose an additional representation for retained HETATM atoms. |

Surface modes follow [PyMOL's definitions](https://github.com/schrodinger/pymol-open-source/blob/master/layer2/RepSurface.h):

- **0:** respect atom flags (normally excludes HETATM atoms).
- **1:** include all atoms.
- **2:** include heavy atoms only (exclude hydrogens).
- **3:** include surface-visible atoms only.
- **4:** include surface-visible heavy atoms only.

pHmap initially shows the whole molecule's surface, so modes 3 and 4 do not add
a custom atom-selection interface. The two switches remain independent:
[PyMOL's APBS interface](https://github.com/schrodinger/pymol-open-source/blob/v3.1.0/data/startup/apbs_gui/__init__.py)
normally disables above-surface sampling for solvent-accessible geometry.
For that combination, use:

```console
pixi run phmap run example/mutant2.pdb --ph-range 3 8 1 \
  --surface-mode 1 --surface-solvent --no-surface-ramp-above-mode \
  --ligand-representation spheres
```

These options do **not** change protonation, APBS calculation parameters, or
the force field. The ligand representation applies to `HETATM` atoms retained
in the prepared PQR, not specifically to the file uploaded as a shared ligand.
Missing or discarded ligands cannot be recovered by changing representation.
`none` (GUI: **Hidden**) suppresses the extra representation; it does not remove
those atoms from the calculation or an inclusive surface.

Shared MOL2 parameterization remains experimental. In particular, the legacy
`example/protein.pdb` plus `example/ligand.mol2` FAD example currently fails in
PDB2PQR 3.7.1's ligand parameter assignment (`ValueError: 'P' is not in list`),
before rendering starts. The native rendering smoke uses a separate prepared
synthetic PQR fixture; it does not validate general ligand parameterization.

## Publication styling

Open **Publication styling** in the browser interface to adjust labels and
layout for a new run. The same controls are available in the CLI:

| Option | Default | Range / effect |
| --- | --- | --- |
| `--font-size` | `22` | Label size in pixels, 1–256. |
| `--outer-margin` | `24` | Outer margin in pixels, 0–1024. |
| `--column-gap` | `16` | Gap between pH columns in pixels, 0–1024. |
| `--row-gap` | `16` | Gap between protein rows in pixels, 0–1024. |
| `--label-gap` | `16` | Gap between labels/grid and legend in pixels, 0–1024. |
| `--legend` / `--no-legend` | On | Show or hide the shared potential legend. |
| `--ph-label-precision` | Preserve input | Display 0–6 decimal places. |
| `--foreground` | `black` | Label and legend-border color. |
| `--background` | Transparent in CLI | Figure/tile background; GUI defaults to white. |
| `--dpi` | `300` | PNG resolution metadata, including the final composed image. |

The GUI provides automatic label contrast or explicit black, white, and dark gray
presets. Its existing background selector remains under **Figure & calculation
settings**. CLI color strings are validated; label colors may also use hex such
as `--foreground '#555555'`.

```console
pixi run phmap run example/mutant2.pdb --ph 5.125 --ph 7.5 \
  --font-size 32 --outer-margin 32 --column-gap 24 --row-gap 32 \
  --label-gap 20 --ph-label-precision 1 --dpi 450 --background white
```

This labels the columns **5.1** and **7.5**, but still calculates at **5.125** and
**7.5**. Formatting uses decimal round-half-to-even and never changes scientific
pH values. A precision that merges distinct columns (for example, `5.11` and
`5.14` at one decimal place) is rejected before calculations or downloads start.

The compositor uses
[Pillow's bundled default font](https://pillow.readthedocs.io/en/stable/reference/ImageFont.html#PIL.ImageFont.load_default)
(Aileron Regular with limited character coverage when FreeType is available),
not an operating-system font path. Font-family selection, bold/italic variants,
custom alignment, and axis lines are not exposed yet.

Large labels expand row/column layout without rescaling the rendered surface
tiles. A wide legend also expands the canvas to avoid clipping. Final figures
are limited to 100 million pixels; reduce tile size, spacing, font size, or the
number of cells if this limit is reached. Composition loads one tile at a time.
Text placement has been corrected, so final figure dimensions/pixels may differ
slightly from older releases even with default settings; scientific artifacts
and render settings are unchanged by publication styling.

Effective values, font source, and displayed pH labels are recorded under
`configuration.composition` in the manifest; `configuration.ph_values` retains
the exact calculation values. Use **Edit figure** or `phmap compose` to apply
these settings to verified saved tiles. GUI edits replace the current figure;
CLI edits require `--in-place` to do so. See [saved-run editing](#edit-figures-and-re-render-saved-runs).
