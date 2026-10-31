"""Command-line interface for the local Python pHmap workflow."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from phmap import __version__
from phmap.backends import BackendError as ScientificBackendError
from phmap.compose import format_ph_labels
from phmap.configuration import WorkflowOptions
from phmap.doctor import run_doctor
from phmap.errors import PHMapError
from phmap.inputs import (
    load_pymol_view,
    parse_ph_selection,
    validate_mol2_path,
    validate_protein_names,
    validate_run_inputs,
    validate_structure_files,
)
from phmap.models import InputValidationError, RunInputs, structure_format
from phmap.parallel import recommended_workers
from phmap.saved_runs import SavedRun
from phmap.structures import fetch_structure, normalize_pdb_id, structure_source
from phmap.workflow import LocalWorkflow


def _add_input_arguments(parser: argparse.ArgumentParser, *, include_run: bool) -> None:
    parser.add_argument(
        "pdb",
        nargs="*",
        type=Path,
        metavar="STRUCTURE",
        help="local .pdb, .cif, or .mmcif structures; appended after any --pdb-id entries",
    )
    parser.add_argument(
        "--pdb-id",
        action="append",
        default=[],
        metavar="CODE",
        help="fetch an RCSB PDB entry as mmCIF, e.g. 3SQR; repeat for multiple entries",
    )
    parser.add_argument(
        "--structure-cache-dir",
        type=Path,
        default=Path("runs/.structures"),
        help="cache for downloaded structures (default: runs/.structures)",
    )
    parser.add_argument(
        "--refresh-structures",
        action="store_true",
        help="download fresh copies of --pdb-id entries instead of reusing verified cached files",
    )
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument(
        "--ph",
        dest="ph_values",
        action="append",
        metavar="VALUE",
        help="explicit pH value; repeat to preserve the desired column order",
    )
    selection.add_argument(
        "--ph-range",
        nargs=3,
        metavar=("START", "STOP", "STEP"),
        help="inclusive ascending pH range",
    )
    parser.add_argument("--view", type=Path, help="file containing an 18-number PyMOL view")
    parser.add_argument(
        "--ligand",
        type=Path,
        help="MOL2 ligand applied to each supplied protein (experimental)",
    )
    parser.add_argument(
        "--protein-id",
        action="append",
        help="explicit safe protein ID; repeat once per structure, fetched entries first",
    )
    parser.add_argument(
        "--label",
        action="append",
        help="display label; repeat once per structure, fetched entries first",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help=(
            "exact new run directory"
            if include_run
            else "show the intended run directory without creating it"
        ),
    )
    if include_run:
        _add_workflow_arguments(parser)


def _add_workflow_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--workers",
        type=_worker_count,
        default=1,
        metavar="N|auto",
        help="parallel protein/pH cells (1–32); auto selects up to 2 for RAM safety",
    )
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    parser.add_argument("--size", type=int, default=500, help="square tile size")
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--level", type=float, default=5.0, help="symmetric potential limit")
    parser.add_argument(
        "--ramp-colors",
        nargs=3,
        default=("red", "white", "blue"),
        metavar=("NEGATIVE", "NEUTRAL", "POSITIVE"),
    )
    parser.add_argument(
        "--background",
        help="opaque PyMOL/composition background; omit for transparency",
    )
    parser.add_argument("--foreground", default="black", help="label and border color")
    parser.add_argument("--font-size", type=int, default=22, help="figure label size, 1–256 px")
    parser.add_argument("--outer-margin", type=int, default=24, help="outer figure margin, px")
    parser.add_argument("--column-gap", type=int, default=16, help="gap between pH columns, px")
    parser.add_argument("--row-gap", type=int, default=16, help="gap between protein rows, px")
    parser.add_argument(
        "--label-gap",
        type=int,
        default=16,
        help="gap between labels and grid, px",
    )
    parser.add_argument(
        "--legend",
        dest="show_colorbar",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="show the shared potential legend (default: enabled)",
    )
    parser.add_argument(
        "--ph-label-precision",
        type=int,
        default=None,
        metavar="DIGITS",
        help="display 0–6 pH decimal places; omit to preserve input precision (display only)",
    )
    parser.add_argument("--force-field", default="PARSE")
    parser.add_argument("--timeout", type=float, default=1800.0, metavar="SECONDS")
    parser.add_argument("--no-ray", action="store_true", help="disable PyMOL ray tracing")
    parser.add_argument("--ray", dest="no_ray", action="store_false", help="enable ray tracing")
    parser.add_argument(
        "--surface-mode",
        type=int,
        choices=range(5),
        default=0,
        help="PyMOL atom inclusion mode, 0–4 (default: 0, respect atom flags)",
    )
    parser.add_argument(
        "--surface-solvent",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="show the solvent-accessible surface instead of solvent-excluded",
    )
    parser.add_argument(
        "--surface-ramp-above-mode",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="sample potential above the surface (default: enabled)",
    )
    parser.add_argument(
        "--ligand-representation",
        choices=("sticks", "lines", "dots", "spheres", "none"),
        default="sticks",
        help="display retained HETATM atoms; none hides the extra representation",
    )
    parser.add_argument("--pdb2pqr-executable", default="pdb2pqr30")
    parser.add_argument("--apbs-executable", default="apbs")
    parser.add_argument("--pymol-executable", default="pymol")


def _worker_count(value: str) -> int:
    if value.lower() == "auto":
        return recommended_workers()
    try:
        return int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Choose an integer worker count or auto") from exc


def build_parser() -> argparse.ArgumentParser:
    """Construct the command parser without executing it."""

    parser = argparse.ArgumentParser(
        prog="phmap",
        description="Generate pH-dependent protein electrostatic surface maps locally.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--debug", action="store_true", help="show a traceback for unexpected failures"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    doctor = commands.add_parser("doctor", help="check the local scientific toolchain")
    doctor.add_argument("--output-base", type=Path, default=Path("runs"))
    doctor.add_argument("--json", action="store_true", dest="as_json")

    validate = commands.add_parser("validate", help="validate and normalize inputs only")
    _add_input_arguments(validate, include_run=False)
    validate.add_argument("--json", action="store_true", dest="as_json")

    run = commands.add_parser("run", help="run PDB2PQR, APBS, PyMOL, and composition")
    _add_input_arguments(run, include_run=True)
    for command, description in (
        ("compose", "edit a figure using verified saved PNG tiles; no scientific tools"),
        ("render", "rerender verified saved PQR/DX artifacts; run only PyMOL and composition"),
    ):
        saved = commands.add_parser(command, help=description)
        saved.add_argument("source", type=Path, help="source run directory")
        _add_workflow_arguments(saved)
        # Saved-run settings inherit from the source unless explicitly overridden.
        for action in saved._actions:
            if action.option_strings and action.dest != "help":
                action.default = argparse.SUPPRESS
        saved.add_argument("--output-dir", type=Path)
        saved.add_argument(
            "--in-place",
            action="store_true",
            help="update this calculation after success, without retaining old versions",
        )
        saved.add_argument("--protein-id", action="append", help="select/reorder saved rows")
        saved.add_argument("--label", action="append", help="replacement label per selected row")
        saved.add_argument(
            "--ph", dest="ph_values", action="append", help="select/reorder saved columns"
        )
        saved.add_argument("--view", type=Path, help="replace the saved camera (render only)")
        saved.add_argument("--auto-view", action="store_true", help="clear the saved camera")
        saved.add_argument(
            "--transparent", action="store_true", help="use a transparent background"
        )
        saved.add_argument(
            "--preserve-ph-labels", action="store_true", help="reset pH label precision"
        )
        saved.add_argument(
            "--plan", action="store_true", help="verify artifacts and print JSON plan only"
        )
        saved.add_argument("--source-manifest-sha256", help=argparse.SUPPRESS)
    gui = commands.add_parser("gui", help="open the local browser interface")
    gui.add_argument("--runs-dir", type=Path, default=Path("runs"))
    gui.add_argument("--port", type=int, default=8765)
    gui.add_argument("--no-browser", action="store_true", help="print the URL without opening it")
    return parser


def _inputs_from_args(args: argparse.Namespace) -> RunInputs:
    if not args.pdb and not args.pdb_id:
        raise InputValidationError("Provide local structure files or at least one --pdb-id")
    # Reject invalid selections and IDs before making any network requests.
    parse_ph_selection(ph_values=args.ph_values, ph_range=args.ph_range)
    pdb_ids = [normalize_pdb_id(pdb_id) for pdb_id in args.pdb_id]
    validate_protein_names(
        len(pdb_ids) + len(args.pdb),
        protein_ids=args.protein_id,
        labels=args.label,
    )
    local_paths = validate_structure_files(args.pdb)
    if args.view is not None:
        load_pymol_view(args.view)
    if args.ligand is not None:
        validate_mol2_path(args.ligand)
    paths = [
        fetch_structure(pdb_id, args.structure_cache_dir, refresh=args.refresh_structures)
        for pdb_id in pdb_ids
    ] + list(local_paths)
    return validate_run_inputs(
        paths,
        ph_values=args.ph_values,
        ph_range=args.ph_range,
        protein_ids=args.protein_id,
        labels=args.label,
        ligand=args.ligand,
        view_file=args.view,
    )


def _input_summary(inputs: RunInputs, output_dir: Path | None = None) -> dict[str, object]:
    return {
        "valid": True,
        "proteins": [
            {
                "id": protein.protein_id,
                "label": protein.label,
                "pdb": str(protein.path),
                "path": str(protein.path),
                "format": structure_format(protein.path),
                "source": structure_source(protein.path) or None,
                "ligand": None if protein.ligand is None else str(protein.ligand),
            }
            for protein in inputs.proteins
        ],
        "ph_values": [str(ph) for ph in inputs.ph_values],
        "ph_mode": inputs.ph_mode.value,
        "view": None if inputs.view is None else list(inputs.view.values),
        "output_dir": None if output_dir is None else str(output_dir.expanduser().resolve()),
    }


def _doctor(args: argparse.Namespace) -> int:
    results = run_doctor(args.output_base)
    if args.as_json:
        print(json.dumps([result.as_dict() for result in results], indent=2))
    else:
        width = max(len(result.name) for result in results)
        for result in results:
            mark = "ok" if result.ok else "MISSING"
            version = f" ({result.version})" if result.version else ""
            print(f"{result.name:<{width}}  {mark:<7} {result.message}{version}")
            if result.path:
                print(f"{'':<{width}}          {result.path}")
    return 0 if all(result.ok for result in results) else 1


def _validate(args: argparse.Namespace) -> int:
    inputs = _inputs_from_args(args)
    summary = _input_summary(inputs, args.output_dir)
    if args.as_json:
        print(json.dumps(summary, indent=2))
    else:
        print("Inputs are valid.")
        print("Proteins: " + ", ".join(protein.protein_id for protein in inputs.proteins))
        print("pH order: " + ", ".join(str(ph) for ph in inputs.ph_values))
        if args.output_dir is not None:
            print(f"Run directory: {args.output_dir.expanduser().resolve()}")
    return 0


def _options_from_args(
    args: argparse.Namespace, base: WorkflowOptions | None = None
) -> WorkflowOptions:
    from dataclasses import fields, replace

    mapping = {"level": "potential_level", "no_ray": "ray_trace"}
    names = {item.name for item in fields(WorkflowOptions)}
    values = {}
    for key, value in vars(args).items():
        target = mapping.get(key, key)
        if target not in names:
            continue
        if key == "no_ray":
            value = not value
        elif key == "ramp_colors":
            value = tuple(value)
        elif key == "ligand_representation" and value == "none":
            value = None
        values[target] = value
    if getattr(args, "transparent", False):
        if "background" in vars(args):
            raise InputValidationError("Choose --background or --transparent, not both")
        values["background"] = None
    if getattr(args, "preserve_ph_labels", False):
        values["ph_label_precision"] = None
    return replace(base or WorkflowOptions(), **values)


def _run(args: argparse.Namespace) -> int:
    options = _options_from_args(args)
    values, _ = parse_ph_selection(ph_values=args.ph_values, ph_range=args.ph_range)
    format_ph_labels(values, options.ph_label_precision)
    inputs = _inputs_from_args(args)
    result = LocalWorkflow(inputs, options).run()
    print(f"Run completed: {result.workspace.root}")
    print(f"Figure: {result.figure}")
    print(f"Manifest: {result.manifest}")
    return 0


def _saved_run(args: argparse.Namespace) -> int:
    if args.in_place and (args.output_dir is not None or "runs_dir" in vars(args)):
        raise InputValidationError("--in-place cannot be combined with an output destination")
    source = SavedRun(args.source, expected_manifest=args.source_manifest_sha256)
    if args.view is not None and args.auto_view:
        raise InputValidationError("Choose --view or --auto-view, not both")
    workflow = source.prepare(
        args.command,
        _options_from_args(args, source.options),
        protein_ids=args.protein_id,
        labels=args.label,
        ph_values=args.ph_values,
        view=None if args.view is None else load_pymol_view(args.view),
        use_saved_view=not args.auto_view,
    )
    print(json.dumps(workflow.plan(), indent=2))
    if not args.plan:
        result = workflow.run_in_place() if args.in_place else workflow.run()
        print(f"Run completed: {result.workspace.root}")
        print(f"Figure: {result.figure}")
        print(f"Manifest: {result.manifest}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line and return a process exit status."""

    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            return _doctor(args)
        if args.command == "validate":
            return _validate(args)
        if args.command == "run":
            return _run(args)
        if args.command in {"compose", "render"}:
            return _saved_run(args)
        if args.command == "gui":
            from phmap.gui.app import serve

            try:
                serve(runs_dir=args.runs_dir, port=args.port, open_browser=not args.no_browser)
            except KeyboardInterrupt:
                return 130
            return 0
        parser.error(f"unknown command: {args.command}")
    except (InputValidationError, PHMapError, ScientificBackendError) as exc:
        print(f"phmap: error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("phmap: calculation cancelled", file=sys.stderr)
        return 130
    except Exception as exc:
        if args.debug:
            raise
        print(f"phmap: unexpected error: {exc}", file=sys.stderr)
        print("Re-run with --debug before the command for a traceback.", file=sys.stderr)
        return 1
    return 1
