"""Shared portable scientific tool fixtures; no imports between test modules."""

from pathlib import Path

from fake_executable import executable


def tool(tmp_path: Path, name: str, source: str) -> Path:
    return executable(tmp_path / "tools", name, source)


def fake_tools(tmp_path: Path, *, fail_apbs: bool = False) -> tuple[Path, Path, Path]:
    pdb2pqr = tool(
        tmp_path,
        "pdb2pqr30",
        r'''
        import pathlib
        import sys

        if sys.argv[1:] == ["--version"]:
            print("pdb2pqr 3.7.1")
            raise SystemExit(0)
        args = sys.argv[1:]
        apbs_input = pathlib.Path(
            next(arg.split("=", 1)[1] for arg in args if arg.startswith("--apbs-input="))
        )
        pqr = pathlib.Path(args[-1])
        pqr.write_text("ATOM 1 N ALA A 1 0 0 0 -0.3 1.5\n", encoding="utf-8")
        apbs_input.write_text(str(pathlib.Path(f"{pqr}.dx")), encoding="utf-8")
        print("prepared", pqr)
        ''',
    )
    apbs_body = (
        r'''
        import sys
        if sys.argv[1:] == ["--version"]:
            print("APBS 3.4.1")
            raise SystemExit(0)
        print("solver failed", file=sys.stderr)
        raise SystemExit(4)
        '''
        if fail_apbs
        else r'''
        import pathlib
        import sys

        if sys.argv[1:] == ["--version"]:
            print("APBS 3.4.1")
            raise SystemExit(0)
        input_path = pathlib.Path(sys.argv[-1])
        dx = pathlib.Path(input_path.read_text(encoding="utf-8").strip())
        dx.write_text(
            "object 1 class gridpositions counts 1 1 2\n"
            "origin 0 0 0\n"
            "delta 1 0 0\n"
            "delta 0 1 0\n"
            "delta 0 0 1\n"
            "object 2 class gridconnections counts 1 1 2\n"
            "object 3 class array type double rank 0 items 2 data follows\n"
            "-1.0 1.0\n",
            encoding="utf-8",
        )
        print("solved", dx)
        '''
    )
    apbs = tool(tmp_path, "apbs", apbs_body)
    pymol = tool(
        tmp_path,
        "pymol",
        r'''
        import json
        import pathlib
        import re
        import sys
        from PIL import Image

        if sys.argv[1:] == ["--version"]:
            print("PyMOL 3.1.0")
            raise SystemExit(0)
        script = pathlib.Path(sys.argv[-1]).read_text(encoding="utf-8")
        output_match = re.search(
            r'^cmd\.png\(("(?:\\.|[^"\\])*")', script, re.MULTILINE
        )
        assert output_match is not None
        output = pathlib.Path(json.loads(output_match.group(1)))
        width = int(re.search(r'width=(\d+)', script).group(1))
        height = int(re.search(r'height=(\d+)', script).group(1))
        color = (240, 0, 0, 255) if "ph-5.0" in str(output) else (0, 0, 240, 255)
        Image.new("RGBA", (width, height), color).save(output)
        print("rendered", output)
        ''',
    )
    return pdb2pqr, apbs, pymol
