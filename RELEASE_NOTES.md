# Android ADB Controller 0.1.3

- Screen off/on buttons control the phone's physical display while mirroring and input remain active; stopping the session attempts to restore the display.
- Android file browser with folder shortcuts, multi-select file/folder downloads, cancellation, and protection against replacing existing local files.
- Fixed directory-listing command quoting and error handling. Listings were confirmed on a connected Samsung phone.
- Up/Down arrow navigation for Shorts, TikTok, Reels, and similar feeds using touch swipes; optional and remembered.
- Device-dependent call-audio sources (both sides, uplink, downlink) for listening and screen recording.
- Remember successful pairings immediately and preserve existing phones, including when Wi-Fi endpoints are reused.

Includes the remembered-device menu, verified GitHub release downloads, integrated audio controls, camera preview/webcam output, and rotation controls from earlier releases.

## Downloads

- **Windows x64:** extract the ZIP to a new folder and run `AndroidADBController.exe`.
- **Debian 13+ x86-64:** `sudo apt install ./adb-phone-controller_*_amd64.deb`.
- **Arch:** `sudo pacman -U ./adb-phone-controller-*-any.pkg.tar.zst`.

Checksums, Arch PKGBUILD, and source archive are included. See README.md for webcam driver setup and feature instructions.

## Validation status

Directory listings were confirmed against internal storage, Downloads, and DCIM on a connected Samsung phone; missing-directory errors were also confirmed. Downloads, screen power controls, multi-phone pairing, short-video navigation, and call capture still need live testing. Android may deny call capture or return silence. Windows real-device use and camera/webcam behavior require further testing. Previous camera/video transitions showed intermittent corruption. CI performs package build and launch checks before publishing.
