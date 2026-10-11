# Android ADB Controller

A PySide6 desktop application for Android control over USB or paired Wi-Fi, with
embedded scrcpy video, live touch gestures, screenshots, recording, APK installation,
and automatic USB preference / Wi-Fi fallback for the same phone.

## Install

- **Windows x64:** extract the release ZIP and run `AndroidADBController.exe`.
  ADB and scrcpy are included. A console accompanies the UI so recording can stop
  gracefully; close the UI to exit. Some phones need an OEM USB driver.
- **Debian 13 or newer:** `sudo apt install ./adb-phone-controller_0.1.0-1_all.deb`.
  APT resolves dependencies; older distributions may not provide compatible PySide6
  or PyAV packages. Debian 12 is not a supported package target.
- **Arch Linux:** `sudo pacman -U adb-phone-controller-0.1.0-1-any.pkg.tar.zst`.
  Dependencies come from the configured repositories.

Enable USB debugging and accept Android's authorization prompt. For Wi-Fi, enable
Wireless debugging and pair through **Connect over Wi-Fi**. The app remembers the
endpoint and matches transports by the phone's hardware serial. USB takes priority.
Changed Android wireless debugging ports must be entered again if ADB has not
already discovered/connected the new endpoint. Disconnect pauses auto reconnect.

## Develop

Requires Python 3.11+, ADB, and scrcpy. The embedded video client speaks scrcpy 5's
session protocol. Builds bundle the pinned 5.0.1 server independently of the system
scrcpy CLI, which is used for recording/audio.

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
python tools/fetch_vendor.py
adb-phone-control
```

On Windows, use `.venv\Scripts\activate` and `python tools/fetch_vendor.py --windows`.
Configuration uses the platform app config folder on Windows and
`$XDG_CONFIG_HOME/adb-phone-control.ini` (normally `~/.config/`) on Linux.

## Build packages

```sh
python tools/build_linux.py --deb
python tools/build_linux.py --arch  # on Arch, with makepkg and base-devel
```

Linux packages install Python sources, verified scrcpy server, desktop entry, and
launcher. Python dependencies are distribution packages, so these artifacts do not
embed a host-specific glibc build. Arch packaging uses `--nodeps` because staging
only copies files; installation still enforces all declared runtime dependencies.

On Windows:

```powershell
python -m pip install pyinstaller "PySide6>=6.4,<7" "av>=14,<20"
python tools/build_windows.py
```

Artifacts are written to `dist/`. `.github/workflows/packages.yml` builds all three
platforms on their native runners. Tags `v*` also create a draft GitHub release.

## Current verification limits

Linux video decoding, transport selection, touch event serialization, and indicator
states were checked locally. Camera/video transitions have previously shown colour
corruption; the session-aware decoder needs longer testing. Windows runtime behavior
and Debian installation require native validation. Packaging is an initial preview.
