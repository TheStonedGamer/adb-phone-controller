# Android ADB Controller 0.2.0

## Device toolbox

- File and folder uploads, including drag-and-drop into the device file browser. Uploads stage items before moving them into place and refuse to replace existing phone files.
- Optional text clipboard synchronization in both directions with the active phone.
- APK export including split APKs; private app data is not included.
- App management: package search, launch, force stop, inspect permissions, clear data with confirmation, and clear cache when Android supports it.
- Dashboard showing battery, charging, model, Android version, memory, and storage.
- Notification and Quick Settings panel controls.
- Live logcat viewer with text/error filters, crash-buffer capture, and filtered log export.
- Saved automation shortcuts for touch gestures, typing, navigation keys, app launches, and delays. Replay stops on device/stream changes.
- Wireless ADB setup from an authorized USB connection, endpoint saving, and restoration of USB mode.
- Multiple live device tabs with independent scrcpy sessions and forwarding ports. Existing tabs remain open when selecting another phone.
- Connection diagnostics for authorization, offline devices, wireless discovery support, TCP reachability, and Linux VPN/routing conflicts.

Open **Device tools…**, **Shortcuts…**, or the **Device** menu for the new tools.
Clipboard synchronization is optional and off by default. File transfers and tools
windows remain tied to the device named in their header/title.

## Downloads

- **Windows x64:** extract the ZIP into a new folder and launch `AndroidADBController.exe`.
- **Debian 13+ x86-64:** `sudo apt install ./adb-phone-controller_*_amd64.deb`.
- **Arch:** `sudo pacman -U ./adb-phone-controller-*-any.pkg.tar.zst`.

Checksums, Arch PKGBUILD, and source archive are included. See README.md for complete usage and webcam driver setup.

## Validation status and limitations

Python syntax checks passed. The new toolbox features have not been tested end to end on real devices or across all packaged platforms. CI performs the existing package build and launch checks before publishing. APK export does not back up private app data, per-app cache clearing depends on Android's available commands, and clipboard access may be restricted. Camera/webcam, call capture, and screen-power behavior still need further live testing; Android may reject call capture or return silence. Previous camera/video transitions showed intermittent corruption.
