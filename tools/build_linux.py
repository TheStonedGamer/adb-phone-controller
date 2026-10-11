"""Build a Debian 13+ source-based package or an Arch package with system dependencies."""
import argparse
import hashlib
import io
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
NAME = 'adb-phone-controller'
VERSION = '0.1.0'

def tar_bytes(directory):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz', format=tarfile.GNU_FORMAT) as archive:
        for path in sorted(directory.iterdir()):
            archive.add(path, arcname=path.name)
    return buffer.getvalue()

def ar_member(name, data):
    header = f'{name + "/":<16}{0:<12}{0:<6}{0:<6}{"100644":<8}{len(data):<10}`\n'.encode()
    return header + data + (b'\n' if len(data) % 2 else b'')

parser = argparse.ArgumentParser()
parser.add_argument('--deb', action='store_true')
parser.add_argument('--arch', action='store_true')
args = parser.parse_args()
if not args.deb and not args.arch:
    parser.error('select --deb and/or --arch')
subprocess.run(['python3', str(ROOT / 'tools/fetch_vendor.py')], check=True)
dist = ROOT / 'dist'
dist.mkdir(exist_ok=True)
if args.deb:
    subprocess.run(['python3', str(ROOT / 'tools/fetch_vendor.py'), '--linux'], check=True)
    with tempfile.TemporaryDirectory() as temp:
        staging = Path(temp)
        data = staging / 'data'
        control = staging / 'control'
        control.mkdir()
        shutil.copytree(ROOT / 'src/adb_phone_controller', data / 'usr/lib' / NAME / 'adb_phone_controller', ignore=shutil.ignore_patterns('__pycache__'))
        files = [('packaging/linux-launcher', 'usr/bin/adb-phone-control', 0o755),
                 ('assets/adb-phone-controller.desktop', 'usr/share/applications/adb-phone-controller.desktop', 0o644),
                 ('README.md', f'usr/share/doc/{NAME}/README.md', 0o644),
                 ('THIRD_PARTY.md', f'usr/share/doc/{NAME}/THIRD_PARTY.md', 0o644)]
        for source, target, mode in files:
            dest = data / target
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / source, dest)
            dest.chmod(mode)
        (control / 'control').write_text(f'''Package: {NAME}
Version: {VERSION}-1
Architecture: amd64
Maintainer: Android ADB Controller contributors
Depends: python3 (>= 3.11), python3-av (>= 14), python3-pyside6.qtwidgets, libudev1, libgcc-s1, libc6 (>= 2.35)
Recommends: android-sdk-platform-tools-common
Section: utils
Priority: optional
Description: Android screen mirroring and control over USB or Wi-Fi
 Embedded scrcpy video, native touch gestures, recording, and automatic transport selection.
''')
        package = b'!<arch>\n' + ar_member('debian-binary', b'2.0\n') + ar_member('control.tar.gz', tar_bytes(control)) + ar_member('data.tar.gz', tar_bytes(data))
        output = dist / f'{NAME}_{VERSION}-1_amd64.deb'
        output.write_bytes(package)
        print(output)
if args.arch:
    subprocess.run(['python3', str(ROOT / 'tools/fetch_vendor.py')], check=True)
    source = ROOT / 'packaging' / f'{NAME}-{VERSION}.tar.gz'
    with tarfile.open(source, 'w:gz') as archive:
        for name in ['src/adb_phone_controller', 'packaging/linux-launcher', 'assets', 'README.md', 'THIRD_PARTY.md']:
            archive.add(ROOT / name, arcname=f'{NAME}-{VERSION}/{name}', filter=lambda item: None if '__pycache__' in item.name else item)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    pkgbuild = (ROOT / 'packaging/PKGBUILD').read_text().replace("sha256sums=('SKIP')", f"sha256sums=('{digest}')")
    with tempfile.TemporaryDirectory(prefix='adb-controller-arch-') as temp:
        stage = Path(temp)
        shutil.copy(source, stage / source.name)
        (stage / 'PKGBUILD').write_text(pkgbuild)
        subprocess.run(['makepkg', '--nodeps', '--force'], cwd=stage, check=True)
        for package in stage.glob('*.pkg.tar.*'):
            shutil.copy(package, dist / package.name)
        shutil.copy(stage / 'PKGBUILD', dist / 'PKGBUILD')
        shutil.copy(source, dist / source.name)
