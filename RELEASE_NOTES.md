# Android ADB Controller 0.1.1

- Direct front/back phone camera preview (Android 12+).
- Virtual webcam output at 1280×720, 30 fps, with start/stop controls.
- Camera rotation controls saved separately for front and back cameras; rotation applies to preview, webcam output, and snapshots.
- Buttons to rotate the actual phone display and restore auto rotation.
- Scrollable controls sidebar and webcam setup instructions.

## Setup

Windows builds include the webcam backend; install OBS Studio to register OBS Virtual Camera and leave OBS's own virtual camera stopped.
Linux webcam output requires v4l2loopback and the optional Python backend described in README.md.
Phone microphone forwarding and camera video recording are not included; video recording requires Phone screen mode.

## Downloads

- **Windows x64:** extract the ZIP and run `AndroidADBController.exe`.
- **Debian 13+ x86-64:** `sudo apt install ./adb-phone-controller_*_amd64.deb`.
- **Arch:** `sudo pacman -U ./adb-phone-controller-*-any.pkg.tar.zst`.

Checksums, Arch PKGBUILD, and source archive are included.

## Validation status

The new camera, virtual webcam, and phone rotation controls have not yet been tested end to end on a connected phone. Windows real-device interaction also needs testing. Previous camera/video transitions showed intermittent corruption. CI performs package build and launch checks before publishing.
