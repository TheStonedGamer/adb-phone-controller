"""Browse ADB-readable storage and download without blocking the UI."""
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
import uuid
from PySide6.QtCore import Qt, QThread, Signal, QStandardPaths, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTreeWidget, QTreeWidgetItem, QFileDialog, QAbstractItemView,
)


def local_name(name):
    # Windows filenames cannot contain these characters. Always produce one
    # basename, never a local path from a phone-provided name.
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).rstrip(' .') or 'download'
    if value.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}:
        value = '_' + value
    return value


class FileWorker(QThread):
    result = Signal(object)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(self, serial, path=None, downloads=None, destination=None, uploads=None, remote_folder=None):
        super().__init__()
        self.serial, self.path = serial, path
        self.downloads, self.destination = downloads, destination
        self.uploads, self.remote_folder = uploads, remote_folder
        self.cancelled = threading.Event()

    def cancel(self):
        self.cancelled.set()

    def execute(self, args, timeout=None):
        process = subprocess.Popen(['adb', '-s', self.serial, *args], stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE,
                                   creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0)
        import time
        deadline = time.monotonic() + timeout if timeout else None
        try:
            while True:
                if self.cancelled.is_set():
                    raise RuntimeError('Operation cancelled.')
                if deadline and time.monotonic() > deadline:
                    raise RuntimeError('Directory listing timed out. Check the phone connection.')
                try:
                    output, error = process.communicate(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    pass
            if process.returncode:
                raise RuntimeError(error.decode(errors='replace').strip() or output.decode(errors='replace').strip() or 'ADB operation failed.')
            return output
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()

    def run(self):
        try:
            if self.path is not None:
                # ADB joins shell arguments, so quote the whole script and each
                # user-controlled path explicitly. NUL delimiters preserve spaces,
                # quotes and newlines in Android filenames.
                script = 'cd ' + shlex.quote(self.path) + ''' || exit
for item in .[!.]* ..?* *; do
    [ -e "$item" ] || [ -L "$item" ] || continue
    if [ -d "$item" ]; then kind=d; else kind=f; fi
    size=$(stat -c %s -- "$item" 2>/dev/null) || size=0
    printf '%s\\0%s\\0%s\\0' "$kind" "$size" "$item"
done'''
                output = self.execute(['shell', '-T', 'sh', '-c', shlex.quote(script)], timeout=20)
                if output and (not output.endswith(b'\0') or output.count(b'\0') % 3):
                    raise RuntimeError('Invalid directory response: ' + output.decode(errors='replace')[:300])
                fields = output.split(b'\0')
                entries = []
                for index in range(0, len(fields) - 2, 3):
                    kind, size, name = fields[index:index + 3]
                    if kind not in (b'd', b'f') or not size.isdigit():
                        raise RuntimeError('Invalid file metadata returned by the phone.')
                    name = name.decode('utf-8', errors='replace')
                    if not name or name in ('.', '..') or '/' in name:
                        continue
                    entries.append((name, kind == b'd', int(size) if size.isdigit() else 0))
                self.result.emit((self.path, sorted(entries, key=lambda e: (not e[1], e[0].casefold()))))
                return
            if self.uploads is not None:
                completed = []
                for index, filename in enumerate(self.uploads):
                    source = Path(filename)
                    if not source.exists() or source.is_symlink():
                        raise RuntimeError(f'Upload source unavailable or a symlink: {source}')
                    if source.is_dir() and any(p.is_symlink() for p in source.rglob('*')):
                        raise RuntimeError('Folders containing symbolic links cannot be uploaded.')
                    remote = str(PurePosixPath(self.remote_folder) / source.name)
                    staging = str(PurePosixPath(self.remote_folder) / ('.adb-upload-' + uuid.uuid4().hex))
                    def command(script):
                        return self.execute(['shell', '-T', 'sh', '-c', shlex.quote(script)], timeout=20)
                    command('if [ -e ' + shlex.quote(remote) + ' ] || [ -L ' + shlex.quote(remote) + ' ]; then echo "Destination already exists; upload was not started" >&2; exit 1; fi; mkdir ' + shlex.quote(staging))
                    self.progress.emit(f'Uploading {index + 1}/{len(self.uploads)}: {source.name}')
                    try:
                        staged_item = staging + '/item'
                        self.execute(['push', str(source), staged_item])
                        if self.cancelled.is_set():
                            raise RuntimeError('Upload cancelled.')
                        command('if [ -e ' + shlex.quote(remote) + ' ] || [ -L ' + shlex.quote(remote) + ' ]; then echo "Destination appeared during upload" >&2; exit 1; fi; mv ' + shlex.quote(staged_item) + ' ' + shlex.quote(remote))
                    finally:
                        # Delete only this operation's randomly named staging area,
                        # even after cancellation. Never delete the final destination.
                        try:
                            subprocess.run(['adb', '-s', self.serial, 'shell', '-T', 'sh', '-c', shlex.quote('rm -rf -- ' + shlex.quote(staging))], capture_output=True, timeout=10,
                                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0)
                        except (OSError, subprocess.TimeoutExpired):
                            pass
                    completed.append(remote)
                self.result.emit({'uploaded': completed})
                return
            completed = []
            for index, remote in enumerate(self.downloads):
                if self.cancelled.is_set():
                    raise RuntimeError('Download cancelled.')
                name = local_name(PurePosixPath(remote).name)
                target = Path(self.destination) / name
                number = 2
                while target.exists():
                    target = Path(self.destination) / f'{name} ({number})'
                    number += 1
                self.progress.emit(f'Downloading {index + 1}/{len(self.downloads)}: {PurePosixPath(remote).name}')
                # Stage each item separately. Interrupted downloads never replace
                # existing local files or appear as completed downloads.
                with tempfile.TemporaryDirectory(prefix='.adb-download-', dir=self.destination) as staging:
                    partial = Path(staging) / 'item'
                    self.execute(['pull', remote, str(partial)])
                    if self.cancelled.is_set():
                        raise RuntimeError('Download cancelled.')
                    if target.exists():
                        raise RuntimeError(f'Destination appeared during download: {target}')
                    shutil.move(str(partial), str(target))
                completed.append(str(target))
            self.result.emit(completed)
        except Exception as exc:
            self.failed.emit(str(exc))


class FileBrowserDialog(QDialog):
    def __init__(self, serial, parent=None):
        super().__init__(parent)
        self.serial = serial
        self.worker = None
        self.current_path = '/sdcard'
        self.destination = None
        self.setWindowTitle('Phone files')
        self.setAcceptDrops(True)
        self.resize(760, 520)
        layout = QVBoxLayout(self)
        title = QLabel('Phone files')
        title.setStyleSheet('font-size:22px;font-weight:600;')
        layout.addWidget(title)
        device = QLabel(f'Device: {serial}')
        device.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(device)
        navigation = QHBoxLayout()
        up = QPushButton('↑ Up')
        up.clicked.connect(lambda: self.browse(str(PurePosixPath(self.current_path).parent)))
        navigation.addWidget(up)
        self.path = QLineEdit('/sdcard')
        self.path.returnPressed.connect(lambda: self.browse(self.path.text()))
        navigation.addWidget(self.path, 1)
        go = QPushButton('Go / Refresh')
        go.clicked.connect(lambda: self.browse(self.path.text()))
        navigation.addWidget(go)
        layout.addLayout(navigation)
        places = QHBoxLayout()
        for label, path in [('Internal storage', '/sdcard'), ('Downloads', '/sdcard/Download'), ('Camera', '/sdcard/DCIM'), ('Pictures', '/sdcard/Pictures'), ('Storage devices', '/storage')]:
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, path=path: self.browse(path))
            places.addWidget(button)
        layout.addLayout(places)
        self.files = QTreeWidget()
        self.files.setHeaderLabels(['Name', 'Type', 'Size'])
        self.files.setColumnWidth(0, 410)
        self.files.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.files.itemDoubleClicked.connect(self.open_item)
        layout.addWidget(self.files, 1)
        self.status = QLabel('Only files readable through ADB are available; protected app storage may be inaccessible.')
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        self.download = QPushButton('Download selected…')
        self.download.clicked.connect(self.download_selected)
        actions.addWidget(self.download)
        upload = QPushButton('Upload files…')
        upload.clicked.connect(self.choose_uploads)
        actions.addWidget(upload)
        upload_folder = QPushButton('Upload folder…')
        upload_folder.clicked.connect(self.choose_upload_folder)
        actions.addWidget(upload_folder)
        self.cancel = QPushButton('Cancel transfer')
        self.cancel.setEnabled(False)
        self.cancel.clicked.connect(self.cancel_operation)
        actions.addWidget(self.cancel)
        folder = QPushButton('Open download folder')
        folder.clicked.connect(self.open_destination)
        actions.addWidget(folder)
        layout.addLayout(actions)
        self.browse('/sdcard')

    def busy(self):
        return self.worker is not None and self.worker.isRunning()

    def start(self, worker):
        self.worker = worker
        self.download.setEnabled(False)
        self.cancel.setEnabled(True)
        worker.result.connect(self.received)
        worker.failed.connect(self.status.setText)
        worker.progress.connect(self.status.setText)
        worker.finished.connect(self.operation_finished)
        worker.start()

    def operation_finished(self):
        self.download.setEnabled(True)
        self.cancel.setEnabled(False)

    def browse(self, path):
        if self.busy():
            self.status.setText('Wait for the current operation or cancel it first.')
            return
        path = path.strip()
        if not path.startswith('/') or '\x00' in path:
            self.status.setText('Enter an absolute phone folder path, such as /sdcard/Download.')
            return
        self.status.setText(f'Loading {path}…')
        self.start(FileWorker(self.serial, path=str(PurePosixPath(path))))

    def received(self, result):
        if isinstance(result, dict):
            self.status.setText(f"Uploaded {len(result['uploaded'])} item(s). Refresh this folder to view them.")
        elif isinstance(result, tuple):
            self.current_path, entries = result
            self.path.setText(self.current_path)
            self.files.clear()
            style = self.style()
            from PySide6.QtWidgets import QStyle
            for name, folder, size in entries:
                label = '' if folder else (f'{size / 1024**2:.1f} MB' if size >= 1024**2 else f'{size / 1024:.1f} KB')
                item = QTreeWidgetItem([name, 'Folder' if folder else 'File', label])
                item.setData(0, Qt.ItemDataRole.UserRole, (str(PurePosixPath(self.current_path) / name), folder))
                item.setIcon(0, style.standardIcon(QStyle.StandardPixmap.SP_DirIcon if folder else QStyle.StandardPixmap.SP_FileIcon))
                self.files.addTopLevelItem(item)
            self.status.setText(f'{len(entries)} items • Double-click a folder to open it. Select files or folders to download.')
        else:
            self.status.setText(f'Downloaded {len(result)} item(s) to {self.destination}')

    def open_item(self, item, _column):
        path, folder = item.data(0, Qt.ItemDataRole.UserRole)
        if folder:
            self.browse(path)

    def download_selected(self):
        if self.busy():
            return
        selected = [item.data(0, Qt.ItemDataRole.UserRole)[0] for item in self.files.selectedItems()]
        if not selected:
            self.status.setText('Select one or more files or folders first.')
            return
        default = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
        destination = QFileDialog.getExistingDirectory(self, 'Download phone files to…', self.destination or default)
        if destination:
            self.destination = destination
            self.start(FileWorker(self.serial, downloads=selected, destination=destination))

    def choose_uploads(self):
        if self.busy():
            return
        filenames, _ = QFileDialog.getOpenFileNames(self, 'Upload files to ' + self.current_path)
        self.upload_paths(filenames)

    def choose_upload_folder(self):
        if self.busy():
            return
        folder = QFileDialog.getExistingDirectory(self, 'Upload a folder to ' + self.current_path)
        if folder:
            self.upload_paths([folder])

    def upload_paths(self, filenames):
        if self.busy():
            self.status.setText('Finish or cancel the current transfer first.')
            return
        if filenames:
            self.start(FileWorker(self.serial, uploads=filenames, remote_folder=self.current_path))

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and all(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        filenames = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if filenames:
            self.upload_paths(filenames)
            event.acceptProposedAction()

    def cancel_operation(self):
        if self.busy():
            self.worker.cancel()
            self.status.setText('Cancelling… Completed downloads will be kept.')

    def open_destination(self):
        if self.destination:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.destination))

    def reject(self):
        self.close()

    def closeEvent(self, event):
        if self.busy():
            self.cancel_operation()
            self.status.setText('Cancelling operation. Close this window again once it finishes.')
            event.ignore()
        else:
            super().closeEvent(event)
