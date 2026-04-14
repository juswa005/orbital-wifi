# Orbital WiFi

`orbital-wifi` is a NASA-themed terminal UI for WiFi management on Linux. It is built on top of NetworkManager and `nmcli`, with a focus on being fast, dependable, and practical enough to replace tools like `wifitui` for everyday use.

## Features

- Live WiFi scan view with signal strength, security, BSSID, and active network markers
- Saved profile view for reconnecting or deleting remembered WiFi connections
- Password prompt for secured networks
- Hidden network support
- WiFi radio toggle, disconnect action, interface cycling, and manual rescan
- No runtime dependencies beyond Python and `nmcli`
- Packaged with a console entry point and basic tests so it is ready to publish

## Requirements

- Linux
- Python 3.11+
- NetworkManager with `nmcli` available on `PATH`
- A terminal with curses support

## Install

### From source with `pipx`

```bash
pipx install .
```

### Local development install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Usage

```bash
orbital-wifi
```

Select a specific wireless interface:

```bash
orbital-wifi --interface wlan0
```

## Keybindings

### Global

- `q`: quit
- `Tab`: switch between scanned networks and saved profiles
- `r`: refresh data
- `w`: toggle WiFi radio
- `i`: cycle wireless interfaces

### Networks view

- `Up` / `Down`: move selection
- `Enter`: connect to the selected network
- `d`: disconnect the current wireless interface
- `h`: connect to a hidden network

### Profiles view

- `Up` / `Down`: move selection
- `Enter`: activate the selected saved profile
- `x`: delete the selected saved profile

## Release Notes

This project is set up as a standard Python package:

- `pyproject.toml` defines package metadata and the `orbital-wifi` console script
- `src/orbital_wifi/` contains the application code
- `tests/` contains parser and sorting tests that do not need live WiFi hardware

Build a distributable package with:

```bash
python3 -m build
```

Tagged releases are wired into GitHub Actions through `.github/workflows/release.yml`. Pushing a tag like `v0.1.0` runs the test suite, builds the source distribution and wheel, generates `SHA256SUMS.txt`, and attaches the artifacts to the GitHub release.

## Arch Linux / AUR

Packaging files are included in `packaging/aur/`:

- `packaging/aur/PKGBUILD`
- `packaging/aur/.SRCINFO`

Before submitting the stable package to the AUR:

1. Push a matching Git tag such as `v0.1.0`.
2. Wait for the GitHub release workflow to publish the source tarball.
3. Replace `REPLACE_WITH_RELEASE_TARBALL_SHA256` in both packaging files with the real tarball checksum.
4. Copy those files into your AUR package repository root.

Example checksum command:

```bash
curl -L https://github.com/juswa005/orbital-wifi/archive/refs/tags/v0.1.0.tar.gz | sha256sum
```

Then regenerate `.SRCINFO` from the AUR repo with:

```bash
makepkg --printsrcinfo > .SRCINFO
```

## Design Notes

Orbital WiFi intentionally leans on `nmcli` instead of talking to NetworkManager over DBus directly. That keeps the install path simple, makes debugging easier, and matches the behavior users expect from existing terminal-first WiFi tools.

The visual language borrows from mission-control dashboards: status banners, telemetry-style panels, and compact operator-focused shortcuts instead of decorative chrome.

## Limitations

- This currently targets NetworkManager-managed Linux systems.
- Permission prompts are delegated to your local NetworkManager and polkit setup.
- Hidden networks can be connected manually, but they will only be listed automatically if NetworkManager reports them.

## License

MIT
