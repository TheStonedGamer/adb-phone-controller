#!/usr/bin/env python3
"""Small PySide6 desktop controller for an Android device over ADB."""

from __future__ import annotations

import os
import math
import shutil
import socket
import av
from adb_phone_controller import __version__
import struct
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal, QThread, QSettings, QStandardPaths, QRectF, QPointF, QEvent
from PySide6.QtGui import QImage, QPixmap, QTransform, QColor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFileDialog,
    QGroupBox,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QInputDialog,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


APP_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
BUNDLED_TOOLS = APP_ROOT / "vendor" / "scrcpy"
if BUNDLED_TOOLS.is_dir():
    os.environ["PATH"] = str(BUNDLED_TOOLS) + os.pathsep + os.environ.get("PATH", "")


def notify(title, message):
    executable = shutil.which("notify-send") if sys.platform != "win32" else None
    if executable:
        subprocess.Popen([executable, title, message], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def stop_recording_process(process):
    if sys.platform == "win32":
        process.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        process.send_signal(signal.SIGINT)


def recording_process_options():
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def adb(args: list[str], *, serial: str | None = None, timeout: float = 8) -> bytes:
    cmd = ["adb"]
    if serial:
        cmd += ["-s", serial]
    cmd += args
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if result.returncode:
        message = result.stderr.decode(errors="replace").strip() or "ADB command failed"
        raise RuntimeError(message)
    return result.stdout


class ScreenView(QLabel):
    text_input = Signal(str, bool)
    phone_key = Signal(int, int)
    phone_scroll = Signal(int, int, int, int, float, float)
    touch = Signal(int, int, int, int, int)
    tapped = Signal(int, int)
    swipe = Signal(int, int, int, int, int)

    def __init__(self):
        super().__init__("Connect a device to view its screen")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(300, 450)
        self.setStyleSheet("background:#17191e;color:#c7cbd3;border:1px solid #353942;border-radius:10px;")
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, True)
        self.setToolTip("Click a phone text field and type here. Ctrl+V pastes from this computer.")
        self._image_size: tuple[int, int] | None = None
        self._source_pixmap: QPixmap | None = None
        self.rotation = 0
        self._press: tuple[int, int] | None = None
        self._press_time = 0.0
        self._frame_rotation = 0
        self._scroll_velocity = QPointF()
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._scroll_timer.setInterval(16)
        self._scroll_timer.timeout.connect(self._advance_scroll)

    def show_frame(self, image: QImage):
        if image.isNull():
            return
        self._image_size = (image.width(), image.height())
        self._source_pixmap = QPixmap.fromImage(image)
        self._render()

    def show_video_frame(self, image: QImage, rotation: int = 0):
        if image.isNull():
            return
        self._frame_rotation = rotation % 360
        self._image_size = (image.width(), image.height())
        self._source_pixmap = QPixmap.fromImage(image)
        self._render()

    def set_rotation(self, degrees: int):
        self.rotation = degrees % 360
        self._render()

    def _rotated_pixmap(self):
        if not self._source_pixmap:
            return None
        rotation = (self.rotation + self._frame_rotation) % 360
        if rotation:
            return self._source_pixmap.transformed(QTransform().rotate(rotation), Qt.TransformationMode.FastTransformation)
        return self._source_pixmap

    def _render(self):
        pix = self._rotated_pixmap()
        if pix and not pix.isNull():
            self.setPixmap(pix.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event):
        self._render()
        super().resizeEvent(event)

    def _device_point(self, pos):
        if not self._image_size or not self.pixmap():
            return None
        iw, ih = self._image_size
        rotation = (self.rotation + self._frame_rotation) % 360
        transform = QTransform().rotate(rotation)
        bounds = transform.mapRect(QRectF(0, 0, iw, ih))
        display_width = int(round(bounds.width()))
        display_height = int(round(bounds.height()))
        scale = min(self.width() / display_width, self.height() / display_height)
        w, h = display_width * scale, display_height * scale
        left, top = (self.width() - w) / 2, (self.height() - h) / 2
        x, y = pos.x() - left, pos.y() - top
        if x < 0 or y < 0 or x >= w or y >= h:
            return None
        rotated_point = QPointF(x / scale + bounds.left(), y / scale + bounds.top())
        inverse, invertible = transform.inverted()
        if not invertible:
            return None
        source = inverse.map(rotated_point)
        return max(0, min(iw - 1, int(source.x()))), max(0, min(ih - 1, int(source.y())))

    def event(self, event):
        if event.type() == QEvent.Type.ShortcutOverride:
            event.accept()
            return True
        if event.type() == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            self.keyPressEvent(event)
            return True
        return super().event(event)

    def inputMethodEvent(self, event):
        if event.commitString():
            self.text_input.emit(event.commitString(), True)
        event.accept()

    def keyPressEvent(self, event):
        modifiers = event.modifiers()
        ctrl = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        alt = bool(modifiers & Qt.KeyboardModifier.AltModifier)
        meta = (4096 if ctrl else 0) | (1 if shift else 0) | (2 if alt else 0)
        key = event.key()
        if ctrl and not alt and key == Qt.Key.Key_V:
            text = QApplication.clipboard().text()
            if text:
                self.text_input.emit(text, True)
        else:
            keys = {Qt.Key.Key_Backspace: 67, Qt.Key.Key_Delete: 112,
                    Qt.Key.Key_Return: 66, Qt.Key.Key_Enter: 66,
                    Qt.Key.Key_Left: 21, Qt.Key.Key_Right: 22,
                    Qt.Key.Key_Up: 19, Qt.Key.Key_Down: 20,
                    Qt.Key.Key_Home: 122, Qt.Key.Key_End: 123,
                    Qt.Key.Key_PageUp: 92, Qt.Key.Key_PageDown: 93,
                    Qt.Key.Key_Tab: 61, Qt.Key.Key_Backtab: 61,
                    Qt.Key.Key_Escape: 4}
            if key in keys:
                self.phone_key.emit(keys[key], meta | (1 if key == Qt.Key.Key_Backtab else 0))
            elif ctrl and not alt and Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
                self.phone_key.emit(29 + key - Qt.Key.Key_A, meta)
            elif event.text() and all(ch.isprintable() for ch in event.text()):
                text = event.text()
                self.text_input.emit(text, not text.isascii())
        event.accept()

    def keyReleaseEvent(self, event):
        event.accept()

    def stop_scrolling(self):
        self._scroll_timer.stop()
        self._scroll_velocity = QPointF()

    def _advance_scroll(self):
        context = (self._image_size, (self.rotation + self._frame_rotation) % 360)
        if context != self._scroll_context or self._press is not None:
            self.stop_scrolling()
            return
        now = time.monotonic()
        elapsed = min(now - self._scroll_tick, 0.05)
        self._scroll_tick = now
        decay = math.exp(-elapsed / 0.18)
        movement = self._scroll_velocity * (0.18 * (1 - decay))
        self._scroll_velocity *= decay
        self.phone_scroll.emit(*self._scroll_point, *self._image_size, movement.x(), movement.y())
        if math.hypot(self._scroll_velocity.x(), self._scroll_velocity.y()) < 0.08:
            self.stop_scrolling()

    def wheelEvent(self, event):
        point = self._device_point(event.position().toPoint())
        if point is None or self._press is not None:
            event.ignore()
            return
        # Qt already applies the operating system's natural-scroll preference.
        pixels = event.pixelDelta()
        delta = pixels if not pixels.isNull() else event.angleDelta()
        divisor = 40.0 if not pixels.isNull() else 120.0
        horizontal, vertical = delta.x() / divisor, delta.y() / divisor
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier and not horizontal:
            horizontal, vertical = vertical, 0.0
        rotation = (self.rotation + self._frame_rotation) % 360
        direction = QTransform().rotate(-rotation).map(QPointF(horizontal, vertical))
        # Trackpads report smooth motion and often their own momentum phases.
        # Forward them as delivered rather than applying a second inertial tail.
        if not pixels.isNull() or event.phase() != Qt.ScrollPhase.NoScrollPhase:
            self.stop_scrolling()
            if direction.x() or direction.y():
                self.phone_scroll.emit(*point, *self._image_size, direction.x(), direction.y())
        elif direction.x() or direction.y():
            context = (self._image_size, rotation)
            if not self._scroll_timer.isActive() or context != self._scroll_context:
                self.stop_scrolling()
                self._scroll_tick = time.monotonic()
            if QPointF.dotProduct(direction, self._scroll_velocity) < 0:
                self._scroll_velocity = QPointF()
            self._scroll_point = point
            self._scroll_context = context
            self._scroll_velocity += direction / 0.18
            speed = math.hypot(self._scroll_velocity.x(), self._scroll_velocity.y())
            if speed > 40:
                self._scroll_velocity *= 40 / speed
            if not self._scroll_timer.isActive():
                self._scroll_timer.start()
        event.accept()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        point = self._device_point(event.position().toPoint())
        if point is None:
            return
        self.stop_scrolling()
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self._press = point
        self._gesture_size = self._image_size
        self._last_touch = point
        self.touch.emit(0, *point, *self._gesture_size)

    def mouseMoveEvent(self, event):
        if self._press is None:
            return
        point = self._device_point(event.position().toPoint())
        if point is not None and self._image_size == self._gesture_size:
            self._last_touch = point
            self.touch.emit(2, *point, *self._gesture_size)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self._press is None:
            return
        point = self._device_point(event.position().toPoint())
        if point is None or self._image_size != self._gesture_size:
            point = self._last_touch
        self.touch.emit(1, *point, *self._gesture_size)
        self._press = None


class VideoWorker(QThread):
    frame_ready = Signal(QImage, int)
    capture_error = Signal(str)

    def __init__(self, serial: str):
        super().__init__()
        self.serial = serial
        self._process: subprocess.Popen | None = None
        self._socket: socket.socket | None = None
        self._control_socket = None
        self._decoder = None
        self.latest_video_frame = None
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()
        if self._socket:
            try:
                self._socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if self._decoder and self._decoder.poll() is None:
            self._decoder.terminate()
        if self._process and self._process.poll() is None:
            self._process.terminate()

    def send_control(self, packet):
        control = self._control_socket
        if control is None:
            self.capture_error.emit("Keyboard unavailable until the live feed connects")
            return
        try:
            control.sendall(packet)
        except OSError as exc:
            self.capture_error.emit(f"Phone input failed: {exc}")

    def send_key(self, keycode, metastate=0):
        # Android DOWN and UP together keep editing keys from becoming stuck.
        packet = b"".join(struct.pack(">BBIII", 0, action, keycode, 0, metastate)
                          for action in (0, 1))
        self.send_control(packet)

    def send_text(self, text, paste=False):
        if paste:
            payload = text.encode("utf-8")
            if len(payload) > (1 << 18) - 14:
                self.capture_error.emit("Paste is too large (maximum 256 KiB)")
                return
            # Sequence zero disables clipboard acknowledgements; paste=true
            # injects Android's paste key after setting the phone clipboard.
            self.send_control(struct.pack(">BQBI", 9, 0, 1, len(payload)) + payload)
        else:
            chunk = bytearray()
            for char in text:
                encoded = char.encode("utf-8")
                if len(chunk) + len(encoded) > 300:
                    self.send_control(struct.pack(">BI", 1, len(chunk)) + chunk)
                    chunk.clear()
                chunk.extend(encoded)
            if chunk:
                self.send_control(struct.pack(">BI", 1, len(chunk)) + chunk)

    def send_scroll(self, x, y, width, height, horizontal, vertical):
        # scrcpy represents scroll steps in [-16, 16] as signed fixed point.
        def fixed(value):
            return max(-32768, min(32767, round(value * 2048)))
        packet = struct.pack(">BiiHHhhI", 3, x, y, width, height,
                             fixed(horizontal), fixed(vertical), 0)
        self.send_control(packet)

    def send_touch(self, action, x, y, width, height):
        control = self._control_socket
        if control is None:
            return
        # scrcpy INJECT_TOUCH_EVENT: action, pointer, position, pressure, buttons.
        packet = struct.pack(">BBQiiHHHII", 2, action, 0, x, y, width, height,
                             0 if action == 1 else 65535, 0, 0)
        try:
            control.sendall(packet)
        except OSError as exc:
            self.capture_error.emit(f"Touch input failed: {exc}")

    @staticmethod
    def _read_exact(stream, count: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < count:
            chunk = stream.recv(count - len(chunks))
            if not chunk:
                break
            chunks.extend(chunk)
        return bytes(chunks)

    def run(self):
        scrcpy = shutil.which("scrcpy")
        if not scrcpy:
            self.capture_error.emit("Embedded video requires scrcpy.")
            return
        server = None
        client = None
        decoder = None
        try:
            version = "5.0.1"
            server_jar = APP_ROOT / "vendor" / "scrcpy-server"
            if not server_jar.is_file():
                candidates = [Path(scrcpy).parent / "scrcpy-server",
                              Path(scrcpy).parent.parent / "share" / "scrcpy" / "scrcpy-server"]
                server_jar = next((path for path in candidates if path.is_file()), candidates[-1])
                output = subprocess.run([scrcpy, "--version"], capture_output=True, text=True, timeout=5).stdout
                version = output.splitlines()[0].split()[1]
            if not server_jar.is_file():
                raise RuntimeError(f"Could not find scrcpy server at {server_jar}")
            adb(["push", str(server_jar), "/data/local/tmp/scrcpy-server.jar"], serial=self.serial, timeout=20)
            adb(["forward", "tcp:27183", "localabstract:scrcpy_00027183"], serial=self.serial, timeout=5)
            server = subprocess.Popen(
                ["adb", "-s", self.serial, "shell", "CLASSPATH=/data/local/tmp/scrcpy-server.jar", "app_process", "/", "com.genymobile.scrcpy.Server", version,
                 "scid=27183", "tunnel_forward=true", "send_device_meta=false",
                 "audio=false", "video_codec=h264", "max_size=1280", "max_fps=30"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
            self._process = server
            deadline = time.monotonic() + 15
            while not self._stop_event.is_set() and time.monotonic() < deadline:
                try:
                    candidate = socket.create_connection(("127.0.0.1", 27183), timeout=1)
                    try:
                        first_packet = candidate.recv(1)
                    except OSError:
                        candidate.close()
                        raise
                    if not first_packet:
                        candidate.close()
                        if self._stop_event.wait(0.1):
                            break
                        continue
                    client = candidate
                    break
                except OSError:
                    if server.poll() is not None:
                        break
                    time.sleep(0.1)
            if client is None:
                if server.poll() is None:
                    server.terminate()
                output, errors = server.communicate(timeout=3)
                log = (output + errors).decode(errors="replace").strip()
                raise RuntimeError(log or "Could not connect to the scrcpy video stream")
            self._socket = client
            client.settimeout(None)
            self._control_socket = socket.create_connection(("127.0.0.1", 27183), timeout=2)
            self._control_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            codec_header = self._read_exact(client, 4)
            if codec_header != b"h264":
                raise RuntimeError(f"Unexpected scrcpy codec: {codec_header!r}")
            codec = None
            config = b""
            while not self._stop_event.is_set():
                header = self._read_exact(client, 12)
                if len(header) != 12:
                    break
                flags, size = struct.unpack(">QI", header)
                if flags & (1 << 63):
                    # scrcpy 5 sends session metadata whenever the encoder
                    # restarts. Never carry old reference frames into it.
                    codec = av.CodecContext.create("h264", "r")
                    codec.thread_count = 1
                    config = b""
                    continue
                if not 0 < size <= 32 * 1024 * 1024:
                    raise RuntimeError("Invalid scrcpy video packet size")
                data = self._read_exact(client, size)
                if len(data) != size:
                    break
                if flags & (1 << 62):
                    config += data
                    continue
                if codec is None:
                    raise RuntimeError("Missing scrcpy video session")
                for frame in codec.decode(av.Packet(config + data)):
                    rgb = frame.reformat(format="rgb24")
                    plane = rgb.planes[0]
                    image = QImage(bytes(plane), rgb.width, rgb.height,
                                   plane.line_size, QImage.Format.Format_RGB888).copy()
                    self.latest_video_frame = image
                    self.frame_ready.emit(image, 0)
                config = b""
            if not self._stop_event.is_set():
                if server.poll() is None:
                    server.terminate()
                output, errors = server.communicate(timeout=3)
                detail = (output + errors).decode(errors="replace").strip()
                raise RuntimeError(detail or "scrcpy video connection closed")
        except Exception as exc:
            if not self._stop_event.is_set():
                self.capture_error.emit(str(exc))
        finally:
            if self._control_socket:
                self._control_socket.close()
                self._control_socket = None
            if client:
                try:
                    client.close()
                except OSError:
                    pass
            for process in (decoder, server):
                if process and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2)
            try:
                adb(["forward", "--remove", "tcp:27183"], serial=self.serial, timeout=3)
            except Exception:
                pass
            self._process = None
            self._socket = None


class DeviceScanner(QThread):
    devices_ready = Signal(object)
    failed = Signal(str)

    def __init__(self, endpoint, reconnect, identities):
        super().__init__()
        self.endpoint = endpoint
        self.reconnect = reconnect
        self.identities = dict(identities)

    def run(self):
        try:
            def listing():
                result = []
                for line in adb(["devices", "-l"], timeout=4).decode(errors="replace").splitlines()[1:]:
                    fields = line.split()
                    if len(fields) >= 2 and fields[1] == "device":
                        result.append((fields[0], " ".join(fields[2:])))
                return result
            found = listing()
            if self.reconnect and self.endpoint and self.endpoint not in [s for s, _ in found]:
                try:
                    adb(["connect", self.endpoint], timeout=3)
                    found = listing()
                except Exception:
                    pass
            records = []
            for serial, details in found:
                identity = self.identities.get(serial)
                if not identity:
                    try:
                        identity = adb(["shell", "getprop", "ro.serialno"], serial=serial, timeout=2).decode().strip()
                    except Exception:
                        identity = ""
                records.append((serial, details, identity, not (":" in serial or serial.startswith("adb-") or "_adb-tls-" in serial)))
            self.devices_ready.emit(records)
        except Exception as exc:
            self.failed.emit(str(exc))


class ConnectDialog(QDialog):
    def __init__(self, settings: QSettings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Connect over Wi-Fi")
        self.address = QLineEdit()
        self.address.setPlaceholderText("Phone IP address, e.g. 192.168.1.42")
        self.port = QSpinBox()
        self.port.setRange(0, 65535)
        self.port.setSpecialValueText("Enter port")
        self.pair_port = QSpinBox()
        self.pair_port.setRange(0, 65535)
        self.pair_port.setSpecialValueText("Enter pairing port")
        self.pair_code = QLineEdit()
        self.pair_code.setPlaceholderText("Optional 6-digit pairing code")
        self.address.setText(settings.value("wifi/address", ""))
        self.port.setValue(int(settings.value("wifi/port", 0)))
        self.pair_port.setValue(int(settings.value("wifi/pair_port", 0)))
        form = QFormLayout()
        form.addRow("IP address", self.address)
        form.addRow("Connection port", self.port)
        form.addRow("Pairing port (if pairing)", self.pair_port)
        form.addRow("Pairing code (if pairing)", self.pair_code)
        buttons = QDialogButtonBox()
        self.connect_action = buttons.addButton("Connect", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self.connect_action.clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Use the phone IP and connection port. For a new pairing, enter the separate pairing port and code too."))
        layout.addLayout(form)
        layout.addWidget(buttons)


class RecordingSettingsDialog(QDialog):
    def __init__(self, settings: QSettings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Recording settings")
        self.resize(480, 260)
        form = QFormLayout(self)
        self.folder = QLineEdit(settings.value("record/folder", ""))
        browse = QPushButton("Browse…")
        browse.clicked.connect(self.choose_folder)
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.folder, 1)
        folder_row.addWidget(browse)
        form.addRow("Save recordings to", folder_row)

        self.format = QComboBox()
        self.format.addItem("MKV (recommended)", "mkv")
        self.format.addItem("MP4", "mp4")
        self.format.setCurrentIndex(max(0, self.format.findData(settings.value("record/format", "mkv"))))
        form.addRow("File format", self.format)

        self.size = QComboBox()
        for label, value in (("Original resolution", "0"), ("1920 px", "1920"), ("1280 px", "1280"), ("1024 px", "1024")):
            self.size.addItem(label, value)
        self.size.setCurrentIndex(max(0, self.size.findData(str(settings.value("record/size", "0")))))
        form.addRow("Maximum video size", self.size)

        self.fps = QComboBox()
        for value in ("24", "30", "60", "90", "120"):
            self.fps.addItem(f"{value} fps", value)
        self.fps.setCurrentIndex(max(0, self.fps.findData(str(settings.value("record/fps", "30")))))
        form.addRow("Frame rate limit", self.fps)

        self.bitrate = QComboBox()
        for label, value in (("4 Mbps", "4M"), ("8 Mbps", "8M"), ("16 Mbps", "16M"), ("32 Mbps", "32M")):
            self.bitrate.addItem(label, value)
        self.bitrate.setCurrentIndex(max(0, self.bitrate.findData(settings.value("record/bitrate", "8M"))))
        form.addRow("Video bitrate", self.bitrate)

        self.audio_source = QComboBox()
        self.audio_source.addItem("App playback (some apps may block capture)", "playback")
        self.audio_source.addItem("Full device output (may mute phone speaker)", "output")
        self.audio_source.addItem("Phone microphone", "mic")
        self.audio_source.setCurrentIndex(max(0, self.audio_source.findData(settings.value("record/audio_source", "output"))))
        form.addRow("Audio source", self.audio_source)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def choose_folder(self):
        initial = self.folder.text() or QStandardPaths.writableLocation(QStandardPaths.StandardLocation.MoviesLocation) or str(Path.home() / "Videos")
        folder = QFileDialog.getExistingDirectory(self, "Choose recording folder", initial)
        if folder:
            self.folder.setText(folder)

    def save(self):
        self.settings.setValue("record/folder", self.folder.text().strip())
        self.settings.setValue("record/format", self.format.currentData())
        self.settings.setValue("record/size", self.size.currentData())
        self.settings.setValue("record/fps", self.fps.currentData())
        self.settings.setValue("record/bitrate", self.bitrate.currentData())
        self.settings.setValue("record/audio_source", self.audio_source.currentData())
        self.settings.sync()
        self.accept()


class Controller(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Android ADB Controller")
        self.resize(1000, 760)
        self.serial: str | None = None
        self.connection_paused = False
        if sys.platform == "win32":
            config_dir = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation))
        else:
            config_dir = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        config_dir.mkdir(parents=True, exist_ok=True)
        self.settings = QSettings(str(config_dir / "adb-phone-control.ini"), QSettings.Format.IniFormat)
        self.latest_frame: QImage | None = None
        self.screen = ScreenView()
        self.screen_host = QWidget()
        self.screen_host_layout = QVBoxLayout(self.screen_host)
        self.screen_host_layout.setContentsMargins(0, 0, 0, 0)
        self.screen_host_layout.addWidget(self.screen)
        self.screen.set_rotation(int(self.settings.value("preview/rotation", 0)))
        self.devices = QComboBox()
        self.connection_badge = QWidget()
        badge_layout = QHBoxLayout(self.connection_badge)
        badge_layout.setContentsMargins(10, 4, 10, 4)
        badge_layout.setSpacing(7)
        self.connection_light = QLabel()
        self.connection_light.setFixedSize(10, 10)
        self.connection_glow = QGraphicsDropShadowEffect(self.connection_light)
        self.connection_glow.setOffset(0, 0)
        self.connection_glow.setBlurRadius(18)
        self.connection_glow.setColor(QColor("#40f58c"))
        self.connection_light.setGraphicsEffect(self.connection_glow)
        self.connection_type = QLabel()
        self.connection_type.setMinimumWidth(64)
        badge_layout.addWidget(self.connection_light)
        badge_layout.addWidget(self.connection_type)
        self.update_connection_badge(None)
        self.status = QLabel("Looking for ADB devices…")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.status.setMinimumHeight(48)
        self.status.setMaximumHeight(96)
        self.status.setStyleSheet("color:#aeb5c2")
        self.refresh_button = QPushButton("Refresh devices")
        self.refresh_button.clicked.connect(self.refresh_devices)
        self.connect_button = QPushButton("Connect over Wi-Fi…")
        self.connect_button.clicked.connect(self.wifi_connect)
        self.capture_button = QPushButton("Save screenshot")
        self.capture_button.setToolTip("Save the latest phone screen image in Pictures")
        self.capture_button.clicked.connect(self.save_screenshot)
        self.scrcpy_button = QPushButton("Restart live feed")
        self.scrcpy_button.setToolTip("Restart the embedded scrcpy video stream")
        self.scrcpy_button.clicked.connect(self.open_scrcpy)
        self.audio_button = QPushButton("Play phone audio")
        self.audio_button.setToolTip("Stream the selected phone's audio to this laptop")
        self.audio_button.clicked.connect(self.open_audio)
        self.devices.currentIndexChanged.connect(self.select_device)
        top = QHBoxLayout()
        top.addWidget(QLabel("Device"))
        top.addWidget(self.devices, 1)
        top.addWidget(self.connection_badge)
        self.disconnect_button = QPushButton("Disconnect")
        self.disconnect_button.clicked.connect(self.disconnect_phone)
        self.reconnect_button = QPushButton("Reconnect")
        self.reconnect_button.clicked.connect(self.reconnect_phone)
        top.addWidget(self.disconnect_button)
        top.addWidget(self.reconnect_button)
        top.addWidget(self.refresh_button)
        top.addWidget(self.connect_button)
        top.addWidget(self.capture_button)
        top.addWidget(self.scrcpy_button)
        top.addWidget(self.audio_button)

        self.install_button = QPushButton("Install APK…")
        self.install_button.clicked.connect(self.install_apk)
        top.addWidget(self.install_button)

        self.back = QPushButton("← Back")
        self.home = QPushButton("⌂ Home")
        self.overview = QPushButton("▢ Recent apps")
        for button, key in ((self.back, "BACK"), (self.home, "HOME"), (self.overview, "APP_SWITCH")):
            button.clicked.connect(lambda checked=False, k=key: self.keyevent(k))
        nav = QHBoxLayout()
        nav.addWidget(self.back)
        nav.addWidget(self.home)
        nav.addWidget(self.overview)

        self.text = QLineEdit()
        self.text.setPlaceholderText("Type text to send to the phone…")
        send_text = QPushButton("Send text")
        send_text.clicked.connect(self.send_text)
        self.text.returnPressed.connect(self.send_text)
        text_row = QHBoxLayout()
        text_row.addWidget(self.text, 1)
        text_row.addWidget(send_text)

        self.x = QSpinBox(); self.x.setRange(0, 20000); self.x.setPrefix("X ")
        self.y = QSpinBox(); self.y.setRange(0, 20000); self.y.setPrefix("Y ")
        tap_btn = QPushButton("Tap coordinates")
        tap_btn.clicked.connect(lambda: self.tap(self.x.value(), self.y.value()))
        self.tap_scale = QDoubleSpinBox(); self.tap_scale.setRange(0.1, 4.0); self.tap_scale.setSingleStep(0.1); self.tap_scale.setValue(1.0)
        coords = QHBoxLayout()
        coords.addWidget(self.x); coords.addWidget(self.y); coords.addWidget(tap_btn)
        coords.addWidget(QLabel("Scale")); coords.addWidget(self.tap_scale)

        controls = QVBoxLayout()
        controls.addLayout(nav)
        controls.addLayout(text_row)
        controls.addLayout(coords)
        rotate_row = QHBoxLayout()
        rotate_ccw = QPushButton("↶ Rotate left")
        rotate_cw = QPushButton("Rotate right ↷")
        self.rotation_label = QLabel(f"{self.screen.rotation}°")
        rotate_ccw.clicked.connect(lambda: self.rotate_preview(-90))
        rotate_cw.clicked.connect(lambda: self.rotate_preview(90))
        rotate_row.addWidget(rotate_ccw)
        rotate_row.addWidget(self.rotation_label)
        rotate_row.addWidget(rotate_cw)
        controls.addLayout(rotate_row)
        preview_help = QLabel("Click the phone to tap or type. Drag to swipe or use the scroll wheel. Ctrl+V pastes text.")
        preview_help.setWordWrap(True)
        controls.addWidget(preview_help)
        recording_group = QGroupBox("Record video + audio")
        recording_layout = QHBoxLayout(recording_group)
        self.record_start_button = QPushButton("Start recording")
        self.record_start_button.clicked.connect(self.start_recording)
        self.record_stop_button = QPushButton("Stop")
        self.record_stop_button.setEnabled(False)
        self.record_stop_button.clicked.connect(self.stop_recording)
        record_settings_button = QPushButton("Settings…")
        record_settings_button.clicked.connect(self.open_recording_settings)
        recording_layout.addWidget(self.record_start_button)
        recording_layout.addWidget(self.record_stop_button)
        recording_layout.addWidget(record_settings_button)
        controls.addWidget(recording_group)
        app_group = QGroupBox("Apps on device")
        app_layout = QVBoxLayout(app_group)
        app_top = QHBoxLayout()
        self.include_system_apps = QCheckBox("Include system apps")
        self.include_system_apps.toggled.connect(self.refresh_apps)
        refresh_apps_button = QPushButton("Refresh")
        refresh_apps_button.clicked.connect(self.refresh_apps)
        app_top.addWidget(self.include_system_apps)
        app_top.addWidget(refresh_apps_button)
        app_layout.addLayout(app_top)
        self.app_search = QLineEdit()
        self.app_search.setPlaceholderText("Filter package names…")
        self.app_search.textChanged.connect(self.filter_apps)
        app_layout.addWidget(self.app_search)
        self.app_list = QListWidget()
        self.app_list.setMaximumHeight(190)
        app_layout.addWidget(self.app_list)
        app_actions = QHBoxLayout()
        launch_app_button = QPushButton("Launch")
        launch_app_button.clicked.connect(self.launch_selected_app)
        self.uninstall_button = QPushButton("Uninstall")
        self.uninstall_button.clicked.connect(self.uninstall_selected_app)
        app_actions.addWidget(launch_app_button)
        app_actions.addWidget(self.uninstall_button)
        app_layout.addLayout(app_actions)
        controls.addWidget(app_group)
        right = QWidget(); right.setLayout(controls); right.setMaximumWidth(380)

        body = QHBoxLayout()
        body.addWidget(self.screen_host, 1)
        body.addWidget(right)
        root = QVBoxLayout()
        root.addLayout(top)
        root.addWidget(self.status)
        root.addLayout(body, 1)
        container = QWidget(); container.setLayout(root)
        self.setCentralWidget(container)

        self.screen.text_input.connect(self.send_preview_text)
        self.screen.phone_key.connect(self.send_preview_key)
        self.screen.phone_scroll.connect(self.send_preview_scroll)
        self.screen.touch.connect(self.send_preview_touch)
        self.screen.tapped.connect(self.tap)
        self.screen.swipe.connect(self.swipe)
        self.refresh_timer = QTimer(self); self.refresh_timer.timeout.connect(self.refresh_devices); self.refresh_timer.start(3000)
        self.frame_worker: VideoWorker | None = None
        self.audio_process: subprocess.Popen | None = None
        self.record_process: subprocess.Popen | None = None
        self.record_log = None
        self.record_path: Path | None = None
        self.record_timer = QTimer(self)
        self.record_timer.timeout.connect(self.check_recording)
        self.record_timer.start(500)
        self.refresh_devices()

    def report_error(self, error: Exception):
        self.status.setText(f"ADB error: {error}")

    def refresh_devices(self):
        if self.connection_paused:
            return
        scanner = getattr(self, "device_scanner", None)
        if scanner and scanner.isRunning():
            return
        host = self.settings.value("wifi/address", "")
        port = self.settings.value("wifi/port", 0)
        endpoint = f"{host}:{port}" if host and port else ""
        reconnect = time.monotonic() >= getattr(self, "next_wifi_retry", 0)
        if reconnect:
            self.next_wifi_retry = time.monotonic() + 15
        self.device_scanner = DeviceScanner(endpoint, reconnect, getattr(self, "device_identities", {}))
        self.device_scanner.devices_ready.connect(self.apply_devices)
        self.device_scanner.failed.connect(lambda message: self.status.setText(f"ADB unavailable: {message}"))
        self.device_scanner.start()

    def apply_devices(self, records):
        if self.connection_paused:
            return
        self.device_records = {r[0]: r for r in records}
        self.device_identities = {r[0]: r[2] for r in records if r[2]}
        previous = self.serial or self.settings.value("device/serial", "")
        identity = self.settings.value("device/identity", "")
        if not identity:
            identity = self.device_identities.get(previous, "")
        matching = [r for r in records if identity and r[2] == identity]
        if matching:
            selected = min(matching, key=lambda r: (not r[3], r[0] != previous))[0]
        elif identity:
            selected = None  # Wait for the same phone, even if another is connected.
        else:
            selected = previous if previous in self.device_records else (records[0][0] if records else None)
        self.devices.blockSignals(True)
        self.devices.clear()
        for serial, details, _, wired in records:
            self.devices.addItem(f"{'USB' if wired else 'Wi-Fi'} · {serial}  {details}", serial)
        self.devices.setCurrentIndex(self.devices.findData(selected) if selected else -1)
        self.devices.blockSignals(False)
        self.select_device()
        if not selected:
            self.status.setText("Waiting for phone over USB or paired Wi-Fi…")

    def disconnect_phone(self):
        self.connection_paused = True
        self.devices.blockSignals(True)
        self.devices.setCurrentIndex(-1)
        self.devices.blockSignals(False)
        self.select_device()
        self.devices.setEnabled(False)
        self.disconnect_button.setEnabled(False)
        self.status.setText("Disconnected · automatic reconnect paused")

    def reconnect_phone(self):
        self.connection_paused = False
        self.devices.setEnabled(True)
        self.disconnect_button.setEnabled(True)
        self.next_wifi_retry = 0
        self.status.setText("Reconnecting over USB or paired Wi-Fi…")
        self.refresh_devices()

    def update_connection_badge(self, serial):
        record = getattr(self, "device_records", {}).get(serial)
        connected = bool(serial)
        wired = record[3] if record else bool(serial and ":" not in serial and not serial.startswith("adb-"))
        label = ("USB" if wired else "Wi-Fi") if connected else "Offline"
        self.connection_type.setText(label)
        self.connection_type.setStyleSheet(f"color:{'#83ffb4' if connected else '#9299a5'};font-weight:600;")
        self.connection_light.setStyleSheet(f"background:{'#40f58c' if connected else '#626975'};border-radius:5px;")
        self.connection_glow.setEnabled(connected)
        self.connection_badge.setToolTip(f"Active ADB connection: {label}\n{serial}" if connected else "No phone connected")
        self.connection_badge.setAccessibleName(f"Connection: {label}")

    def select_device(self):
        selected = self.devices.currentData()
        if selected == self.serial and self.frame_worker:
            self.update_connection_badge(selected)
            return
        self.screen.stop_scrolling()
        if self.record_process and self.record_process.poll() is None:
            self.stop_recording()
        if self.frame_worker:
            self.frame_worker.stop()
            if not self.frame_worker.wait(25000):
                self.status.setText("Waiting for previous connection to stop…")
                return
            self.frame_worker = None
        self.serial = selected
        self.update_connection_badge(selected)
        if selected:
            identity = getattr(self, "device_identities", {}).get(selected)
            if identity:
                self.settings.setValue("device/identity", identity)
            record = getattr(self, "device_records", {}).get(selected)
            if record and not record[3] and ":" in selected:
                host, port = selected.rsplit(":", 1)
                if port.isdigit():
                    self.settings.setValue("wifi/address", host)
                    self.settings.setValue("wifi/port", int(port))
            self.settings.setValue("device/serial", selected)
            self.settings.sync()
        if self.serial:
            self.status.setText(f"Connected to {self.serial}")
            self.open_scrcpy()
            self.refresh_apps()
        else:
            self.latest_frame = None
            self.screen.clear()
            self.screen.setText("Connect a device to view its screen")
            self.app_list.clear()
            self.scrcpy_button.setEnabled(True)

    def start_video_preview(self):
        if not self.serial:
            return
        if self.frame_worker:
            self.frame_worker.stop()
            if not self.frame_worker.wait(25000):
                self.status.setText("Previous video stream is still stopping")
                return
        self.frame_worker = VideoWorker(self.serial)
        # Poll the newest frame instead of queueing every decoded image.
        if not hasattr(self, "video_timer"):
            self.video_timer = QTimer(self)
            self.video_timer.timeout.connect(self.present_video_frame)
            self.video_timer.start(16)
        self.frame_worker.capture_error.connect(self.status.setText)
        self.frame_worker.start()

    def wifi_connect(self):
        dialog = ConnectDialog(self.settings, self)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        host = dialog.address.text().strip()
        if not host:
            QMessageBox.warning(self, "Address required", "Enter the phone's IP address shown in Wireless debugging.")
            return
        if not dialog.port.value():
            QMessageBox.warning(self, "Port required", "Enter the current connection port shown in Wireless debugging.")
            return
        self.settings.setValue("wifi/address", host)
        self.settings.setValue("wifi/port", dialog.port.value())
        self.settings.setValue("wifi/pair_port", dialog.pair_port.value())
        self.settings.sync()
        try:
            code = dialog.pair_code.text().strip()
            if code:
                if not dialog.pair_port.value():
                    QMessageBox.warning(self, "Pairing port required", "Enter the separate pairing port shown on the Pair device with pairing code screen.")
                    return
                paired = adb(["pair", f"{host}:{dialog.pair_port.value()}", code], timeout=30).decode(errors="replace").strip()
                if "successfully paired" not in paired.lower():
                    QMessageBox.warning(self, "Pairing failed", paired or "ADB did not confirm pairing.")
                    self.status.setText(paired or "Pairing failed")
                    return
            result = adb(["connect", f"{host}:{dialog.port.value()}"], timeout=30).decode(errors="replace").strip()
            self.status.setText(result or "ADB connect completed.")
            if "connected to" not in result.lower() and "already connected" not in result.lower():
                QMessageBox.warning(self, "Connection failed", result or "ADB did not confirm the connection.")
                return
            self.reconnect_phone()
            QMessageBox.information(self, "Phone connected", result)
        except Exception as exc:
            self.report_error(exc)
            QMessageBox.critical(self, "ADB connection failed", str(exc))

    def run_device(self, args):
        if not self.serial:
            self.status.setText("Connect and select an authorized device first.")
            return None
        try:
            return adb(args, serial=self.serial, timeout=3)
        except Exception as exc:
            self.report_error(exc)
            return None

    def present_video_frame(self):
        worker = self.frame_worker
        if worker is None:
            return
        image = worker.latest_video_frame
        if image is not None and image is not self.latest_frame:
            self.on_video_frame(image, 0)

    def on_video_frame(self, image: QImage, rotation: int):
        self.latest_frame = image
        self.screen.show_video_frame(image, rotation)
        self.x.setMaximum(max(0, image.width() - 1)); self.y.setMaximum(max(0, image.height() - 1))
        if self.serial:
            self.status.setText(f"Live scrcpy feed {image.width()} × {image.height()} · rotation {rotation}°")

    def rotate_preview(self, delta: int):
        rotation = (self.screen.rotation + delta) % 360
        self.screen.rotation = rotation
        self.screen._render()
        self.rotation_label.setText(f"{rotation}°")
        self.settings.setValue("preview/rotation", rotation)
        self.settings.sync()

    def save_screenshot(self):
        image = self.latest_frame
        if (image is None or image.isNull()) and self.serial:
            try:
                image = QImage.fromData(adb(["exec-out", "screencap", "-p"], serial=self.serial, timeout=8), "PNG")
            except Exception as exc:
                QMessageBox.warning(self, "Capture failed", str(exc))
                return
        if image is None or image.isNull():
            QMessageBox.information(self, "No screen image", "Connect to an authorized device first.")
            return
        pictures = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation)
        folder = Path(pictures) if pictures else Path.home() / "Pictures"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / f"android-screen-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.png"
            if not image.save(str(target), "PNG"):
                raise OSError("Qt could not write the PNG image")
            self.status.setText(f"Screenshot saved: {target}")
            notify("Phone screenshot saved", str(target))
        except Exception as exc:
            QMessageBox.critical(self, "Screenshot save failed", str(exc))

    def open_scrcpy(self):
        if not self.serial:
            self.status.setText("Connect and select a device first.")
            return
        if not shutil.which("scrcpy"):
            self.status.setText("Install scrcpy to enable the embedded live feed.")
            return
        self.start_video_preview()
        self.status.setText(f"Starting embedded scrcpy video for {self.serial}…")

    def open_audio(self):
        if not self.serial:
            self.status.setText("Connect and select a device first.")
            return
        executable = shutil.which("scrcpy")
        if not executable:
            QMessageBox.information(self, "scrcpy required", "Install scrcpy to stream phone audio:\n\n    sudo pacman -S scrcpy")
            return
        try:
            subprocess.Popen([executable, "--serial", self.serial, "--no-video"], start_new_session=True)
        except Exception as exc:
            self.report_error(exc)

    def open_recording_settings(self):
        RecordingSettingsDialog(self.settings, self).exec()

    def start_recording(self):
        if not self.serial:
            QMessageBox.information(self, "No device selected", "Connect and select an authorized device first.")
            return
        if self.record_process and self.record_process.poll() is None:
            self.status.setText("A recording is already running.")
            return
        executable = shutil.which("scrcpy")
        if not executable:
            QMessageBox.critical(self, "scrcpy not found", "Install scrcpy to record phone video and audio.")
            return
        folder_value = self.settings.value("record/folder", "")
        folder = Path(folder_value) if folder_value else Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.MoviesLocation) or Path.home() / "Videos")
        extension = self.settings.value("record/format", "mkv")
        try:
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / f"phone-recording-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.{extension}"
            args = [executable, "--serial", self.serial, f"--record={target}", "--no-playback"]
            args += [f"--audio-source={self.settings.value('record/audio_source', 'output')}", "--require-audio"]
            args += [f"--audio-codec={'aac' if extension == 'mp4' else 'opus'}"]
            args += [f"--max-fps={self.settings.value('record/fps', '30')}"]
            args += [f"--video-bit-rate={self.settings.value('record/bitrate', '8M')}"]
            max_size = str(self.settings.value("record/size", "0"))
            if max_size != "0":
                args += [f"--max-size={max_size}"]
            args.append("--no-window")
            import tempfile
            self.record_log = tempfile.TemporaryFile(mode="w+b")
            self.record_path = target
            self.record_process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=self.record_log, **recording_process_options())
            self.record_start_button.setEnabled(False)
            self.record_stop_button.setEnabled(True)
            self.status.setText(f"Recording to {target}")
        except Exception as exc:
            self.record_process = None
            self.record_path = None
            self.report_error(exc)
            QMessageBox.critical(self, "Could not start recording", str(exc))

    def stop_recording(self):
        if self.record_process and self.record_process.poll() is None:
            self.status.setText("Finishing recording…")
            try:
                stop_recording_process(self.record_process)
            except ProcessLookupError:
                pass
            QTimer.singleShot(5000, self.force_stop_recording)

    def force_stop_recording(self):
        if self.record_process and self.record_process.poll() is None:
            self.record_process.terminate()

    def check_recording(self):
        if not self.record_process or self.record_process.poll() is None:
            return
        return_code = self.record_process.returncode
        self.record_process = None
        self.record_start_button.setEnabled(True)
        self.record_stop_button.setEnabled(False)
        log_text = ""
        if self.record_log:
            self.record_log.seek(0)
            log_text = self.record_log.read().decode(errors="replace").strip()
            self.record_log.close()
            self.record_log = None
        if return_code == 0 and self.record_path and self.record_path.exists():
            self.status.setText(f"Recording saved: {self.record_path}")
            notify("Phone recording saved", str(self.record_path))
        else:
            message = log_text or f"scrcpy exited with status {return_code}"
            self.status.setText("Recording failed")
            QMessageBox.warning(self, "Recording failed", message)
        self.record_path = None

    def install_apk(self):
        if not self.serial:
            QMessageBox.information(self, "No device selected", "Connect and select an authorized device first.")
            return
        filename, _ = QFileDialog.getOpenFileName(self, "Choose APK to install", str(Path.home()), "Android packages (*.apk)")
        if not filename:
            return
        self.status.setText(f"Installing {Path(filename).name}…")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            output = adb(["install", "-r", filename], serial=self.serial, timeout=180).decode(errors="replace").strip()
            if "success" in output.lower():
                self.status.setText(f"Installed {Path(filename).name}")
                QMessageBox.information(self, "Install complete", output or "APK installed successfully.")
                self.refresh_apps()
            else:
                self.status.setText("APK installation failed")
                QMessageBox.warning(self, "Install failed", output or "ADB did not report success.")
        except Exception as exc:
            self.report_error(exc)
            QMessageBox.critical(self, "Install failed", str(exc))
        finally:
            QApplication.restoreOverrideCursor()

    def refresh_apps(self, *_):
        if not hasattr(self, "app_list") or not self.serial:
            return
        try:
            args = ["shell", "pm", "list", "packages"]
            if not self.include_system_apps.isChecked():
                args.append("-3")
            output = adb(args, serial=self.serial, timeout=20).decode(errors="replace")
            selected = self.app_list.currentItem().data(Qt.ItemDataRole.UserRole) if self.app_list.currentItem() else None
            self.app_list.clear()
            for line in sorted(output.splitlines(), key=str.casefold):
                package = line.removeprefix("package:").strip()
                if package:
                    item = QListWidgetItem(package)
                    item.setData(Qt.ItemDataRole.UserRole, package)
                    self.app_list.addItem(item)
            self.filter_apps()
            if selected:
                for index in range(self.app_list.count()):
                    item = self.app_list.item(index)
                    if item.data(Qt.ItemDataRole.UserRole) == selected:
                        self.app_list.setCurrentItem(item)
                        break
        except Exception as exc:
            self.status.setText(f"Could not list apps: {exc}")

    def filter_apps(self, *_):
        query = self.app_search.text().casefold() if hasattr(self, "app_search") else ""
        if not hasattr(self, "app_list"):
            return
        for index in range(self.app_list.count()):
            item = self.app_list.item(index)
            item.setHidden(query not in item.text().casefold())

    def selected_package(self):
        item = self.app_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def launch_selected_app(self):
        package = self.selected_package()
        if not package:
            self.status.setText("Select an app first.")
            return
        try:
            output = adb(["shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"], serial=self.serial, timeout=15).decode(errors="replace")
            self.status.setText(f"Launched {package}")
        except Exception as exc:
            self.report_error(exc)

    def uninstall_selected_app(self):
        package = self.selected_package()
        if not package:
            self.status.setText("Select an app first.")
            return
        if self.include_system_apps.isChecked():
            QMessageBox.information(self, "System apps", "Uninstall is limited to user-installed apps. Turn off ‘Include system apps’ and select a user app.")
            return
        answer = QMessageBox.question(self, "Uninstall app?", f"Uninstall {package} from this device?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            output = adb(["uninstall", package], serial=self.serial, timeout=60).decode(errors="replace").strip()
            if "success" in output.lower():
                self.status.setText(f"Uninstalled {package}")
                self.refresh_apps()
            else:
                QMessageBox.warning(self, "Uninstall failed", output or "ADB did not report success.")
        except Exception as exc:
            self.report_error(exc)
            QMessageBox.critical(self, "Uninstall failed", str(exc))

    def send_preview_text(self, text, paste):
        if self.frame_worker:
            self.frame_worker.send_text(text, paste)

    def send_preview_key(self, keycode, metastate):
        if self.frame_worker:
            self.frame_worker.send_key(keycode, metastate)

    def send_preview_scroll(self, x, y, width, height, horizontal, vertical):
        if self.frame_worker:
            self.frame_worker.send_scroll(x, y, width, height, horizontal, vertical)

    def send_preview_touch(self, action, x, y, width, height):
        if self.frame_worker:
            self.frame_worker.send_touch(action, x, y, width, height)

    def tap(self, x: int, y: int):
        self.x.setValue(x); self.y.setValue(y)
        scale = self.tap_scale.value()
        self.run_device(["shell", "input", "tap", str(round(x * scale)), str(round(y * scale))])

    def swipe(self, x1, y1, x2, y2, duration):
        scale = self.tap_scale.value()
        args = ["shell", "input", "swipe", str(round(x1 * scale)), str(round(y1 * scale)), str(round(x2 * scale)), str(round(y2 * scale)), str(duration)]
        self.run_device(args)

    def keyevent(self, key: str):
        self.run_device(["shell", "input", "keyevent", key])

    def send_text(self):
        value = self.text.text()
        if not value:
            return
        # Android input text treats spaces as %s. Escape shell metacharacters by
        # passing the full command as one safely quoted argument to `adb shell`.
        safe = value.replace("%", "%25").replace(" ", "%s")
        self.run_device(["shell", "input", "text", safe])
        self.text.clear()

    def closeEvent(self, event):
        self.refresh_timer.stop()
        scanner = getattr(self, "device_scanner", None)
        if scanner and scanner.isRunning():
            scanner.wait(15000)
        if self.frame_worker:
            self.frame_worker.stop()
            self.frame_worker.wait(3000)
        if self.record_process and self.record_process.poll() is None:
            try:
                stop_recording_process(self.record_process)
                self.record_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.record_process.terminate()
                self.record_process.wait(timeout=2)
        super().closeEvent(event)


def main():
    if "--version" in sys.argv:
        print(f"Android ADB Controller {__version__}")
        return 0
    if not shutil_which("adb"):
        app = QApplication(sys.argv)
        QMessageBox.critical(None, "ADB not found", "Install Android platform tools (adb), or use the bundled Windows release.")
        return 127
    app = QApplication(sys.argv)
    app.setApplicationName("Android ADB Controller")
    window = Controller()
    window.show()
    return app.exec()


def shutil_which(program: str) -> str | None:
    from shutil import which
    return which(program)


if __name__ == "__main__":
    raise SystemExit(main())
