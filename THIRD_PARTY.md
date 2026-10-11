# Third-party components

The application uses PySide6/Qt (LGPLv3/GPLv3/commercial) and PyAV (BSD-3-Clause).
PyAV wheels include FFmpeg libraries; their license files are included in the wheel
and collected into the Windows distribution by PyInstaller.

The bundled scrcpy server and Windows scrcpy distribution are official, unmodified
Genymobile scrcpy 5.0.1 release artifacts (Apache-2.0). The Windows upstream archive
also supplies ADB and runtime libraries. Preserve its included license files.

Sources, licenses, and upstream distribution notices:

- scrcpy: https://github.com/Genymobile/scrcpy/tree/v5.0.1
- Android platform tools: https://developer.android.com/tools/releases/platform-tools
- PySide6 / Qt: https://www.qt.io/qt-for-python and https://code.qt.io/
- PyAV: https://github.com/PyAV-Org/PyAV
- FFmpeg: https://ffmpeg.org/legal.html
- PyInstaller: https://pyinstaller.org/en/stable/license.html

Linux packages use distribution-provided Qt, PyAV/FFmpeg, and ADB. Arch uses its
repository scrcpy package; Debian bundles the official Linux x86-64 scrcpy release
and its included license notices.
The application's license has not been selected; no open-source license grant is
implied for the original application code. Select one before public distribution.

Webcam output uses pyvirtualcam (MIT) and NumPy (BSD-3-Clause). Windows builds
collect their package data and license notices with PyInstaller.
- pyvirtualcam: https://github.com/letmaik/pyvirtualcam
- NumPy: https://numpy.org/doc/stable/license.html
