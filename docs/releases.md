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

## Troubleshooting

### Windows first-run download fails on a company network

#### Symptoms

The desktop launcher fails while installing its scientific environment, before
any protein calculation starts. A reported example was:

```text
failed to fetch font-ttf-inconsolata-3.000-h77eed37_0.tar.bz2
failed to interact with the package cache layer
Request failed after 3 retries
client error (Connect)
received corrupt message of type InvalidContentType
```

The download was from `conda.anaconda.org/conda-forge`. The affected Windows
computer used a company VPN and Zscaler; SentinelOne was also installed.

#### Cause and investigation status

`InvalidContentType` is a TLS-message decoding error, not an HTTP Content-Type
error or evidence that the font archive or package cache is corrupt. The cache
message wraps the underlying connection failure. See the
[Rustls error documentation](https://docs.rs/rustls/latest/rustls/enum.InvalidMessage.html#variant.InvalidContentType).

A corporate proxy or HTTPS-inspection compatibility issue is suspected, but
the exact cause has not been confirmed on the affected computer. Zscaler can
inspect HTTPS traffic using an organization-managed certificate. The standalone
Pixi bundled with the app normally uses Mozilla certificate roots, which may
not include that company certificate. This is one possible trust issue, not a
proven explanation for this particular TLS-message error. There is no evidence
from the supplied error that SentinelOne caused it. See
[Zscaler's SSL-inspection guidance](https://help.zscaler.com/client-connector/configuring-ssl-inspection-zscaler-client-connector)
and [Pixi's certificate configuration](https://pixi.prefix.dev/v0.75.0/reference/pixi_configuration/#tls-root-certs).

#### Safe recovery steps

1. Close and reopen pHmap to retry setup. A failed install is not marked ready;
   completed cached downloads can be reused. Do not delete your results or the
   whole app-data directory.
2. Ask IT to confirm that the approved company inspection certificate is
   installed in the Windows certificate store. Do not install certificates
   obtained from an untrusted source.
3. If company policy permits it, back up any existing
   `%USERPROFILE%\.pixi\config.toml`, then create or edit that file. Set this
   top-level option **above any `[table]` sections**, replacing an existing value
   rather than adding a duplicate:

   ```toml
   tls-root-certs = "system"
   ```

   Create the `.pixi` folder if needed, and ensure the file is named
   `config.toml`, not `config.toml.txt`. This uses Windows' trusted certificates
   while keeping verification enabled. The setting applies to other Pixi
   projects for the same user too; higher-priority settings and explicit
   certificate environment variables can override it.
4. Close and reopen pHmap again to retry the installation.

Using system certificates addresses certificate-trust problems. It is **not a
verified fix** for the reported `InvalidContentType` failure; a proxy-protocol
or access-policy problem still needs investigation. Do not disable TLS
verification, Zscaler, SentinelOne, or the company VPN to work around it.

#### If setup still fails

Ask IT to review connection and policy logs for the bundled `pixi.exe`, starting
with `conda.anaconda.org:443`. The locked dependencies also use `pypi.org` and
`files.pythonhosted.org`; IT should approve the necessary package-download
access and check any redirects rather than applying a blanket exception.

Have IT check inherited `HTTP_PROXY`/`HTTPS_PROXY` variables and Pixi proxy
configuration. An HTTPS destination may use an `http://` CONNECT proxy; the
proxy URL scheme must match the proxy's actual protocol. Do not guess or
overwrite company-managed proxy settings. See
[Pixi's proxy configuration](https://pixi.prefix.dev/v0.75.0/reference/pixi_configuration/#proxy-config).

Click **View log** in the launcher. With default Windows storage, the log is:

```text
%LOCALAPPDATA%\pHmap\runs\.gui\desktop.log
```

For a follow-up issue, include the app version, the final error and preceding
log lines, and whether an explicit proxy or HTTPS inspection is in use. Redact
credentials, tokens, and sensitive company information. A successful browser
download alone does not establish that Pixi has the same trust or proxy setup.

This report is documented as first-run corporate-network troubleshooting, not
as a verified Windows application fix. If the failure persists after an
IT-approved configuration change, reopen or file an issue with the sanitized
logs and the steps attempted.

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
