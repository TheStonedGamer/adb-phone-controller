"""Persistent phone library and its desktop picker."""
import json
from datetime import datetime
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QInputDialog,
)


class DeviceLibrary:
    def __init__(self, settings):
        self.settings = settings
        try:
            entries = json.loads(settings.value('devices/library', '[]'))
            self.entries = [e for e in entries if isinstance(e, dict) and e.get('id')] if isinstance(entries, list) else []
        except (ValueError, TypeError):
            self.entries = []
        try:
            self.forgotten = set(json.loads(settings.value('devices/forgotten', '[]')))
        except (ValueError, TypeError):
            self.forgotten = set()
        # Migrate the phone remembered by earlier versions once.
        if not settings.value('devices/migrated', False, type=bool):
            identity = settings.value('device/identity', '')
            serial = settings.value('device/serial', '')
            if (identity or serial) and not self.entries:
                host, port = settings.value('wifi/address', ''), settings.value('wifi/port', '')
                self.entries.append({'id': identity or serial, 'identity': identity,
                    'serial': serial, 'name': 'My phone', 'wifi': f'{host}:{port}' if host and port else '', 'seen': ''})
            settings.setValue('devices/migrated', True)
            self.save()

    def save(self):
        self.settings.setValue('devices/library', json.dumps(self.entries))
        self.settings.setValue('devices/forgotten', json.dumps(sorted(self.forgotten)))
        self.settings.sync()

    def remember(self, records, force=False):
        for serial, details, identity, wired in records:
            if force:
                self.forgotten.difference_update((identity, serial))
            elif identity in self.forgotten or serial in self.forgotten:
                continue
            entry = next((e for e in self.entries if (identity and e.get('identity') == identity)
                          or e.get('serial') == serial or e.get('wifi') == serial), None)
            if entry is None:
                model = next((part[6:].replace('_', ' ') for part in details.split() if part.startswith('model:')), 'Android phone')
                entry = {'id': identity or serial, 'identity': identity, 'name': model, 'wifi': ''}
                self.entries.append(entry)
            if identity:
                entry['identity'] = identity
            entry['serial'] = serial
            if not wired and ':' in serial and serial.rsplit(':', 1)[1].isdigit():
                entry['wifi'] = serial
            entry['seen'] = datetime.now().strftime('%Y-%m-%d %H:%M')
        if records:
            self.save()

    @staticmethod
    def matches(entry, record):
        return bool((entry.get('identity') and entry['identity'] == record[2])
                    or record[0] in (entry.get('serial'), entry.get('wifi')))


class RememberedDevicesDialog(QDialog):
    def __init__(self, controller):
        super().__init__(controller)
        self.controller = controller
        self.library = controller.device_library
        self.setWindowTitle('Your devices')
        self.resize(540, 450)
        layout = QVBoxLayout(self)
        title = QLabel('Your devices')
        title.setStyleSheet('font-size:24px;font-weight:600;padding:8px 0;')
        layout.addWidget(title)
        subtitle = QLabel('Saved phones • USB preferred when available')
        subtitle.setStyleSheet('color:#9ba7b6;padding-bottom:10px;')
        layout.addWidget(subtitle)
        self.list = QListWidget()
        self.list.setSpacing(8)
        self.list.setStyleSheet('QListWidget { border:0; background:transparent; } QListWidget::item { padding:14px; border:1px solid #48515d; border-radius:10px; } QListWidget::item:selected { background:#234336; border:1px solid #4bbf88; }')
        self.list.itemDoubleClicked.connect(lambda *_: self.connect_selected())
        layout.addWidget(self.list, 1)
        self.empty = QLabel('Connect a phone over USB or Wi-Fi to save it here automatically.')
        self.empty.setWordWrap(True)
        layout.addWidget(self.empty)
        actions = QHBoxLayout()
        self.actions = []
        for label, handler in [('Connect', self.connect_selected), ('Rename', self.rename), ('Wi-Fi address…', self.edit_wifi), ('Forget', self.forget)]:
            button = QPushButton(label)
            button.clicked.connect(handler)
            actions.addWidget(button)
            self.actions.append(button)
        layout.addLayout(actions)
        add = QPushButton('Add phone over Wi-Fi…')
        add.clicked.connect(self.add_phone)
        layout.addWidget(add)
        remember = QPushButton('Remember connected phones')
        remember.clicked.connect(self.remember_connected)
        layout.addWidget(remember)
        self.list.currentItemChanged.connect(self.update_actions)
        self.refresh()

    def selected(self):
        item = self.list.currentItem()
        return next((e for e in self.library.entries if item and e['id'] == item.data(Qt.ItemDataRole.UserRole)), None)

    def update_actions(self, *_):
        for action in self.actions:
            action.setEnabled(self.selected() is not None)

    def refresh(self):
        selected = self.selected()
        key = selected['id'] if selected else None
        self.list.clear()
        records = getattr(self.controller, 'device_records', {}).values()
        for entry in self.library.entries:
            online = [r for r in records if self.library.matches(entry, r)]
            state = '● Connected' if any(r[0] == self.controller.serial for r in online) else ('● Available' if online else '○ Offline')
            transport = 'USB' if any(r[3] for r in online) else ('Wi-Fi' if online else '')
            endpoint = entry.get('wifi') or 'USB only • no Wi-Fi address saved'
            item = QListWidgetItem(f"{entry['name']}\n{state}{' · ' + transport if transport else ''}\n{endpoint}")
            item.setData(Qt.ItemDataRole.UserRole, entry['id'])
            item.setToolTip(f"Device: {entry.get('identity') or entry.get('serial', '')}\nLast seen: {entry.get('seen') or 'Unknown'}")
            self.list.addItem(item)
            if entry['id'] == key:
                self.list.setCurrentItem(item)
        if self.list.currentRow() < 0 and self.list.count():
            self.list.setCurrentRow(0)
        self.empty.setVisible(not self.library.entries)
        self.update_actions()

    def rename(self):
        entry = self.selected()
        if entry:
            name, ok = QInputDialog.getText(self, 'Rename phone', 'Phone name', text=entry['name'])
            if ok and name.strip():
                entry['name'] = name.strip()
                self.library.save()
                self.refresh()

    def edit_wifi(self):
        entry = self.selected()
        if entry:
            endpoint, ok = QInputDialog.getText(self, 'Wi-Fi connection', 'Address and connection port from Wireless debugging (IP:port)', text=entry.get('wifi', ''))
            if ok:
                endpoint = endpoint.strip()
                host, separator, port = endpoint.rpartition(':')
                if endpoint and (not separator or not host or not port.isdigit() or not 1 <= int(port) <= 65535):
                    self.empty.setText('Enter an address and port such as 192.168.1.42:37123.')
                    self.empty.show()
                    return
                entry['wifi'] = endpoint
                self.library.save()
                self.refresh()

    def forget(self):
        entry = self.selected()
        if entry:
            self.library.forgotten.update(v for v in (entry.get('identity'), entry.get('serial'), entry.get('wifi')) if v)
            self.library.entries.remove(entry)
            self.library.save()
            self.refresh()

    def connect_selected(self):
        entry = self.selected()
        if entry:
            self.controller.connect_remembered_device(entry)
            self.accept()

    def remember_connected(self):
        self.library.remember(list(getattr(self.controller, 'device_records', {}).values()), force=True)
        self.refresh()

    def add_phone(self):
        self.accept()
        self.controller.wifi_connect()
