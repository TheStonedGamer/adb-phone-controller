"""Fetch official scrcpy artifacts and verify their published SHA256 checksums."""
import argparse
import hashlib
from pathlib import Path
import urllib.request
import zipfile

VERSION = "5.0.1"
BASE = f"https://github.com/Genymobile/scrcpy/releases/download/v{VERSION}/"
ROOT = Path(__file__).resolve().parents[1]

def download(name):
    sums = urllib.request.urlopen(BASE + "SHA256SUMS.txt").read().decode()
    expected = next(line.split()[0] for line in sums.splitlines() if line.split()[-1].lstrip('*') == name)
    data = urllib.request.urlopen(BASE + name).read()
    if hashlib.sha256(data).hexdigest() != expected:
        raise RuntimeError(f"Checksum mismatch: {name}")
    return data

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--windows", action="store_true")
    parser.add_argument("--linux", action="store_true")
    args = parser.parse_args()
    vendor = ROOT / "src/adb_phone_controller/vendor"
    vendor.mkdir(parents=True, exist_ok=True)
    (vendor / "scrcpy-server").write_bytes(download(f"scrcpy-server-v{VERSION}"))
    if not args.windows and not args.linux:
        import shutil
        shutil.rmtree(vendor / "scrcpy", ignore_errors=True)
    if args.windows:
        import io
        archive = zipfile.ZipFile(io.BytesIO(download(f"scrcpy-win64-v{VERSION}.zip")))
        target = vendor / "scrcpy"
        target.mkdir(exist_ok=True)
        for item in archive.infolist():
            if item.is_dir():
                continue
            # Flat upstream directory; never trust archive paths as output paths.
            (target / Path(item.filename).name).write_bytes(archive.read(item))
    if args.linux:
        import io
        import tarfile
        target = vendor / "scrcpy"
        target.mkdir(exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(download(f"scrcpy-linux-x86_64-v{VERSION}.tar.gz")), mode="r:gz") as archive:
            for item in archive.getmembers():
                if not item.isfile():
                    continue
                dest = target / Path(item.name).name
                dest.write_bytes(archive.extractfile(item).read())
                dest.chmod(item.mode & 0o777)
    print(f"Verified scrcpy {VERSION} artifacts installed in {vendor}")
