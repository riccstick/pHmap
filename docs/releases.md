# Desktop releases

The desktop launcher starts the existing local browser interface. It does not
replace or freeze the scientific CLI: jobs still run in an ordinary Python
interpreter with the pinned PDB2PQR, APBS, and PyMOL tools. All existing CLI
arguments remain available in the project environment.

## What users download

| Target | Download | Start |
| --- | --- | --- |
| Apple Silicon macOS | `pHmap-<version>-macos-arm64.zip` | Extract, open `pHmap.app` |
| Intel macOS | `pHmap-<version>-macos-x64.zip` | Extract, open `pHmap.app` |
| Windows x64 | `pHmap-<version>-windows-x64.zip` | Extract the whole folder, open `pHmap.exe` |
| Linux x64 | `pHmap-<version>-linux-x64.tar.gz` | Extract the whole folder, open the `pHmap` executable |

The first launch installs the locked scientific environment in user-owned app
storage. This requires internet access, disk space for the native scientific
packages, and potentially several minutes. Progress and errors are displayed
in the launcher, with a **View log** button. Later launches reuse the environment
and do not need internet. This is an online first-run installer, **not an offline
bundle of all scientific binaries**.

The browser is only the interface; calculations and uploaded files remain local.
Close the launcher or use its application **Quit** command to stop pHmap.
Unfinished GUI jobs are cancelled; saved results and logs remain available.
Opening pHmap again while its service is running reopens that same interface.

Release runs and installations are stored in:

- macOS: `~/Library/Application Support/pHmap/`
- Windows: `%LOCALAPPDATA%\pHmap\`
- Linux: `${XDG_DATA_HOME:-~/.local/share}/phmap/`

`runs/` is shared across app versions. Installed runtime directories are
versioned by package version and payload hash, so an upgrade does not overwrite
the previous environment or run history. Keep the whole extracted Windows/Linux
folder together. On macOS the release app can be moved to Applications; its
installation and results are separate from the app bundle.

Linux requires a graphical desktop/browser and compatible system libraries.
The current Linux build uses Ubuntu 22.04 (glibc-based x64); it is not an Alpine
(musl), ARM, or universal Linux build. macOS has separate Intel/Apple Silicon
downloads; Windows currently targets x64, not ARM.

## Build locally

Install the project's locked Pixi environment, then build on the target OS:

```console
pixi install --locked
pixi run build-app
pixi run python scripts/check_desktop.py
```

The generated launcher is in `dist/pHmap.app` on Mac or `dist/pHmap/` on
Windows/Linux. Archives and SHA-256 checksums are in `dist/release/`.
Builds are native: run the build on each target OS/architecture.

For development on an already configured checkout:

```console
pixi run build-app --local-project
```

This app uses that checkout's `.pixi/envs/default/` and existing `runs/` instead
of installing a separate runtime. Its archive is marked `-local`. **Do not
publish this development build:** it stores the checkout's absolute path and
requires that checkout to remain present. The release workflow never sets this
option.

## Publish through GitHub

The workflow `.github/workflows/desktop.yml`, shown as **CI and desktop releases**,
provides both continuous integration and release builds:

| Event | Checks and native builds | Release creation |
| --- | --- | --- |
| PR opened, updated with new commits, reopened, or marked ready for review | All four platforms | None |
| Push to `main` or `master`, including after merging | All four platforms | None |
| Merge queue requests checks | All four platforms | None |
| Push of a `v*` version tag | All four platforms and matching version check | Draft only after all builds pass |
| Manual **Run workflow** | All four platforms | None |

Each platform job runs the unit and integration suite before building. Different
commits use different concurrency groups, so a newer push does not cancel an
older commit's checks. A push containing several commits tests the resulting
tip/PR merge snapshot, not every intermediate historical commit separately;
that is how GitHub's [workflow events](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)
work. PR checks use GitHub's proposed merge with the base branch, and merge
queue checks use the queued merge group. No path filters or draft-PR exclusions
skip tests for particular source changes.

The workflow:

1. Installs the locked scientific environment on all four native runners.
2. Runs unit/integration tests, lint, type checks, and dependency preflight.
3. Runs a real two-pH scientific calculation on each platform.
4. Builds the native launcher and checks actual frozen startup, GUI assets,
   first-time environment installation, shutdown, offline restart, and native
   launcher window creation/layout. Linux window tests use Xvfb.
5. Uploads platform archives and their checksums as workflow artifacts.
6. On a `v*` tag, creates a **draft** GitHub Release only after all builds pass.

Set the same version in `pyproject.toml` and `src/phmap/__init__.py`, refresh
`uv.lock` and `pixi.lock`, commit, and tag that commit. For the current version:

```console
git tag v2.0.0a1
git push origin v2.0.0a1
```

The build rejects tags that do not match `pyproject.toml`. Once Actions is green,
open **Releases**, inspect the draft, download/test the apps on clean machines,
edit release notes, mark alpha/beta releases as **pre-release**, and publish.
Manual workflow runs create downloadable artifacts but no release.

Only the release job receives `contents: write`; builds have read-only repo
permissions. No personal access token or signing credentials are required for
this initial draft-release workflow. Nothing is uploaded to PyPI.

### Require passing checks before merging

Commit and push `.github/workflows/desktop.yml` and the application changes to
make this configuration available to GitHub Actions. After the first workflow
run reports its checks, configure a repository ruleset or branch protection for
`main` (and `master` if used) to require these four platform checks:

- `macos-arm64`
- `macos-x64`
- `windows-x64`
- `linux-x64`

Use **Require status checks to pass before merging** and require the branch to
be up to date, or use a merge queue. These repository settings enforce the
checks; a workflow file alone does not prevent merging a failing PR. See
[GitHub's protected branch documentation](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches).
The `merge_group` trigger keeps the same required checks available if a merge
queue is enabled. Fork contributions may need maintainer approval before their
Actions run; the workflow intentionally uses `pull_request`, not the
privileged `pull_request_target` event.

## Signing and validation before public distribution

These are initial unsigned/not-notarized desktop builds (Mac binaries have
PyInstaller's local ad-hoc signature, not a Developer ID signature). macOS
Gatekeeper and Windows SmartScreen can warn or block downloaded applications.
For a polished public release, add Developer ID signing/notarization and
Windows Authenticode signing using protected repository/environment secrets.
Do not ask users to disable operating-system security globally.

The Actions matrix is the verification gate for Windows, Linux, and Intel Mac;
configuring it is not evidence those targets have already passed. Locally,
only the available host platform can be fully exercised. Native rendering can
vary by platform, so retain numerical artifacts and review the scientific
baseline as described in `development.md`.

Python/Tk and the bundled Pixi launcher carry their upstream license notices.
Scientific dependencies are downloaded from the pinned upstream package
repositories, not redistributed as an opaque frozen interpreter. Review all
third-party notices and dependencies before a public release, especially if
changing to a fully offline scientific bundle.
