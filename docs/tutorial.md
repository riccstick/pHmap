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
