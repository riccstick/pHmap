from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

CI_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ci_changes.py"


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        [
            "git",
            "-c",
            "user.name=pHmap CI tests",
            "-c",
            "user.email=ci@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "-c",
            f"core.hooksPath={repository / 'no-hooks'}",
            *arguments,
        ],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()


def _commit(repository: Path, files: dict[str, str]) -> str:
    for name, content in files.items():
        path = repository / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _git(repository, "add", "--all")
    _git(repository, "commit", "--quiet", "-m", "Test change")
    return _git(repository, "rev-parse", "HEAD")


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    root.mkdir()
    _git(root, "init", "--quiet", "--initial-branch=main")
    _commit(root, {"README.md": "pHmap", "docs/index.md": "Home", "src/model.py": "value = 1"})
    return root


def _classify(
    repository: Path,
    event_name: str,
    event: dict,
    *,
    ref: str = "refs/heads/main",
) -> bool:
    event_path = repository.parent / "event.json"
    output_path = repository.parent / "output.txt"
    summary_path = repository.parent / "summary.md"
    event_path.write_text(json.dumps(event), encoding="utf-8")
    output_path.write_text("", encoding="utf-8")
    env = {
        **os.environ,
        "GITHUB_EVENT_NAME": event_name,
        "GITHUB_EVENT_PATH": str(event_path),
        "GITHUB_REF": ref,
        "GITHUB_OUTPUT": str(output_path),
        "GITHUB_STEP_SUMMARY": str(summary_path),
    }
    subprocess.run(
        [sys.executable, str(CI_SCRIPT)],
        cwd=repository,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    output = output_path.read_text(encoding="utf-8")
    assert output in {"docs_only=true\n", "docs_only=false\n"}
    assert summary_path.read_text(encoding="utf-8")
    return output == "docs_only=true\n"


def _pull_request(base: str, head: str) -> dict:
    return {"pull_request": {"base": {"sha": base}, "head": {"sha": head}}}


@pytest.mark.parametrize(
    "files",
    [
        {"docs/publish/story with spaces.md": "Story", "docs/assets/image.png": "Image fixture"},
        {"README.md": "New introduction", "zensical.toml": "[project]"},
        {".github/workflows/docs.yml": "name: Documentation site"},
    ],
)
def test_documentation_pull_requests_use_lightweight_checks(
    repository: Path,
    files: dict[str, str],
) -> None:
    base = _git(repository, "rev-parse", "HEAD")
    head = _commit(repository, files)
    assert _classify(repository, "pull_request", _pull_request(base, head))


def test_mixed_documentation_and_source_changes_run_full_ci(repository: Path) -> None:
    base = _git(repository, "rev-parse", "HEAD")
    head = _commit(repository, {"docs/index.md": "Updated", "src/model.py": "value = 2"})
    assert not _classify(repository, "pull_request", _pull_request(base, head))


def test_push_checks_all_commits_not_only_the_latest_commit(repository: Path) -> None:
    base = _git(repository, "rev-parse", "HEAD")
    _commit(repository, {"src/model.py": "value = 2"})
    head = _commit(repository, {"docs/index.md": "Documentation follows source change"})
    assert not _classify(repository, "push", {"before": base, "after": head})


def test_pull_request_ignores_unrelated_changes_on_the_base_branch(repository: Path) -> None:
    _git(repository, "switch", "-c", "documentation")
    head = _commit(repository, {"docs/index.md": "Documentation branch"})
    _git(repository, "switch", "main")
    base = _commit(repository, {"src/model.py": "value = 2"})
    assert _classify(repository, "pull_request", _pull_request(base, head))


def test_moving_source_into_docs_still_runs_full_ci(repository: Path) -> None:
    base = _git(repository, "rev-parse", "HEAD")
    (repository / "src/model.py").rename(repository / "docs/model.py")
    head = _commit(repository, {})
    assert not _classify(repository, "pull_request", _pull_request(base, head))


@pytest.mark.parametrize("source_change", [False, True])
def test_merge_group_classifies_the_whole_group(repository: Path, source_change: bool) -> None:
    base = _git(repository, "rev-parse", "HEAD")
    _commit(repository, {"docs/index.md": "First grouped change"})
    files = {"src/model.py": "value = 2"} if source_change else {"docs/tutorial.md": "Tutorial"}
    head = _commit(repository, files)
    event = {"merge_group": {"base_sha": base, "head_sha": head}}
    assert _classify(repository, "merge_group", event) is not source_change


@pytest.mark.parametrize(
    ("event_name", "ref"),
    [
        ("push", "refs/tags/v2.0.1"),
        ("workflow_dispatch", "refs/heads/main"),
    ],
)
def test_tags_and_manual_runs_always_build(
    repository: Path,
    event_name: str,
    ref: str,
) -> None:
    base = _git(repository, "rev-parse", "HEAD")
    head = _commit(repository, {"docs/index.md": "Documentation-only last commit"})
    assert not _classify(repository, event_name, {"before": base, "after": head}, ref=ref)


@pytest.mark.parametrize("base", ["0" * 40, "f" * 40, "--invalid-ref"])
def test_unverified_diffs_run_full_ci(repository: Path, base: str) -> None:
    head = _git(repository, "rev-parse", "HEAD")
    assert not _classify(repository, "push", {"before": base, "after": head})


def test_deleting_the_packaging_readme_runs_full_ci(repository: Path) -> None:
    base = _git(repository, "rev-parse", "HEAD")
    (repository / "README.md").unlink()
    head = _commit(repository, {})
    assert not _classify(repository, "push", {"before": base, "after": head})


def test_empty_or_missing_event_data_runs_full_ci(repository: Path) -> None:
    head = _git(repository, "rev-parse", "HEAD")
    assert not _classify(repository, "push", {"before": head, "after": head})
    assert not _classify(repository, "pull_request", {})
    assert not _classify(repository, "unknown", {})


def test_changed_file_detection_is_not_limited_to_300_paths(repository: Path) -> None:
    base = _git(repository, "rev-parse", "HEAD")
    files = {f"docs/page-{index:03}.md": "Documentation" for index in range(301)}
    files["src/model.py"] = "value = 2"
    head = _commit(repository, files)
    assert not _classify(repository, "pull_request", _pull_request(base, head))
