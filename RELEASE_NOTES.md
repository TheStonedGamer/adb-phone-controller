Android ADB Controller's first release includes:

- Embedded scrcpy video with session-aware decoding and rotation handling.
- Live taps, swipes, mouse-wheel scrolling with momentum, and trackpad scrolling.
- Direct keyboard input, editing keys, and clipboard paste.
- Automatic USB preference and paired Wi-Fi fallback for the same phone.
- Glowing connection indicator and disconnect/reconnect controls.
- Screenshots, video/audio recording, and app management.

Downloads:

- **Windows x64:** extract the ZIP and run `AndroidADBController.exe`. ADB and scrcpy are bundled. A console accompanies the app for recording shutdown.
- **Debian 13+ x86-64:** install the `.deb` with `sudo apt install ./adb-phone-controller_*_amd64.deb`.
- **Arch:** install the `.pkg.tar.zst` with `sudo pacman -U ./adb-phone-controller-*-any.pkg.tar.zst`.

`SHA256SUMS`, the Arch `PKGBUILD`, and its source archive are included. See README.md for setup instructions.

Builds include Windows executable/tool launch checks and installation checks in a clean Debian 13 container. Linux live video has been tested on a Samsung phone. Windows interaction with a real phone still needs testing; camera/video transitions have shown intermittent corruption and need longer testing.
