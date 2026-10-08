"""Exercise an actual frozen release, including first-install and offline restart."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    executable = (ROOT / "build" / "desktop-executable.txt").read_text().strip()
    with tempfile.TemporaryDirectory(prefix="phmap-release-") as temporary:
        storage = Path(temporary)
        command = [executable, "--smoke-test", "--data-dir", str(storage)]
        try:
            subprocess.run(command, check=True, timeout=1200)
            # A second launch must not reinstall or need a network connection.
            subprocess.run(
                command, check=True, timeout=120, env={**os.environ, "PIXI_OFFLINE": "true"}
            )
            subprocess.run(
                [executable, "--window-smoke-test", "--data-dir", str(storage)],
                check=True,
                timeout=120,
                env={**os.environ, "PIXI_OFFLINE": "true"},
            )
            report = json.loads((storage / "runs/.gui/window-smoke.json").read_text())
            assert report["ready"], report
            print(f"Native launcher window verified: {report}")
        except Exception:
            for log in storage.rglob("desktop.log"):
                print(log.read_text(errors="replace")[-20_000:])
            raise


if __name__ == "__main__":
    main()
