"""Background ADB tasks and device management pages."""
from collections import deque
import codecs
from datetime import datetime
import ipaddress
from pathlib import Path, PurePosixPath
import re
import shlex
import socket
import subprocess
import sys

from PySide6.QtCore import QThread, Signal, QProcess, QTimer, Qt
from PySide6.QtWidgets import (
    QDialog, QWidget, QTabWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QPlainTextEdit, QLineEdit, QListWidget, QCheckBox, QFileDialog, QMessageBox,
    QSpinBox, QTreeWidget, QTreeWidgetItem,
)


def run_adb(serial, args, timeout=15):
    command = ['adb'] + (['-s', serial] if serial else []) + args
    process = subprocess.run(command, capture_output=True, timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
    output = process.stdout.decode(errors='replace').strip()
    error = process.stderr.decode(errors='replace').strip()
    if process.returncode:
        raise RuntimeError(error or output or f'ADB exited with {process.returncode}')
    return output


def shell(serial, script, timeout=15):
    return run_adb(serial, ['shell', '-T', 'sh', '-c', shlex.quote(script)], timeout)


def package_name(value):
    if not re.fullmatch(r'[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+', value or ''):
        raise ValueError('Select a valid Android package.')
    return value


class Task(QThread):
    result = Signal(object)
    failed = Signal(str)

    def __init__(self, function, parent=None):
        super().__init__(parent)
        self.function = function

    def run(self):
        try:
            self.result.emit(self.function())
        except Exception as exc:
            self.failed.emit(str(exc))


class TaskPage(QWidget):
    def __init__(self, serial):
        super().__init__()
        self.serial = serial
        self.task = None
        self.layout = QVBoxLayout(self)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.layout.addWidget(self.status)

    def execute(self, function, received=None):
        if self.task and self.task.isRunning():
            self.status.setText('Wait for the current operation to finish.')
            return False
        self.status.setText('Working…')
        self.task = Task(function, self)
        self.task.result.connect(received or (lambda result: self.status.setText(str(result))))
        self.task.failed.connect(self.status.setText)
        worker = self.task
        worker.finished.connect(lambda: self.task_finished(worker))
        self.task.start()
        return True

    def task_finished(self, worker):
        if self.task is worker:
            self.task = None
        worker.deleteLater()

    def busy(self):
        return self.task is not None and self.task.isRunning()


class DashboardPage(TaskPage):
    def __init__(self, serial):
        super().__init__(serial)
        refresh = QPushButton('Refresh device status')
        refresh.clicked.connect(self.refresh)
        self.layout.addWidget(refresh)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['Device information', 'Value'])
        self.tree.setColumnWidth(0, 210)
        self.layout.addWidget(self.tree)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.layout.addWidget(self.details)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.auto_refresh)
        self.timer.start(10000)
        self.refresh()

    def auto_refresh(self):
        if self.isVisible() and not self.busy():
            self.refresh()

    def refresh(self):
        def collect():
            props = run_adb(self.serial, ['shell', 'getprop'])
            values = dict(re.findall(r'^\[([^]]+)\]: \[(.*)\]$', props, re.M))
            info = [('Manufacturer', values.get('ro.product.manufacturer', '')),
                    ('Model', values.get('ro.product.model', '')),
                    ('Android', values.get('ro.build.version.release', '')),
                    ('API level', values.get('ro.build.version.sdk', '')),
                    ('Security patch', values.get('ro.build.version.security_patch', '')),
                    ('Hardware serial', values.get('ro.serialno', ''))]
            battery = run_adb(self.serial, ['shell', 'dumpsys', 'battery'])
            fields = dict(re.findall(r'^\s*([\w ]+):\s*(.*)$', battery, re.M))
            level, scale = fields.get('level', '?'), fields.get('scale', '100')
            info.append(('Battery', f'{level}/{scale}'))
            states = {'1': 'Unknown', '2': 'Charging', '3': 'Discharging', '4': 'Not charging', '5': 'Full'}
            info.append(('Charging state', states.get(fields.get('status'), fields.get('status', '?'))))
            info.append(('Power source', ', '.join(key.removesuffix(' powered') for key in ('AC powered', 'USB powered', 'Wireless powered') if fields.get(key) == 'true') or 'Battery'))
            try:
                info.append(('Battery temperature', f"{int(fields['temperature']) / 10:.1f} °C"))
            except (KeyError, ValueError):
                pass
            memory = shell(self.serial, 'cat /proc/meminfo | head -n 5; echo; df -h /data /sdcard')
            return info, memory
        self.execute(collect, self.show_info)

    def show_info(self, result):
        self.tree.clear()
        for label, value in result[0]:
            self.tree.addTopLevelItem(QTreeWidgetItem([label, value]))
        self.details.setPlainText('Memory and storage\n\n' + result[1])
        self.status.setText('Updated ' + datetime.now().strftime('%H:%M:%S'))


class AppsPage(TaskPage):
    app_launched = Signal(str)

    def __init__(self, serial):
        super().__init__(serial)
        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText('Search installed packages…')
        self.search.textChanged.connect(self.filter)
        row.addWidget(self.search)
        self.system = QCheckBox('Include system apps')
        self.system.toggled.connect(self.refresh)
        row.addWidget(self.system)
        refresh = QPushButton('Refresh')
        refresh.clicked.connect(self.refresh)
        row.addWidget(refresh)
        self.layout.addLayout(row)
        self.apps = QListWidget()
        self.layout.addWidget(self.apps, 1)
        actions = QHBoxLayout()
        for label, handler in [('Launch', self.launch), ('Force stop', self.force_stop), ('Clear cache', self.clear_cache), ('Clear data…', self.clear_data), ('Permissions', self.permissions), ('Export APKs…', self.export_apks)]:
            button = QPushButton(label)
            button.clicked.connect(handler)
            actions.addWidget(button)
        self.layout.addLayout(actions)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.layout.addWidget(self.details, 1)
        self.refresh()

    def selected(self):
        item = self.apps.currentItem()
        if not item:
            self.status.setText('Select an app first.')
            return None
        return package_name(item.text())

    def refresh(self, *_):
        args = ['shell', 'pm', 'list', 'packages'] + ([] if self.system.isChecked() else ['-3'])
        self.execute(lambda: run_adb(self.serial, args), self.show_apps)

    def show_apps(self, result):
        self.apps.clear()
        self.apps.addItems(sorted(line.removeprefix('package:') for line in result.splitlines() if line.startswith('package:')))
        self.filter()
        self.status.setText(f'{self.apps.count()} installed packages')

    def filter(self, *_):
        query = self.search.text().casefold()
        for i in range(self.apps.count()):
            item = self.apps.item(i)
            item.setHidden(query not in item.text().casefold())

    def launch(self):
        package = self.selected()
        if package:
            self.execute(lambda: run_adb(self.serial, ['shell', 'monkey', '-p', package, '-c', 'android.intent.category.LAUNCHER', '1']), lambda result: (self.status.setText(result), self.app_launched.emit(package)))

    def force_stop(self):
        package = self.selected()
        if package:
            self.execute(lambda: run_adb(self.serial, ['shell', 'am', 'force-stop', package]) or f'Stopped {package}')

    def clear_cache(self):
        package = self.selected()
        if not package:
            return
        def clear():
            help_text = run_adb(self.serial, ['shell', 'pm', 'help'])
            if '--cache-only' not in help_text:
                raise RuntimeError('This Android version does not expose per-app cache clearing through ADB. Use the app storage settings on the phone.')
            return run_adb(self.serial, ['shell', 'pm', 'clear', '--cache-only', package])
        self.execute(clear)

    def clear_data(self):
        package = self.selected()
        if package and QMessageBox.question(self, 'Erase app data?', f'Erase all local data and sign-ins for {package}? This cannot be undone.', QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.execute(lambda: run_adb(self.serial, ['shell', 'pm', 'clear', package]))

    def permissions(self):
        package = self.selected()
        if package:
            self.execute(lambda: run_adb(self.serial, ['shell', 'dumpsys', 'package', package]), self.show_details)

    def show_details(self, result):
        self.details.setPlainText(result)
        self.status.setText('Package details include requested, install, and runtime permissions.')

    def export_apks(self):
        package = self.selected()
        if not package or self.busy():
            return
        destination = QFileDialog.getExistingDirectory(self, 'Export installed APKs to…')
        if not destination:
            return
        def export():
            paths = run_adb(self.serial, ['shell', 'pm', 'path', package])
            sources = [line.removeprefix('package:') for line in paths.splitlines() if line.startswith('package:')]
            if not sources:
                raise RuntimeError('No APK paths returned for this app.')
            folder = Path(destination) / f'{package}-{datetime.now():%Y%m%d-%H%M%S}'
            folder.mkdir(exist_ok=False)
            try:
                for i, remote in enumerate(sources):
                    name = PurePosixPath(remote).name
                    name = re.sub(r'[^A-Za-z0-9_.-]', '_', name)
                    target = folder / f'{i:02d}-{name}'
                    partial = target.with_suffix('.part')
                    run_adb(self.serial, ['pull', remote, str(partial)], timeout=180)
                    partial.rename(target)
                (folder / 'README.txt').write_text('Installed APK export including split APKs. This does not include private app data.\nReinstall all APKs together with adb install-multiple.\n')
            except Exception as exc:
                raise RuntimeError(f'Export incomplete in {folder}: {exc}') from exc
            return f'Exported {len(sources)} APK(s) to {folder}. Private app data is not included.'
        self.execute(export)


class LogsPage(TaskPage):
    def __init__(self, serial):
        super().__init__(serial)
        self.lines = deque(maxlen=10000)
        self.pending = ''
        self.decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.read)
        self.process.errorOccurred.connect(lambda _: self.status.setText(self.process.errorString()))
        self.process.finished.connect(lambda *_: self.status.setText('Log stream stopped'))
        row = QHBoxLayout()
        for label, handler in [('Start live logs', self.start), ('Stop', self.stop), ('Crash buffer', self.crashes), ('Save filtered logs…', self.save)]:
            button = QPushButton(label)
            button.clicked.connect(handler)
            row.addWidget(button)
        self.layout.addLayout(row)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Filter by package, tag, PID, or error text…')
        self.search.textChanged.connect(self.render)
        self.layout.addWidget(self.search)
        self.errors = QCheckBox('Errors and fatal messages only')
        self.errors.toggled.connect(self.render)
        self.layout.addWidget(self.errors)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setMaximumBlockCount(10000)
        self.layout.addWidget(self.output, 1)
        self.status.setText('Live logs stay on this computer. Start capture to view logcat.')
        self.render_timer = QTimer(self)
        self.render_timer.setSingleShot(True)
        self.render_timer.timeout.connect(self.render)

    def start(self):
        if self.process.state() != QProcess.ProcessState.NotRunning:
            return
        self.pending = ''
        self.decoder.reset()
        self.status.setText('Streaming logcat…')
        self.process.start('adb', ['-s', self.serial, 'logcat', '-v', 'threadtime'])

    def read(self):
        text = self.pending + self.decoder.decode(bytes(self.process.readAllStandardOutput()))
        chunks = text.split('\n')
        self.pending = chunks.pop()
        self.lines.extend(chunks)
        if not self.render_timer.isActive():
            self.render_timer.start(150)

    def filtered(self):
        query = self.search.text().casefold()
        return [line for line in self.lines if query in line.casefold() and (not self.errors.isChecked() or re.search(r'\s[EF]\s', line))]

    def render(self, *_):
        self.output.setPlainText('\n'.join(self.filtered()))
        bar = self.output.verticalScrollBar()
        bar.setValue(bar.maximum())

    def stop(self):
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.terminate()
            if not self.process.waitForFinished(1000):
                self.process.kill()
                self.process.waitForFinished(1000)
        self.render_timer.stop()

    def crashes(self):
        self.stop()
        self.execute(lambda: run_adb(self.serial, ['logcat', '-b', 'crash', '-d', '-v', 'threadtime']), self.show_crashes)

    def show_crashes(self, result):
        self.lines.clear()
        self.lines.extend(result.splitlines())
        self.render()
        self.status.setText('Crash buffer loaded. No logs were cleared on the phone.')

    def save(self):
        name, _ = QFileDialog.getSaveFileName(self, 'Save filtered logs', f'phone-logs-{datetime.now():%Y%m%d-%H%M%S}.txt', 'Text files (*.txt)')
        if name:
            try:
                Path(name).write_text('\n'.join(self.filtered()), encoding='utf-8')
                self.status.setText(f'Saved {name}')
            except OSError as exc:
                self.status.setText(str(exc))


class WirelessPage(TaskPage):
    connected = Signal(str)

    def __init__(self, serial):
        super().__init__(serial)
        note = QLabel('Enable wireless ADB using an authorized USB connection. The phone and computer must share a trusted local network. TCP mode remains enabled until restored to USB mode or the phone restarts. Android 11+ pairing is also available in Connect over Wi-Fi.')
        note.setWordWrap(True)
        self.layout.addWidget(note)
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(5555)
        self.port.setPrefix('TCP port ')
        self.layout.addWidget(self.port)
        self.address = QLineEdit()
        self.address.setPlaceholderText('Wi-Fi IP (optional; detect automatically)')
        self.layout.addWidget(self.address)
        enable = QPushButton('Enable Wi-Fi from USB and connect')
        enable.clicked.connect(self.enable)
        self.layout.addWidget(enable)
        restore = QPushButton('Restore USB-only ADB')
        restore.clicked.connect(self.restore)
        self.layout.addWidget(restore)
        self.layout.addStretch()

    def enable(self):
        port, address = self.port.value(), self.address.text().strip()
        if ':' in self.serial or self.serial.startswith('adb-'):
            self.status.setText('Select the authorized USB connection for this phone first.')
            return
        def connect():
            host = address
            if host:
                ipaddress.IPv4Address(host)
            else:
                interfaces = run_adb(self.serial, ['shell', 'ip', '-o', '-4', 'addr', 'show'])
                match = re.search(r'\d+:\s+(?:wlan\w*|wifi\w*)\s+.*?inet\s+(\d+\.\d+\.\d+\.\d+)/', interfaces)
                if not match:
                    raise RuntimeError('Could not detect the Wi-Fi IP. Enter it manually.')
                host = match.group(1)
            run_adb(self.serial, ['tcpip', str(port)])
            endpoint = f'{host}:{port}'
            import time
            last = ''
            for _ in range(5):
                time.sleep(0.6)
                try:
                    last = run_adb(None, ['connect', endpoint], timeout=5)
                    if 'connected to' in last.lower():
                        return endpoint
                except Exception as exc:
                    last = str(exc)
            raise RuntimeError(f'TCP mode enabled, but connect failed: {last}. Reconnect USB or update the IP.')
        self.execute(connect, self.show_connected)

    def show_connected(self, endpoint):
        self.status.setText(f'Connected to {endpoint}. You can unplug USB.')
        self.connected.emit(endpoint)

    def restore(self):
        self.execute(lambda: run_adb(self.serial, ['usb']) or 'USB mode requested. Reconnect the USB cable if necessary.')


class DiagnosticsPage(TaskPage):
    def __init__(self, serial, endpoint):
        super().__init__(serial)
        self.endpoint = endpoint
        refresh = QPushButton('Diagnose connections')
        refresh.clicked.connect(self.refresh)
        self.layout.addWidget(refresh)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.layout.addWidget(self.output)
        self.refresh()

    def refresh(self):
        endpoint = self.endpoint
        def diagnose():
            lines = []
            def command(label, serial, args):
                try:
                    value = run_adb(serial, args, timeout=8)
                except Exception as exc:
                    value = str(exc)
                lines.extend([label, value, ''])
                return value
            command('ADB version', None, ['version'])
            devices = command('Detected transports', None, ['devices', '-l'])
            if 'unauthorized' in devices:
                lines.append('Unlock the phone and accept the USB debugging authorization prompt.')
            if 'offline' in devices:
                lines.append('Offline transport: reconnect the cable or refresh Wireless debugging on the phone.')
            if not any('device' in line.split()[1:2] for line in devices.splitlines()[1:]):
                lines.append('No authorized phone detected. Check the USB data cable, debugging authorization, or current Wi-Fi IP/port.')
            command('Wireless discovery support', None, ['mdns', 'services'])
            if endpoint and ':' in endpoint:
                host, port = endpoint.rsplit(':', 1)
                try:
                    with socket.create_connection((host, int(port)), timeout=3):
                        lines.append(f'{endpoint}: TCP port reachable. Reachability alone does not confirm ADB pairing.')
                except Exception as exc:
                    lines.append(f'{endpoint}: not reachable ({exc}). Check the current connection port and shared network.')
                if sys.platform == 'linux':
                    try:
                        route = subprocess.run(['ip', 'route', 'get', host], capture_output=True, text=True, timeout=3).stdout.strip()
                        lines.extend(['Route to phone:', route])
                        if 'tailscale' in route or 'tun' in route:
                            lines.append('Phone traffic is routed through a VPN. Check whether it overrides your local Wi-Fi route.')
                    except (OSError, subprocess.TimeoutExpired):
                        pass
            if self.serial:
                command('Selected phone identity', self.serial, ['shell', 'getprop', 'ro.serialno'])
            return '\n'.join(lines)
        self.execute(diagnose, lambda result: (self.output.setPlainText(result), self.status.setText('Diagnostics complete. No connection settings were changed.')))


class DeviceToolsDialog(QDialog):
    def __init__(self, serial, endpoint, parent=None):
        super().__init__(parent)
        self.serial = serial
        self.setWindowTitle(f'Device tools — {serial or "Connections"}')
        self.resize(900, 650)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.pages = []
        if serial:
            for label, page in [('Dashboard', DashboardPage(serial)), ('Apps & APK backup', AppsPage(serial)), ('Logs', LogsPage(serial)), ('Wireless setup', WirelessPage(serial))]:
                self.pages.append(page)
                self.tabs.addTab(page, label)
        diagnostics = DiagnosticsPage(serial, endpoint)
        self.pages.append(diagnostics)
        self.tabs.addTab(diagnostics, 'Connection diagnostics')
        layout.addWidget(self.tabs)

    def busy(self):
        return any(page.busy() for page in self.pages)

    def stop_logs(self):
        for page in self.pages:
            if isinstance(page, LogsPage):
                page.stop()

    def reject(self):
        self.close()

    def closeEvent(self, event):
        if self.busy():
            for page in self.pages:
                if page.busy():
                    page.status.setText('Wait for the current operation to finish before closing.')
            event.ignore()
            return
        self.stop_logs()
        super().closeEvent(event)
