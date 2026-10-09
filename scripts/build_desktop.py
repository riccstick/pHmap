"""Build a native launcher with a locked first-run scientific installation.

Run on each target OS (PyInstaller is not a cross compiler). Release payloads
include only the source/manifest needed for runtime installation, never local
environments, runs, credentials, or historical reference assets.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from phmap import __version__

ROOT = Path(__file__).resolve().parents[1]


def platform_id() -> str:
    machine = platform.machine().lower()
    if sys.platform == "darwin":
        return "macos-arm64" if machine in {"arm64", "aarch64"} else "macos-x64"
    if sys.platform == "win32":
        return "windows-x64"
    if sys.platform == "linux" and machine in {"x86_64", "amd64"}:
        return "linux-x64"
    raise SystemExit(f"Unsupported desktop build platform: {sys.platform}/{machine}")


def icon(folder: Path) -> Path:
    image = Image.new("RGBA", (1024, 1024))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((32, 32, 992, 992), radius=205, fill="#122c37")
    draw.ellipse((165, 205, 500, 540), fill="#ec6961")
    draw.ellipse((320, 310, 700, 690), fill="#e9e8df")
    draw.ellipse((520, 165, 855, 500), fill="#6bafdc")
    font = ImageFont.load_default(size=180)
    draw.text((512, 815), "pH", fill="white", font=font, anchor="mm")
    image.save(folder / "icon.png")
    suffix = ".icns" if sys.platform == "darwin" else ".ico"
    target = folder / f"icon{suffix}"
    image.save(target)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--local-project",
        action="store_true",
        help="use this checkout's existing environment and run history",
    )
    parser.add_argument("--pixi", type=Path, help="path to the Pixi binary to bundle")
    parser.add_argument(
        "--check-tag", help="reject release tags that do not match the package version"
    )
    args = parser.parse_args()
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    if __version__ != version:
        raise SystemExit("pyproject.toml and phmap.__version__ must agree before building")
    if args.check_tag and args.check_tag != f"v{version}":
        raise SystemExit(f"Tag {args.check_tag!r} must match package version v{version}")
    found = str(args.pixi) if args.pixi else shutil.which("pixi")
    if not found or not Path(found).is_file():
        raise SystemExit("Pixi must be installed on the build machine or passed with --pixi")
    build = ROOT / "build"
    build.mkdir(exist_ok=True)
    dist = ROOT / "dist"
    with tempfile.TemporaryDirectory(prefix="desktop-payload-", dir=build) as temporary:
        assets = Path(temporary)
        shutil.copytree(ROOT / "packaging" / "licenses", assets / "licenses")
        runtime = assets / "runtime"
        runtime.mkdir()
        for name in ("pixi.toml", "pixi.lock", "pyproject.toml", "README.md", "LICENSE"):
            shutil.copy2(ROOT / name, runtime / name)
        shutil.copytree(
            ROOT / "src", runtime / "src", ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
        )
        digest = hashlib.sha256()
        for path in sorted(runtime.rglob("*")):
            if path.is_file():
                digest.update(path.relative_to(runtime).as_posix().encode())
                digest.update(path.read_bytes())
        (assets / "runtime-id.txt").write_text(digest.hexdigest())
        tools = assets / "tools"
        tools.mkdir()
        shutil.copy2(found, tools / ("pixi.exe" if os.name == "nt" else "pixi"))
        app_icon = icon(assets)
        argv = [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onedir",
            "--windowed",
            "--name",
            "pHmap",
            "--paths",
            str(ROOT / "src"),
            "--icon",
            str(app_icon),
            "--distpath",
            str(dist),
            "--workpath",
            str(build / "pyinstaller"),
            "--specpath",
            str(build),
        ]
        for name in ("runtime", "tools", "licenses", "runtime-id.txt", "icon.png"):
            destination = name if name in {"runtime", "tools", "licenses"} else "."
            argv.extend(("--add-data", f"{assets / name}{os.pathsep}{destination}"))
        if args.local_project:
            (assets / "local-project.json").write_text(json.dumps({"project": str(ROOT)}))
            argv.extend(("--add-data", f"{assets / 'local-project.json'}{os.pathsep}."))
        if sys.platform == "darwin":
            argv.extend(("--osx-bundle-identifier", "org.phmap.local"))
        argv.append(str(ROOT / "scripts" / "desktop_entry.py"))
        subprocess.run(
            argv,
            cwd=ROOT,
            check=True,
            env={**os.environ, "PYINSTALLER_CONFIG_DIR": str(build / "pyinstaller-cache")},
        )
    release = dist / "release"
    release.mkdir(exist_ok=True)
    stem = f"pHmap-{version}-{platform_id()}" + ("-local" if args.local_project else "")
    if sys.platform == "darwin":
        archive = release / f"{stem}.zip"
        subprocess.run(
            [
                "ditto",
                "-c",
                "-k",
                "--sequesterRsrc",
                "--keepParent",
                str(dist / "pHmap.app"),
                str(archive),
            ],
            check=True,
        )
        executable = dist / "pHmap.app" / "Contents" / "MacOS" / "pHmap"
    elif os.name == "nt":
        archive = Path(shutil.make_archive(str(release / stem), "zip", dist, "pHmap"))
        executable = dist / "pHmap" / "pHmap.exe"
    else:
        archive = release / f"{stem}.tar.gz"
        with tarfile.open(archive, "w:gz") as package:
            package.add(dist / "pHmap", arcname="pHmap")
        executable = dist / "pHmap" / "pHmap"
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(archive.suffix + ".sha256").write_text(f"{checksum}  {archive.name}\n")
    (build / "desktop-executable.txt").write_text(str(executable))
    print(f"Built {archive}\nLauncher: {executable}")


if __name__ == "__main__":
    main()
