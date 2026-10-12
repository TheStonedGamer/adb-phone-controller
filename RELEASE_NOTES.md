# Android ADB Controller 0.1.2

- Remembered-device menu with connection status, custom names, quick connect, editable Wi-Fi endpoints, and forgetting/restoring saved phones.
- Migrates the previously remembered phone automatically; uses each phone's saved Wi-Fi address when switching devices.
- GitHub release updater with release notes, platform-specific downloads, and SHA256SUMS verification.
- Debian/Arch system installations offer authenticated package installation. Windows portable and source installations open the download folder for manual installation.
- Phone audio Play/Stop controls and status in the main window, without a separate scrcpy window. Audio stops on disconnect, device switch, and app exit.

## Downloads

- **Windows x64:** extract the ZIP to a new folder and run `AndroidADBController.exe`.
- **Debian 13+ x86-64:** `sudo apt install ./adb-phone-controller_*_amd64.deb`.
- **Arch:** `sudo pacman -U ./adb-phone-controller-*-any.pkg.tar.zst`.

Checksums, Arch PKGBUILD, and source archive are included. See README.md for camera/webcam driver setup.

## Validation status

The new device menu, updater installation flow, and audio controls have not yet been tested end to end with a connected phone or across all platforms. Camera/webcam and phone rotation also need further live-device testing. Previous camera/video transitions showed intermittent corruption. CI performs package build and launch checks before publishing.
