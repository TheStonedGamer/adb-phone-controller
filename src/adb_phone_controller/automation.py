"""Record and replay bounded interaction sequences without arbitrary shell scripts."""
from datetime import datetime
import json
import re
import time
import uuid
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QLabel, QPushButton, QInputDialog


class AutomationEngine(QObject):
    changed = Signal()
    status_changed = Signal(str)

    def __init__(self, controller):
        super().__init__(controller)
        self.controller = controller
        self.recording = False
        self.playing = False
        self.actions = []
        self.macros = self.load()
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.advance)
        self.last = 0
        self.index = 0
        self.target = None
        self.touch = None

    def load(self):
        try:
            value = json.loads(self.controller.settings.value('automation/macros', '[]'))
            if not isinstance(value, list):
                return []
            return [entry for entry in value if isinstance(entry, dict) and isinstance(entry.get('actions'), list) and isinstance(entry.get('name'), str) and entry.get('id')]
        except (ValueError, TypeError):
            return []

    def save(self):
        self.controller.settings.setValue('automation/macros', json.dumps(self.macros))
        self.controller.settings.sync()
        self.changed.emit()

    def record(self, kind, payload):
        if not self.recording or self.playing:
            return
        if len(self.actions) >= 10000:
            self.stop_recording()
            self.status_changed.emit('Shortcut saved after reaching the 10,000-action limit.')
            return
        now = time.monotonic()
        self.actions.append({'kind': kind, 'payload': payload, 'delay': min(60000, round((now - self.last) * 1000))})
        self.last = now
        self.status_changed.emit(f'Recording: {len(self.actions)} actions. Typed text is included.')

    def start_recording(self):
        worker = self.controller.frame_worker
        if not worker or worker.source != 'display' or worker._control_socket is None:
            self.status_changed.emit('Connect the Phone screen feed before recording a shortcut.')
            return
        self.cancel()
        self.actions = []
        self.target = (self.controller.serial, worker)
        self.last = time.monotonic()
        self.recording = True
        self.status_changed.emit('Recording interactions. Typed text is included. Return here to Stop & save.')
        self.changed.emit()

    def stop_recording(self, name=None):
        if not self.recording:
            return
        self.recording = False
        if self.actions:
            self.actions[0]['delay'] = 0
            self.macros.append({'id': uuid.uuid4().hex, 'name': name or f'Shortcut {datetime.now():%Y-%m-%d %H:%M:%S}', 'actions': self.actions})
            self.save()
            self.status_changed.emit('Shortcut saved.')
        else:
            self.status_changed.emit('Nothing recorded.')
        self.changed.emit()

    def valid_target(self):
        return self.target == (self.controller.serial, self.controller.frame_worker)

    def play(self, macro):
        worker = self.controller.frame_worker
        if self.recording or not worker or worker.source != 'display' or worker._control_socket is None:
            self.status_changed.emit('Stop recording and connect the Phone screen feed before replay.')
            return
        try:
            actions = macro['actions']
            if not 0 < len(actions) <= 10000:
                raise ValueError('Shortcut must have between 1 and 10,000 actions.')
            allowed = {'touch', 'key', 'text', 'launch', 'wait'}
            for action in actions:
                if not isinstance(action, dict):
                    raise ValueError("Invalid shortcut action.")
                if action.get('kind') not in allowed or not 0 <= int(action.get('delay', 0)) <= 60000:
                    raise ValueError('Invalid shortcut action.')
        except (ValueError, TypeError, KeyError) as exc:
            self.status_changed.emit(str(exc))
            return
        self.cancel()
        self.target = (self.controller.serial, worker)
        self.actions = actions
        self.index = 0
        self.playing = True
        self.status_changed.emit('Playing ' + macro['name'])
        self.changed.emit()
        self.timer.start(int(self.actions[0].get('delay', 0)))

    def advance(self):
        if not self.playing:
            return
        if not self.valid_target() or not self.controller.frame_worker.isRunning():
            self.cancel()
            self.status_changed.emit('Replay stopped because the active phone or stream changed.')
            return
        action = self.actions[self.index]
        kind, payload = action['kind'], action.get('payload')
        try:
            worker = self.controller.frame_worker
            if kind == 'touch':
                event, nx, ny = payload
                if event not in (0, 1, 2) or not (0 <= nx <= 1 and 0 <= ny <= 1):
                    raise ValueError('Invalid touch action.')
                image = worker.latest_video_frame
                if image is None:
                    raise ValueError('No live frame is available.')
                width, height = image.width(), image.height()
                point = (min(width - 1, round(nx * width)), min(height - 1, round(ny * height)), width, height)
                worker.send_touch(event, *point)
                self.touch = point if event != 1 else None
            elif kind == 'key':
                key, meta = payload
                if not isinstance(key, int) or not 0 <= key <= 1000 or not isinstance(meta, int):
                    raise ValueError('Invalid key action.')
                worker.send_key(key, meta)
            elif kind == 'text':
                text, paste = payload
                if not isinstance(text, str) or len(text.encode('utf-8')) > 256000:
                    raise ValueError('Invalid text action.')
                worker.send_text(text, bool(paste))
            elif kind == 'launch':
                if not isinstance(payload, str) or not re.fullmatch(r'[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+', payload):
                    raise ValueError('Invalid app package.')
                worker.start_app(payload)
            self.index += 1
            if self.index >= len(self.actions):
                self.cancel()
                self.status_changed.emit('Shortcut finished.')
            else:
                self.timer.start(int(self.actions[self.index].get('delay', 0)))
        except Exception as exc:
            self.cancel()
            self.status_changed.emit(f'Replay stopped: {exc}')

    def device_changed(self):
        if self.recording:
            self.stop_recording()
        if self.playing:
            self.cancel()
            self.status_changed.emit('Replay stopped on device/stream change.')

    def cancel(self):
        self.timer.stop()
        if self.touch and self.target:
            worker = self.target[1]
            try:
                worker.send_touch(1, *self.touch)
            except (OSError, RuntimeError):
                pass
        self.touch = None
        self.playing = False
        self.changed.emit()


class AutomationDialog(QDialog):
    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.setWindowTitle('Automation shortcuts')
        self.resize(530, 400)
        layout = QVBoxLayout(self)
        note = QLabel('Record taps, swipes, typing, navigation keys, and app launches from the controller. Shortcuts include typed text and run on the active phone.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.list = QListWidget()
        layout.addWidget(self.list, 1)
        self.status = QLabel('Ready')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        self.record = QPushButton('Record interactions')
        self.record.clicked.connect(engine.start_recording)
        row.addWidget(self.record)
        self.stop = QPushButton('Stop & save')
        self.stop.clicked.connect(self.save_recording)
        row.addWidget(self.stop)
        self.play_button = QPushButton('Play selected')
        self.play_button.clicked.connect(self.play)
        row.addWidget(self.play_button)
        cancel = QPushButton('Cancel replay')
        cancel.clicked.connect(engine.cancel)
        row.addWidget(cancel)
        layout.addLayout(row)
        edits = QHBoxLayout()
        for label, handler in [('Rename', self.rename), ('Delete', self.delete), ('Append app launch…', self.add_launch), ('Append wait…', self.add_wait)]:
            button = QPushButton(label)
            button.clicked.connect(handler)
            edits.addWidget(button)
        layout.addLayout(edits)
        engine.changed.connect(self.refresh)
        engine.status_changed.connect(self.status.setText)
        self.refresh()

    def selected(self):
        row = self.list.currentRow()
        return self.engine.macros[row] if 0 <= row < len(self.engine.macros) else None

    def refresh(self):
        row = self.list.currentRow()
        self.list.clear()
        for macro in self.engine.macros:
            self.list.addItem(f"{macro['name']} · {len(macro['actions'])} actions")
        if self.list.count():
            self.list.setCurrentRow(max(0, min(row, self.list.count() - 1)))
        self.record.setEnabled(not self.engine.recording and not self.engine.playing)
        self.stop.setEnabled(self.engine.recording)
        self.play_button.setEnabled(not self.engine.recording and not self.engine.playing)

    def save_recording(self):
        name, ok = QInputDialog.getText(self, 'Save shortcut', 'Shortcut name')
        self.engine.stop_recording(name.strip() if ok and name.strip() else None)

    def play(self):
        macro = self.selected()
        if macro:
            self.engine.play(macro)

    def rename(self):
        macro = self.selected()
        if macro:
            name, ok = QInputDialog.getText(self, 'Rename shortcut', 'Name', text=macro['name'])
            if ok and name.strip():
                macro['name'] = name.strip()
                self.engine.save()

    def delete(self):
        macro = self.selected()
        if macro and not self.engine.playing:
            self.engine.macros.remove(macro)
            self.engine.save()

    def add_launch(self):
        macro = self.selected()
        if macro and not self.engine.playing:
            package, ok = QInputDialog.getText(self, 'Append app launch', 'Android package (for example com.android.settings)')
            if ok and re.fullmatch(r'[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+', package):
                macro['actions'].append({'kind': 'launch', 'payload': package, 'delay': 0})
                self.engine.save()

    def add_wait(self):
        macro = self.selected()
        if macro and not self.engine.playing:
            seconds, ok = QInputDialog.getDouble(self, 'Append delay', 'Seconds', 1, 0.1, 60, 1)
            if ok:
                macro['actions'].append({'kind': 'wait', 'payload': None, 'delay': round(seconds * 1000)})
                self.engine.save()
