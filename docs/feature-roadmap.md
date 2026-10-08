# Feature parity and implementation roadmap

The Python app already replaces the core local calculation workflow, but it
does not yet replace every feature of the Bash program. The most important
remaining capabilities are activity plots, editing figures without repeating
calculations, advanced surface controls, and publication styling.

This is the feature snapshot for 8 October 2026. Priorities below are proposed
implementation order, not committed release dates. The legacy executable code
was removed on that date after this feature audit. Missing and partial features
remain deferred Python work; their removal from the old implementation does not
make them available in the new app.

## Features available today

Both the CLI and local browser interface support multiple PDB files, ordered
pH values or an inclusive range, an imported 18-number PyMOL camera view, and
an experimental shared MOL2 ligand. The workflow runs PDB2PQR with PROPKA,
APBS, and headless PyMOL, then composes an ordered PNG grid in Python.

Tile size, DPI, background, potential limit, and ramp colors are configurable.
The CLI accepts custom ramp colors and a foreground color; the browser form
currently offers two ramp presets and three background choices. Runs retain
scientific artifacts, logs, input and output hashes, numerical summaries,
tool versions, and a JSON manifest.

The GUI adds uploads, editable protein labels and row order, validation,
progress, a serial queue, cancellation, history, and downloads. A desktop
launcher and native GitHub Actions build matrix are implemented. Configuring
the matrix does not establish that every platform has passed; public signing
and notarization remain separate release work.

## Legacy feature mapping

Here, **available** means exposed through the current application, **partial**
means some support exists only in a lower-level Python component or the user
controls are incomplete, and **missing** means the capability has no current
application workflow. Boolean legacy flags also have corresponding `--no-`
forms; the table groups both forms together.

| Legacy feature or arguments | Current Python status | Remaining work |
| --- | --- | --- |
| Positional PDB inputs, `--ph_range` | Available | Use `--ph-range START STOP STEP`; explicit `--ph` values are also supported. |
| `--set_view` | Available | Use `--view`; interactive editing is a new enhancement, not a lost legacy feature. |
| `--ligand` | Available, experimental | Shared ligand input exists; strengthen scientific fixtures before claiming general ligand support. |
| `--size`, `--background` | Available | CLI supports validated colors; GUI has presets. Old ImageMagick color names are not guaranteed to translate. |
| `--pymol_level`, `--pymol_ramp_colors` | Available | Use `--level` and `--ramp-colors`; custom colors still need a browser control. |
| `--activity`, `--activityplot`, `--activity_dataformat` | Missing | Import activity, derive pH values, and optionally draw lollipops with errors. |
| `--activity_radius`, `--activity_lollipop_circle_color`, `--activity_lollipop_circle_stroke` | Missing | Control activity marker size, fill, and outline. |
| `--activity_lollipopbar_color`, `--activity_lollipop_errorbar_color` | Missing | Style stems and error bars separately. |
| `--grid_linewidth`, `--grid_place`, `--grid_font_size` | Missing | Control activity ticks, inward or outward placement, and scale labels. |
| `--update_axis` | Missing | Recompose saved tiles without PDB2PQR, APBS, or PyMOL. |
| `--update_rendering` | Missing | Render saved PQR and DX artifacts again without PDB2PQR or APBS. |
| `--pymol_surface_mode`, `--pymol_surface_solvent`, `--pymol_surface_above_mode` | Partial | Renderer supports them; workflow, manifest configuration, CLI, and GUI do not expose them. |
| `--pymol_ligand_representation` | Partial | Renderer supports sticks, lines, dots, spheres, or hiding the representation; application uses the default. |
| `--pymol_ramp` | Partial | `CompositionSettings.show_colorbar` exists; application always shows the shared legend. |
| `--digits` | Missing as a display control | Decimal pH values are preserved, but there is no separate label precision option. |
| `--axis_font`, `--axis_font_size`, `--axis_font_weight`, `--axis_font_stretch`, `--axis_font_style` | Partial | Compositor has a font size setting, but the application does not expose it; font family and variants are missing. |
| `--xalign`, `--yalign` | Missing as controls | pH labels are centered and protein labels are right-aligned; users cannot change alignment. |
| `--axisline`, `--axisline_strokewidth`, `--axisline_color`, `--axisline_offset` | Missing | Current grid has labels and a legend, not the configurable legacy axis lines. |
| Saved `.pse` sessions in the local Bash workflow | Missing | Retained PML, PQR, and DX files are useful, but are not a saved PyMOL session. |
| Bash completion from `pHmap.m4` | Missing | Add optional completion for the Python CLI; do not retain Argbash as a runtime dependency. |
| Submitted command recording | Replaced | Manifest records validated scientific/render settings and executed backend commands; a portable replay configuration is a separate enhancement. |
| Script Server uploads and result access | Replaced locally | Local GUI provides uploads, queue, progress, history, and downloads; remote hosting is outside current scope. |

The new CLI is not syntax-compatible with every historical Bash invocation.
Keep its existing arguments stable, and document translations instead of
silently promising old flag aliases.

## Activity data and lollipop plots

Priority: high. Users should be able to compare measured residual activity
with surface potential for each protein and pH, with optional uncertainty.

The legacy table is whitespace/tab-separated. In `xy` mode, each row contains
`pH activity1 activity2 ...`. In `xydy` mode, it contains
`pH activity1 error1 activity2 error2 ...`; protein order determines the column
mapping. For example, the first row of the retained
[activity input](reference/activity-xydy.txt) describes
three proteins at pH 4.0: `4.0 0.0 0.2 1.0 0.2 1.0 0.2`.

Proposed implementation: normalize imports into typed records keyed by protein
ID and decimal pH. Preserve both legacy formats; a header-based CSV/TSV format
can provide explicit protein IDs for new users. Keep plotting in the Python
compositor, without reintroducing ImageMagick or shell parsing.

Acceptance criteria:

- Validate column counts, protein mapping, finite values, non-negative errors,
  duplicate pH records, and Windows line endings. State the activity unit and
  uncertainty meaning; never silently normalize or clip supplied data.
- Activity files can supply the pH list even when plotting is disabled. Reject
  conflicting pH sources or require an explicit matching rule.
- Plot one correctly aligned activity marker per protein/pH cell, with optional
  error bars, a labeled percentage scale, and configurable marker/stem colors.
- Reordering proteins in the GUI preserves their activity assignments; report
  missing or unmatched measurements before starting expensive calculations.
- CLI and GUI share the same parser and models. Stage and hash the activity
  input, record its interpretation and styling in the manifest, and test both
  formats with one and multiple proteins, zero activity, and error bars.

The activity example and [historical activity figure](reference/pHmap-activity.png)
are retained in `docs/reference/` until this feature has documented fixtures.
The PNG is not a numerical golden, and the table is not a supported input to the
current app.

## Advanced surface and ligand controls

Priority: high; a relatively small wiring task compared with activity plotting.
Users should be able to select surface modes 0 through 4, solvent-accessible
surface display, ramp-above mode, and ligand representation.

`PyMOLRenderOptions` and `build_pymol_script` already implement these controls.
Extend `WorkflowOptions`, manifest configuration, CLI parsing, GUI settings,
and worker argument construction to pass them through consistently. Avoid
creating a second renderer or accepting arbitrary user PML.

Acceptance criteria:

- Preserve the current default rendering when no new controls are selected.
- All settings can be selected in CLI and GUI and survive worker serialization.
- Invalid modes and representations fail during validation, before calculation.
- Script tests check every mode and representation; scientific smoke fixtures
  include a protein and a representative ligand-bearing input.
- Record these settings in manifests and include them in future render reuse
  decisions. A shared ligand remains explicitly experimental until validated.

## Publication styling and figure editing

Priority: high. Users should be able to improve labels and layout after an
expensive run without repeating scientific calculations.

Expose the existing compositor settings first: font size, margins, gaps, and
legend visibility. Then add display-only pH precision, font selection and
variants, label alignment, optional axis lines, and activity tick styling.
Use a documented font fallback or a redistributable bundled font so desktop
results do not depend on an unavailable Helvetica installation.

Acceptance criteria:

- Labels, spacing, foreground/background colors, and legend visibility are
  available through shared CLI/GUI settings and recorded in the manifest.
- pH label precision changes presentation only, never the pH sent to PDB2PQR.
  Reject or warn about distinct pH columns that become identical labels.
- An edit action composes a new figure from verified saved tiles and new
  labels/activity settings without launching any scientific executable.
- Output is a new run or explicitly versioned derivative linked to its source;
  the original figure and provenance remain intact.
- Tests verify cell order, label placement, transparency, legend consistency,
  invalid fonts/colors, and missing or incompatible tiles.

Exact pixel parity with the legacy layout is not required. Preserve useful
controls, and approve visual changes independently from scientific changes.

## Render reuse and selective reruns

Priority: high after the figure editing foundation. Users should be able to
change camera, surface representation, colors, or resolution without rerunning
protonation and electrostatics. Legacy `--update_rendering` supplied this
capability, but relied on files already present in the working directory.

Proposed implementation: an explicit source-run input with separate compose,
render, and full-calculation operations. Add resume of failed or interrupted
cells only after artifact verification and stage reuse are reliable; automatic
resume was not a legacy feature.

Acceptance criteria:

- Compose uses saved tiles; render uses saved PQR/DX files; full calculation
  reruns protonation, electrostatics, rendering, and composition.
- Check artifact hashes and compatibility. Changes to PDB, ligand, pH, force
  field, or relevant backend versions invalidate scientific-stage reuse.
- Rendering changes invalidate tiles but can retain compatible numerical data;
  presentation-only changes do not invalidate numerical data or tiles.
- Show the planned reused and rerun stages before execution. Missing/corrupt
  artifacts produce a clear error or an explicitly requested recalculation.
- Derivative runs record source provenance, reused artifacts, and new settings.
  Fake-backend tests prove which tools were and were not called; queue and
  cancellation behavior remain the same as normal GUI runs.

## PyMOL sessions and shell completion

Priority: medium for sessions, low for completion.

The local Bash calculation saved a `.pse` session for each protein/pH cell;
the Script Server variant did not. Add optional session export to the renderer,
retain the potential map and molecule with the selected camera and styling,
and offer the file alongside the PNG in the GUI. Test the saved session with
PyMOL, record its hash, and avoid making large sessions mandatory for all runs.

Provide optional Bash/Zsh/PowerShell completion for current Python subcommands,
flags, and file paths if command-line convenience warrants it. Completion must
not change CLI behavior or require Argbash, a server, or elevated installation.

## Enhancements beyond legacy parity

These are useful next steps, not features already present in the Bash code:

- Interactive local camera preview: rotate a protein and apply a validated
  common camera to the grid. The viewer must work without a CDN, and matching
  browser/PyMOL camera conventions needs explicit verification. Structure
  alignment is separate from merely sharing a camera.
- Saved project settings and a rerun action: reload protein ordering, pH
  selection, camera, and styling from history or a portable configuration.
  Validate input availability and hashes before reusing them; distinguish
  this from automatically resuming an interrupted job.
- Per-protein ligand assignment and better structure diagnostics: the domain
  model can hold individual ligands, but CLI/GUI submissions currently use a
  shared ligand. Surface warnings about unsupported or incomplete inputs before
  running backends rather than silently changing the scientific model.
- Public desktop distribution: signing/notarization, clean-machine testing on
  every target, and clearer first-install recovery. Fully offline installers
  and automatic updates are optional future scope; see [desktop releases](releases.md).

Remote execution, multi-user accounts, and rebuilding Script Server are not
part of this local-first roadmap. Additional export formats can be considered
later; the legacy final figure was PNG, so they are not parity requirements.

## Suggested implementation order

1. Expose existing advanced render controls and legend/font-size settings.
2. Add the activity model, import validation, and Python lollipop composition.
3. Add publication styling and safe composition from an existing run.
4. Add rendering from verified PQR/DX artifacts, then explicit selective resume.
5. Add optional PyMOL sessions, followed by camera preview and saved projects.
6. Complete release signing and platform verification before polished public
   distribution; shell completion can be added independently.

Every implemented feature should use the shared Python workflow, retain the
CLI, expose suitable GUI controls, and include validation and provenance tests.

## Legacy cleanup record

- [x] Retain all missing and partial features above as deferred Python work;
  removing the unused implementation does not require full legacy parity.
- [x] Preserve the activity table and historical surface/activity figures in
  `docs/reference/`, with their format and scientific limitations described.
- [x] Keep the original citation and license. Preserve PDB, MOL2, and camera
  fixtures; do not delete user runs, environments, or generated results.
- [x] Remove `pHmap`, `pHmap.template`, `pHmap.m4`, and the three files in
  `Script-Server/`. Update live documentation and asset links, and use a
  Python-generated illustration in the README.
- [x] Verify tests, lint, type checks, a real scientific smoke run, and package
  contents; no supported entry point or build may depend on the deleted code.

Cleanup verification passed locally on Apple Silicon macOS: 85 tests, Ruff,
Mypy, real two-pH scientific runs, source/wheel builds and content checks, and
23 local documentation links. The source package retains the reference assets;
the wheel contains the CLI, desktop launcher module, and GUI assets without
historical reference data or executable legacy code.

Full legacy parity is not mandatory for removing an unused implementation, but
deferred features must not disappear from the roadmap or be described as
already implemented. The removed code is recoverable from Git history; the
reference data remains available in this checkout.

## Implementation references

Legacy behavior came from the removed `pHmap.template`, its generated `pHmap`,
`pHmap.m4`, and the Script Server template, generated script, and settings JSON.
The source snapshot is recoverable from Git revision
`08fe09f8251ebbd06706179e47321a127b759012`. The generated Script Server script
also recorded the submitted command, although its template commented that
step out.

Retained assets are the [surface figure](reference/pHmap.png),
[activity figure](reference/pHmap-activity.png), and
[three-protein activity table](reference/activity-xydy.txt). They preserve visual
and input-format examples, not a reproducible scientific baseline.

Current application boundaries are [CLI parsing](../src/phmap/cli.py),
[workflow options and execution](../src/phmap/workflow.py),
[PyMOL settings and script generation](../src/phmap/backends/pymol.py),
[figure composition](../src/phmap/compose.py),
[GUI settings](../src/phmap/gui/app.py), and
[worker argument construction](../src/phmap/gui/jobs.py).
Keep scientific acceptance aligned with the
[baseline policy](development.md#scientific-baseline-policy), not historical
PNG pixel identity.
