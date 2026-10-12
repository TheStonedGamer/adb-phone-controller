"""User-initiated GitHub release checks and verified package downloads."""
import hashlib
import json
from pathlib import Path
import platform
import sys
import urllib.request
from PySide6.QtCore import QThread, Signal, QProcess, QStandardPaths
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QPlainTextEdit
from adb_phone_controller import __version__

REPOSITORY = 'TheStonedGamer/adb-phone-controller'
API = f'https://api.github.com/repos/{REPOSITORY}/releases/latest'


def request(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers={
        'User-Agent': 'Android-ADB-Controller', 'Accept': 'application/vnd.github+json'}), timeout=30)


def version_tuple(value):
    return tuple(int(n) for n in value.removeprefix('v').split('.'))


def package_suffix():
    if platform.machine().lower() not in ('amd64', 'x86_64'):
        raise RuntimeError('Automatic downloads support x86-64 only. Use the GitHub release page.')
    if sys.platform == 'win32':
        return '-windows-x64.zip'
    release = platform.freedesktop_os_release() if sys.platform == 'linux' else {}
    distro = (release.get('ID', '') + ' ' + release.get('ID_LIKE', '')).split()
    if 'arch' in distro or 'cachyos' in distro:
        return '-any.pkg.tar.zst'
    if 'debian' in distro or 'ubuntu' in distro:
        return '_amd64.deb'
    raise RuntimeError('No package matches this system. Use the GitHub release page.')


class ReleaseWorker(QThread):
    result = Signal(object)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(self, release=None):
        super().__init__()
        self.release = release

    def run(self):
        try:
            if self.release is None:
                with request(API) as response:
                    release = json.loads(response.read(2 * 1024 * 1024))
                version_tuple(release['tag_name'])
                self.result.emit(release)
                return
            suffix = package_suffix()
            assets = self.release['assets']
            asset = next((a for a in assets if a['name'].startswith('adb-phone-controller') and a['name'].endswith(suffix)), None)
            checksums = next((a for a in assets if a['name'] == 'SHA256SUMS'), None)
            if not asset or not checksums:
                raise RuntimeError('This release does not contain a matching package and checksums.')
            # Only fetch assets from this repository's release download paths.
            prefix = f'https://github.com/{REPOSITORY}/releases/download/'
            if any(not a['browser_download_url'].startswith(prefix) for a in (asset, checksums)):
                raise RuntimeError('Unexpected release download URL.')
            name = asset['name']
            if Path(name).name != name or '/' in name or '\\' in name:
                raise RuntimeError('Invalid package filename.')
            with request(checksums['browser_download_url']) as response:
                lines = response.read(1024 * 1024).decode().splitlines()
            expected = next((line.split()[0] for line in lines if len(line.split()) == 2 and line.split()[1] == name), None)
            if not expected or len(expected) != 64:
                raise RuntimeError('Package checksum is missing.')
            downloads = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation) or Path.home() / 'Downloads')
            folder = downloads / 'AndroidADBController' / self.release['tag_name']
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / name
            partial = target.with_name(target.name + '.part')
            digest = hashlib.sha256()
            total = 0
            try:
                with request(asset['browser_download_url']) as response, partial.open('wb') as output:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > 2 * 1024**3:
                            raise RuntimeError('Package exceeds download size limit.')
                        output.write(chunk)
                        digest.update(chunk)
                        self.progress.emit(f'Downloading… {total / 1024**2:.1f} MB')
                if digest.hexdigest() != expected.lower():
                    raise RuntimeError('Checksum mismatch. Download discarded.')
                partial.replace(target)
            finally:
                partial.unlink(missing_ok=True)
            self.result.emit(target)
        except Exception as exc:
            self.failed.emit(str(exc))


class UpdateDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('App updates')
        self.resize(510, 400)
        self.release = None
        self.worker = None
        self.download = None
        self.installer = None
        layout = QVBoxLayout(self)
        heading = QLabel('Keep your controller up to date')
        heading.setStyleSheet('font-size:20px;font-weight:600;')
        layout.addWidget(heading)
        layout.addWidget(QLabel(f'Installed version: {__version__}'))
        self.status = QLabel('Check GitHub for the latest published release.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.notes = QPlainTextEdit()
        self.notes.setReadOnly(True)
        layout.addWidget(self.notes, 1)
        self.check = QPushButton('Check for updates')
        self.check.clicked.connect(self.check_release)
        layout.addWidget(self.check)
        self.get = QPushButton('Download update')
        self.get.setEnabled(False)
        self.get.clicked.connect(self.download_release)
        layout.addWidget(self.get)
        self.install = QPushButton('Install downloaded update')
        self.install.setEnabled(False)
        self.install.clicked.connect(self.install_update)
        layout.addWidget(self.install)
        page = QPushButton('View releases on GitHub')
        page.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(f'https://github.com/{REPOSITORY}/releases')))
        layout.addWidget(page)

    def start_worker(self, release=None):
        if self.worker and self.worker.isRunning():
            return
        self.check.setEnabled(False)
        self.get.setEnabled(False)
        self.worker = ReleaseWorker(release)
        self.worker.progress.connect(self.status.setText)
        self.worker.failed.connect(self.failed)
        self.worker.result.connect(self.received)
        self.worker.finished.connect(lambda: self.check.setEnabled(True))
        self.worker.start()

    def check_release(self):
        self.status.setText('Checking GitHub…')
        self.start_worker()

    def download_release(self):
        self.status.setText('Preparing download…')
        self.start_worker(self.release)

    def failed(self, message):
        self.status.setText(message)
        self.get.setEnabled(self.release is not None)

    def received(self, result):
        if isinstance(result, dict):
            self.release = result
            self.notes.setPlainText(result.get('body', ''))
            newer = version_tuple(result['tag_name']) > version_tuple(__version__)
            self.status.setText(f"Update available: {result['tag_name']}" if newer else 'You have the latest release or a newer development build.')
            self.get.setEnabled(newer)
            if not newer:
                self.release = None
        else:
            self.download = result
            self.status.setText(f'Checksum verified. Saved to {result}')
            self.install.setEnabled(True)

    def install_update(self):
        if not self.download:
            return
        if self.worker and self.worker.isRunning():
            self.status.setText("Wait for the download to finish.")
            return
        if sys.platform == 'win32' or not str(Path(__file__).resolve()).startswith('/usr/lib/adb-phone-controller/'):
            # Portable ZIPs/source installs stay user-managed. Never replace a
            # running executable or silently install a second system copy.
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.download.parent)))
            self.status.setText('Open the downloaded package to install. For the Windows ZIP, extract it to a new folder and launch AndroidADBController.exe.')
            return
        if self.installer and self.installer.state() != QProcess.ProcessState.NotRunning:
            return
        command = ['apt', 'install', '-y', str(self.download)] if self.download.suffix == '.deb' else ['pacman', '-U', '--noconfirm', str(self.download)]
        self.installer = QProcess(self)
        self.installer.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.installer.readyReadStandardOutput.connect(lambda: self.notes.appendPlainText(bytes(self.installer.readAllStandardOutput()).decode(errors='replace')))
        self.installer.finished.connect(self.installed)
        self.installer.errorOccurred.connect(lambda _: self.failed(self.installer.errorString()))
        self.install.setEnabled(False)
        self.status.setText('Installing… authenticate when prompted.')
        self.installer.start('pkexec', command)

    def installed(self, code, _status):
        self.status.setText('Update installed. Close and reopen the controller.' if code == 0 else 'Installation failed or was cancelled. See output above.')
        self.install.setEnabled(code != 0)

    def closeEvent(self, event):
        if (self.worker and self.worker.isRunning()) or (self.installer and self.installer.state() != QProcess.ProcessState.NotRunning):
            self.status.setText('Wait for the current operation to finish before closing.')
            event.ignore()
        else:
            super().closeEvent(event)
