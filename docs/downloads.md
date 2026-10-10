# Download pHmap

Get the desktop app from the [pHmap GitHub Releases page](https://github.com/riccstick/pHmap/releases).
Choose an asset matching your operating system and processor. Releases marked
as pre-releases are test builds and may change before a stable release.

| Platform | Asset name | Start the app |
| --- | --- | --- |
| macOS, Apple Silicon | `pHmap-<version>-macos-arm64.zip` | Extract, then open `pHmap.app`. |
| macOS, Intel | `pHmap-<version>-macos-x64.zip` | Extract, then open `pHmap.app`. |
| Windows, x64 | `pHmap-<version>-windows-x64.zip` | Extract the whole archive, then open `pHmap.exe`. |
| Linux, x64 | `pHmap-<version>-linux-x64.tar.gz` | Extract the archive, then open the `pHmap` executable. |

The desktop app downloads the locked scientific environment on first launch,
so the initial setup needs internet access and can take several minutes. The
calculation interface runs locally; your input files and results are not sent
to a pHmap server.

These initial desktop builds are not code-signed or notarized. macOS Gatekeeper
or Windows SmartScreen may show a warning. Read the [release notes and
troubleshooting guide](releases.md) before proceeding, and only install builds
you trust. Linux packages target x64 desktop systems and are not universal
Linux or ARM builds.

For step-by-step usage, start with the [tutorial](tutorial.md). The full
[release guide](releases.md) covers app storage, first-run setup, and known
platform limitations.
