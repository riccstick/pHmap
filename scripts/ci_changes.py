"""Detect documentation-only CI events without suppressing required checks."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

DOCUMENTATION_FILES = {
    "README.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "CODE_OF_CONDUCT.md",
    "SECURITY.md",
    "zensical.toml",
    ".github/workflows/docs.yml",
}
_SHA = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})\Z")


def documentation_only(
    event_name: str,
    event: dict[str, Any],
    ref: str,
    repository: Path,
) -> bool:
    """Skip heavy CI only when a complete Git diff proves the change is docs-only.

    Tags and manual runs always build. Missing history, new branches, and unknown
    events fall back to full CI. Local Git avoids API pagination/path-filter limits.
    """
    if ref.startswith("refs/tags/") or event_name == "workflow_dispatch":
        return False

    try:
        if event_name == "pull_request":
            base = event["pull_request"]["base"]["sha"]
            head = event["pull_request"]["head"]["sha"]
            separator = "..."
        elif event_name == "merge_group":
            base = event["merge_group"]["base_sha"]
            head = event["merge_group"]["head_sha"]
            separator = ".."
        elif event_name == "push":
            base, head = event["before"], event["after"]
            separator = ".."
        else:
            return False

        if not all(
            isinstance(sha, str) and _SHA.fullmatch(sha) and set(sha) != {"0"}
            for sha in (base, head)
        ):
            return False

        result = subprocess.run(
            ["git", "diff", "--name-only", "--no-renames", "-z", f"{base}{separator}{head}", "--"],
            cwd=repository,
            check=True,
            capture_output=True,
            timeout=30,
        )
    except (KeyError, TypeError, OSError, subprocess.SubprocessError):
        print("Unable to verify changed files; running full desktop CI.")
        return False

    paths = result.stdout.decode("utf-8", errors="surrogateescape").split("\0")
    changed = [path for path in paths if path]
    # README.md is required by the package metadata: deleting it needs a build.
    return (
        bool(changed)
        and (repository / "README.md").is_file()
        and all(path.startswith("docs/") or path in DOCUMENTATION_FILES for path in changed)
    )


def main() -> None:
    docs_only = False
    try:
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
        if isinstance(event, dict):
            docs_only = documentation_only(
                os.environ.get("GITHUB_EVENT_NAME", ""),
                event,
                os.environ.get("GITHUB_REF", ""),
                Path.cwd(),
            )
    except (KeyError, OSError, ValueError):
        print("Unable to read the GitHub event; running full desktop CI.")

    output = f"docs_only={str(docs_only).lower()}\n"
    print(output, end="")
    if output_path := os.environ.get("GITHUB_OUTPUT"):
        with Path(output_path).open("a", encoding="utf-8") as handle:
            handle.write(output)

    message = (
        "Documentation-only update: native tests and desktop builds are skipped. "
        "The Documentation site workflow builds the website."
        if docs_only
        else "Full scientific tests and native desktop builds are required."
    )
    print(message)
    if summary_path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(summary_path).open("a", encoding="utf-8") as handle:
            handle.write(message + "\n")


if __name__ == "__main__":
    main()
