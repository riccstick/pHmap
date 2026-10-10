# Tutorial: run a pH series

This example calculates maps for pH 3 through 8, in steps of 1. The desktop
application and command-line interface use the same local workflow.

## With the desktop app

1. Download and extract the build for your operating system from
   [Downloads](downloads.md).
2. Start **pHmap**. The first launch downloads and installs the scientific
   environment; allow a few minutes and keep the computer online.
3. Add one or more PDB files in the setup form.
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

Run several proteins in one ordered figure by listing each PDB file:

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
