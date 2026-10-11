"""Run on Windows x64 to build a portable ZIP with official scrcpy/ADB binaries."""
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if sys.platform != 'win32':
    raise SystemExit('Build the Windows artifact on Windows (or use the GitHub Actions workflow).')
subprocess.run([sys.executable, str(ROOT / 'tools/fetch_vendor.py'), '--windows'], check=True)
# Keep a console attached so scrcpy recording receives CTRL_BREAK and finalizes
# MP4/MKV files correctly. A future GUI-only launcher can own a hidden console.
subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--console',
                '--name', 'AndroidADBController', '--paths', str(ROOT / 'src'),
                '--collect-all', 'av', '--collect-submodules', 'adb_phone_controller',
                '--add-data', f'{ROOT / "src/adb_phone_controller/vendor"};vendor',
                str(ROOT / 'packaging/windows-entry.py')], cwd=ROOT, check=True)
folder = ROOT / 'dist/AndroidADBController'
for name in ['README.md', 'THIRD_PARTY.md']:
    shutil.copy(ROOT / name, folder / name)
shutil.make_archive(str(ROOT / 'dist/adb-phone-controller-0.1.0-windows-x64'), 'zip', folder.parent, folder.name)
