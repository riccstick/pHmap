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

## Reuse a calculation from history

1. Select a finished calculation in **Run history**.
2. Click **Use as template**. This loads the setup without starting any tools.
3. Adjust pH values, labels/order, rendering, or publication styling. You can
   remove saved structure rows and add new files or PDB entries.
4. Saved camera and shared ligand inputs are marked in the form. Upload a new
   file to replace one, or use **remove saved camera/ligand** to omit it.
5. Click **Start calculation** to create a new run with a new name/ID.

The app uses hash-verified copies retained with the original run, not the
original upload paths or fresh archive downloads. The source figure, settings,
and files are not modified. Every scientific/rendering stage runs again; this
is **not** resume or composition-only figure editing. The new manifest's
`based_on` field identifies and hashes the source manifest or submission record.

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
the exact calculation values. These controls currently apply to **new runs**.
A saved-run editing action with verified tile reuse and separately versioned
figures remains planned; existing figures are not overwritten by a styling edit.
