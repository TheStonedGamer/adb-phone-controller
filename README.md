# Android ADB Controller

A PySide6 desktop application for Android control over USB or paired Wi-Fi, with
embedded scrcpy video, live touch gestures, screenshots, recording, APK installation,
and automatic USB preference / Wi-Fi fallback for the same phone.

## Install

- **Windows x64:** extract the release ZIP and run `AndroidADBController.exe`.
  ADB and scrcpy are included. A console accompanies the UI so recording can stop
  gracefully; close the UI to exit. Some phones need an OEM USB driver.
- **Debian 13 or newer (x86-64):** `sudo apt install ./adb-phone-controller_0.1.0-1_amd64.deb`.
  APT resolves dependencies; older distributions may not provide compatible PySide6
  or PyAV packages. Debian 12 is not a supported package target.
- **Arch Linux:** `sudo pacman -U adb-phone-controller-0.1.0-1-any.pkg.tar.zst`.
  Dependencies come from the configured repositories.

Enable USB debugging and accept Android's authorization prompt. For Wi-Fi, enable
Wireless debugging and pair through **Connect over Wi-Fi**. The app remembers the
endpoint and matches transports by the phone's hardware serial. USB takes priority.
Changed Android wireless debugging ports must be entered again if ADB has not
already discovered/connected the new endpoint. Disconnect pauses auto reconnect.

Move the pointer over the phone image to scroll with the mouse wheel or trackpad.
Mouse-wheel scrolling glides and eases to a stop; trackpad momentum is preserved
without adding a second inertial tail. Clicking the phone stops the glide. Horizontal
scrolling and Shift+wheel are supported, including a rotated preview.

## Type directly on the phone

Click a text field in the phone preview, then type on your computer keyboard.
Backspace, Delete, Enter, Tab, arrows, Home/End, and Ctrl+A are forwarded to Android.
Ctrl+V pastes the computer clipboard, including multiline and Unicode text.
Paste and non-ASCII typing update the phone clipboard. Click a controller input
field to return keyboard focus to the desktop controls.

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
launcher. Debian also bundles the official Linux x86-64 scrcpy client because it
is absent from Debian 13 main; Arch uses its repository client. Python dependencies are distribution packages, so these artifacts do not
embed a host-specific glibc build. Arch packaging uses `--nodeps` because staging
only copies files; installation still enforces all declared runtime dependencies.

On Windows:

```powershell
python -m pip install pyinstaller "PySide6>=6.4,<7" "av>=14,<20"
python tools/build_windows.py
```

Artifacts are written to `dist/`. `.github/workflows/packages.yml` builds all three
platforms on their native runners. Tags `v*` publish a GitHub release only after all platform builds and checks succeed.

## Current verification limits

Linux video decoding, transport selection, touch event serialization, and indicator
states were checked locally. CI also installs the package on Debian 13 and checks
the frozen executable and bundled tools on Windows. Camera/video transitions have previously shown colour
corruption; the session-aware decoder needs longer testing. Windows phone interaction and full Debian desktop behavior require native validation. Packaging is an initial preview.

## Publish a release

1. Update `__version__` in `src/adb_phone_controller/__init__.py`.
2. Update `RELEASE_NOTES.md`, commit, and push `main`.
3. Create and push the matching tag: `git tag v0.1.0 && git push origin v0.1.0`.

CI rejects tags that do not match the source version. It builds all three packages,
checks Windows executables and Debian installation, generates SHA256SUMS, uploads
assets, and publishes the release. An existing draft for that tag is updated and
published. Manual workflow runs build artifacts without publishing a release.

## Phone camera and webcam

Select **Back camera** or **Front camera** in **Camera / webcam** to show a direct
camera feed (Android 12+). Select **Phone screen** to return to touch control.
Use **Camera rotation** (0°, 90°, 180°, 270°) or the rotation buttons to turn
the preview and webcam output together without restarting the feed. Rotation is
saved separately for front camera, back camera, and phone screen. Screenshot saves
the rotated camera view;
video recording currently requires Phone screen mode. Close other phone apps using
the camera if capture fails.

**Start webcam** publishes the selected camera as a 1280×720, 30 fps virtual camera.
Choose that camera in your video calling application. Portrait feeds have black
side margins. Microphone audio is not forwarded. Stop webcam releases the camera;
changing source, restarting the stream, disconnecting, and closing the app also
stop output. Start webcam again after reconnecting.

### Windows

The portable build includes the Python webcam backend. Install OBS Studio once to
register **OBS Virtual Camera**. Keep OBS's own virtual camera stopped, click
Start webcam here, then select OBS Virtual Camera in your calling app. A virtual
camera driver is required; the app does not install drivers automatically.

### Linux (Debian and Arch)

Install the v4l2loopback module matching your kernel (Debian:
`sudo apt install v4l2loopback-dkms linux-headers-$(uname -r) python3-pip`;
Arch: install `v4l2loopback-dkms` and your kernel's headers).
Load it with:

```sh
sudo modprobe v4l2loopback devices=1 exclusive_caps=1 card_label=PhoneCamera
```

For a source install, install the optional dependencies into the app's venv with
`python -m pip install '.[webcam]'`. For Debian/Arch packages, install the backend
into the app's dedicated optional dependency directory:

```sh
python3 -m pip install --target "${XDG_DATA_HOME:-$HOME/.local/share}/adb-phone-controller/webcam" 'pyvirtualcam>=0.15,<0.16' 'numpy>=1.26,<3'
```

Leave the virtual camera device field empty for automatic selection, or enter the
loopback device path, such as `/dev/video10`. Your user needs write access to that
device. Select **PhoneCamera** in the calling application after starting output.

Driver/backend documentation: https://github.com/letmaik/pyvirtualcam#supported-virtual-cameras

### Rotate the phone display

**Rotate phone ↻** turns the actual phone display by 90° and locks its orientation.
**Auto rotate phone** restores sensor rotation. Apps that require a particular
orientation may override the request. These controls change the phone's rotation
setting; camera rotation and the preview rotation controls remain separate.

## Remembered devices

Open **Your devices…** beside the device selector for saved phones, availability,
and USB/Wi-Fi connection status. Phones are saved automatically after ADB discovers
them; the previously selected phone is migrated on first launch. Rename entries,
update a saved Wireless debugging address/port, double-click to connect, or forget
an entry. **Remember connected phones** restores forgotten entries. Forgetting an
entry does not revoke Android's ADB pairing. Pairing codes are not stored.

## Updates

Use **Help → Check for updates…** to check the latest published GitHub release,
read its notes, and download the package for your platform. Downloads are verified
against the release's SHA256SUMS and saved under Downloads/AndroidADBController.
Debian/Arch system installations can install the verified package after an
administrator authentication prompt; restart the controller afterward. Windows
portable and source installations open the download folder for manual installation.
For Windows, extract the ZIP into a new folder and launch its executable.
No automatic background installation occurs.

## Audio in the controller

The **Phone audio** section has Play/Stop controls and playback status. Playback
runs without an extra scrcpy window and stops when disconnecting, switching phones,
or closing the controller. This forwards phone output, not its microphone; some
protected audio may be unavailable. The phone may stop playing audio locally while
forwarding is enabled.

## Arrow keys for short videos

Click the phone preview to focus it. With **↑ / ↓ navigate short videos** enabled,
**Down** swipes up to the next video and **Up** swipes down to the previous video
in Shorts, TikTok, Reels, and similar feeds. Each press sends one 200 ms touch
swipe; holding a key does not skip multiple videos. Swipes follow the preview's
rotation and are cancelled when switching devices or changing rotation. Turn the
option off for normal arrow-key editing in phone text fields. Modified arrow keys
and Left/Right keep their normal Android key behavior. This setting is remembered.

### Call audio (device-dependent)

In **Phone audio**, choose **Call — both sides**, **Call — your voice**, or
**Call — caller's voice**, then click Play phone audio during a call. These map to
scrcpy's `voice-call`, `voice-call-uplink`, and `voice-call-downlink` sources.
Changing the source while playing restarts the audio session. The selection is
remembered; there is no automatic fallback to a microphone or another source.

To save call audio with a screen recording, choose the corresponding source under
**Record video + audio → Settings → Audio source**, then start recording in Phone
screen mode. This recording setting is separate from the live listening source.

Android/firmware may deny these sources or deliver silence. A running session
confirms that the process started, not that both voices are audible. Support has
not been verified on the Samsung phone. See
[scrcpy audio sources](https://github.com/Genymobile/scrcpy/blob/v5.0.1/doc/audio.md#source).

## Browse and download phone files

Click **Files…** or **Device → Browse files…**. Start in internal storage, use the
Downloads/Camera/Pictures shortcuts, double-click folders, or enter an absolute
phone folder path. **Storage devices** opens `/storage` to find accessible SD cards.
Select multiple files or folders and click **Download selected…** to choose a local
destination. Folders download recursively. Existing files are preserved by choosing
another destination name. **Cancel transfer** removes the incomplete current item
while keeping previously completed items; **Open download folder** shows the result.
The browser is tied to the phone shown in its header, even if the main window
switches devices. Only ADB-readable files are accessible; private app data and some
Android/data folders may be restricted by Android. No phone files are modified.

## Use the phone with its physical screen off

In Phone screen mode, click **Screen off** to turn off the physical display while
keeping the embedded live view and touch/keyboard controls active. **Screen on**
restores the display; the phone's physical power button can also turn it on. These
commands control display power rather than sending a lock-screen power key.
Stopping the feed, disconnecting, or closing the app attempts to turn the display
back on if this session requested it off. A lost connection may prevent that
request from reaching the phone. Normal Android lock/timeout policies still apply.
