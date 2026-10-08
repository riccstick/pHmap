"""Portable Python test tools, including real console launchers on Windows."""

from __future__ import annotations

import os
import stat
import sys
import textwrap
from pathlib import Path


def executable(parent: Path, name: str, source: str) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    body = textwrap.dedent(source)
    if os.name == "nt":
        from distlib.scripts import ScriptMaker

        sources = parent / "sources"
        sources.mkdir(exist_ok=True)
        (sources / f"{name}.py").write_text("#!python\n" + body, encoding="utf-8")
        maker = ScriptMaker(str(sources), str(parent))
        maker.executable = sys.executable
        maker.make(f"{name}.py")
        return parent / f"{name}.exe"
    path = parent / name
    path.write_text(f"#!{sys.executable}\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path
