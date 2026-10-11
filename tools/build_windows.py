"""Run on Windows x64 to build a portable ZIP with official scrcpy/ADB binaries."""
from pathlib import Path
import shutil
import subprocess
import sys
from version import app_version

VERSION = app_version()

ROOT = Path(__file__).resolve().parents[1]
if sys.platform != 'win32':
    raise SystemExit('Build the Windows artifact on Windows (or use the GitHub Actions workflow).')
subprocess.run([sys.executable, str(ROOT / 'tools/fetch_vendor.py'), '--windows'], check=True)
# Keep a console attached so scrcpy recording receives CTRL_BREAK and finalizes
# MP4/MKV files correctly. A future GUI-only launcher can own a hidden console.
subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--console',
                '--name', 'AndroidADBController', '--paths', str(ROOT / 'src'),
                '--collect-all', 'av', '--collect-all', 'pyvirtualcam', '--collect-all', 'numpy', '--collect-submodules', 'adb_phone_controller',
                '--add-data', f'{ROOT / "src/adb_phone_controller/vendor"};vendor',
                str(ROOT / 'packaging/windows-entry.py')], cwd=ROOT, check=True)
folder = ROOT / 'dist/AndroidADBController'
# Exercise the frozen executable and its native imports on the Windows runner.
subprocess.run([str(folder / 'AndroidADBController.exe'), '--version'], check=True, timeout=60)
tools = folder / '_internal/vendor/scrcpy'
subprocess.run([str(tools / 'adb.exe'), 'version'], check=True, timeout=30)
subprocess.run([str(tools / 'scrcpy.exe'), '--version'], check=True, timeout=30)
for name in ['README.md', 'THIRD_PARTY.md']:
    shutil.copy(ROOT / name, folder / name)
shutil.make_archive(str(ROOT / f'dist/adb-phone-controller-{VERSION}-windows-x64'), 'zip', folder.parent, folder.name)
