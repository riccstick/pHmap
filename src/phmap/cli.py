"""Command-line interface for the local Python pHmap workflow."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from phmap import __version__
from phmap.backends import BackendError as ScientificBackendError
from phmap.doctor import run_doctor
from phmap.errors import PHMapError
from phmap.inputs import validate_run_inputs
from phmap.models import InputValidationError, RunInputs
from phmap.workflow import LocalWorkflow, WorkflowOptions


def _add_input_arguments(parser: argparse.ArgumentParser, *, include_run: bool) -> None:
    parser.add_argument("pdb", nargs="+", type=Path, help="one or more PDB input files")
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
        help="explicit safe protein ID; repeat once per PDB",
    )
    parser.add_argument(
        "--label",
        action="append",
        help="display label; repeat once per PDB",
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
        parser.add_argument("--force-field", default="PARSE")
        parser.add_argument("--timeout", type=float, default=1800.0, metavar="SECONDS")
        parser.add_argument("--no-ray", action="store_true", help="disable PyMOL ray tracing")
        parser.add_argument("--pdb2pqr-executable", default="pdb2pqr30")
        parser.add_argument("--apbs-executable", default="apbs")
        parser.add_argument("--pymol-executable", default="pymol")


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
    return parser


def _inputs_from_args(args: argparse.Namespace) -> RunInputs:
    return validate_run_inputs(
        args.pdb,
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


def _run(args: argparse.Namespace) -> int:
    inputs = _inputs_from_args(args)
    options = WorkflowOptions(
        output_dir=args.output_dir,
        runs_dir=args.runs_dir,
        size=args.size,
        dpi=args.dpi,
        potential_level=args.level,
        ramp_colors=tuple(args.ramp_colors),
        background=args.background,
        foreground=args.foreground,
        force_field=args.force_field,
        timeout=args.timeout,
        ray_trace=not args.no_ray,
        pdb2pqr_executable=args.pdb2pqr_executable,
        apbs_executable=args.apbs_executable,
        pymol_executable=args.pymol_executable,
    )
    result = LocalWorkflow(inputs, options).run()
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
        parser.error(f"unknown command: {args.command}")
    except (InputValidationError, PHMapError, ScientificBackendError) as exc:
        print(f"phmap: error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        if args.debug:
            raise
        print(f"phmap: unexpected error: {exc}", file=sys.stderr)
        print("Re-run with --debug before the command for a traceback.", file=sys.stderr)
        return 1
    return 1
