#!/usr/bin/env python3
"""Small PySide6 desktop controller for an Android device over ADB."""

from __future__ import annotations

import os
import re
import math
import shutil
import socket
import av
from adb_phone_controller import __version__
from adb_phone_controller.file_browser import FileBrowserDialog
from adb_phone_controller.updater import UpdateDialog
from adb_phone_controller.remembered_devices import DeviceLibrary, RememberedDevicesDialog
import struct
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal, QThread, QSettings, QStandardPaths, QRectF, QPointF, QEvent, QProcess
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


AUDIO_SOURCES = (
    ("Full device output", "output"),
    ("App playback", "playback"),
    ("Phone microphone", "mic"),
    ("Call — both sides (if supported)", "voice-call"),
    ("Call — your voice (if supported)", "voice-call-uplink"),
    ("Call — caller's voice (if supported)", "voice-call-downlink"),
)
CALL_AUDIO_NOTE = "Call capture depends on Android and phone firmware. It may be rejected or silent, even while a session is running."


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
        self.short_video_navigation = True
        self._key_swipe = None
        self._key_swipe_timer = QTimer(self)
        self._key_swipe_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._key_swipe_timer.setInterval(16)
        self._key_swipe_timer.timeout.connect(self._advance_key_swipe)

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
        if (self.short_video_navigation and not modifiers
                and key in (Qt.Key.Key_Up, Qt.Key.Key_Down)):
            if not event.isAutoRepeat():
                self.start_key_swipe(key == Qt.Key.Key_Down)
            event.accept()
            return
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

    def start_key_swipe(self, next_video):
        if self._image_size is None or self._press is not None or self._key_swipe is not None:
            return
        self.stop_scrolling()
        width, height = self._image_size
        rotation = (self.rotation + self._frame_rotation) % 360
        display_width, display_height = (height, width) if rotation % 180 else (width, height)
        scale = min(self.width() / display_width, self.height() / display_height)
        offset = display_height * scale * 0.27
        center = QPointF(self.width() / 2, self.height() / 2)
        start = self._device_point(center + QPointF(0, offset if next_video else -offset))
        end = self._device_point(center + QPointF(0, -offset if next_video else offset))
        if start is None or end is None:
            return
        self._key_swipe = (start, end, self._image_size, rotation, time.monotonic())
        self._key_swipe_last = start
        self.touch.emit(0, *start, width, height)
        self._key_swipe_timer.start()

    def _advance_key_swipe(self):
        if self._key_swipe is None:
            return
        start, end, size, rotation, started = self._key_swipe
        if size != self._image_size or rotation != (self.rotation + self._frame_rotation) % 360:
            self.cancel_key_swipe()
            return
        progress = min(1.0, (time.monotonic() - started) / 0.20)
        point = tuple(round(a + (b - a) * progress) for a, b in zip(start, end))
        self._key_swipe_last = point
        self.touch.emit(2, *point, *size)
        if progress >= 1:
            self.cancel_key_swipe()

    def cancel_key_swipe(self):
        self._key_swipe_timer.stop()
        if self._key_swipe is not None:
            size = self._key_swipe[2]
            self._key_swipe = None
            self.touch.emit(1, *self._key_swipe_last, *size)

    def stop_scrolling(self):
        self.cancel_key_swipe()
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


class WebcamWorker(QThread):
    output_ready = Signal(str)
    output_error = Signal(str)

    def __init__(self, source, device=""):
        super().__init__()
        self.source = source
        self.device = device
        self.rotation = 0
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def run(self):
        try:
            if sys.platform != "win32":
                optional = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "adb-phone-controller/webcam"
                if optional.is_dir() and str(optional) not in sys.path:
                    sys.path.append(str(optional))
            import numpy as np
            import pyvirtualcam
            options = {"device": self.device} if self.device else {}
            # Keep the webcam format stable when the phone rotates or reconnects.
            with pyvirtualcam.Camera(width=1280, height=720, fps=30, **options) as camera:
                self.output_ready.emit(f"Webcam active: {camera.device}")
                while not self._stop_event.is_set():
                    image = self.source.latest_video_frame
                    canvas = np.zeros((720, 1280, 3), dtype=np.uint8)
                    if image is not None:
                        image = image.transformed(QTransform().rotate(self.rotation))
                        image = image.scaled(1280, 720, Qt.AspectRatioMode.KeepAspectRatio,
                                             Qt.TransformationMode.SmoothTransformation)
                        image = image.convertToFormat(QImage.Format.Format_RGB888)
                        pixels = np.frombuffer(image.constBits(), dtype=np.uint8).reshape(
                            image.height(), image.bytesPerLine())[:, :image.width() * 3]
                        pixels = pixels.reshape(image.height(), image.width(), 3)
                        x, y = (1280 - image.width()) // 2, (720 - image.height()) // 2
                        canvas[y:y + image.height(), x:x + image.width()] = pixels
                    camera.send(canvas)
                    if self._stop_event.wait(1 / 30):
                        break
        except Exception as exc:
            self.output_error.emit(str(exc))


class VideoWorker(QThread):
    frame_ready = Signal(QImage, int)
    capture_error = Signal(str)

    def __init__(self, serial: str, source="display"):
        super().__init__()
        self.serial = serial
        self.source = source
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
            source_options = []
            if self.source != "display":
                source_options = ["video_source=camera", f"camera_facing={self.source}",
                                  "camera_ar=16:9", "camera_fps=30", "control=false"]
            server = subprocess.Popen(
                ["adb", "-s", self.serial, "shell", "CLASSPATH=/data/local/tmp/scrcpy-server.jar", "app_process", "/", "com.genymobile.scrcpy.Server", version,
                 "scid=27183", "tunnel_forward=true", "send_device_meta=false",
                 "audio=false", "video_codec=h264", "max_size=1280", "max_fps=30"] + source_options,
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
            if self.source == "display":
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
                if identity.lower() in ("unknown", "null", "none"):
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
        for label, source in AUDIO_SOURCES:
            self.audio_source.addItem(label, source)
        self.audio_source.setCurrentIndex(max(0, self.audio_source.findData(settings.value("record/audio_source", "output"))))
        form.addRow("Audio source", self.audio_source)
        audio_note = QLabel(CALL_AUDIO_NOTE)
        audio_note.setWordWrap(True)
        form.addRow(audio_note)

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
        self.webcam_worker = None
        if sys.platform == "win32":
            config_dir = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation))
        else:
            config_dir = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        config_dir.mkdir(parents=True, exist_ok=True)
        self.settings = QSettings(str(config_dir / "adb-phone-control.ini"), QSettings.Format.IniFormat)
        self.device_library = DeviceLibrary(self.settings)
        self.remembered_dialog = None
        self.update_dialog = None
        self.file_dialog = None
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
        remembered = QPushButton("Your devices…")
        remembered.clicked.connect(self.open_remembered_devices)
        top.addWidget(remembered)
        files_menu = self.menuBar().addMenu("Device")
        files_menu.addAction("Browse files…", self.open_file_browser)
        updates = self.menuBar().addMenu("Help")
        updates.addAction("Check for updates…", self.open_updates)
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

        self.install_button = QPushButton("Install APK…")
        self.install_button.clicked.connect(self.install_apk)
        top.addWidget(self.install_button)
        files_button = QPushButton("Files…")
        files_button.clicked.connect(self.open_file_browser)
        top.addWidget(files_button)

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
        audio_group = QGroupBox("Phone audio")
        audio_layout = QVBoxLayout(audio_group)
        self.audio_capture_source = QComboBox()
        for label, source in AUDIO_SOURCES:
            self.audio_capture_source.addItem(label, source)
        self.audio_capture_source.setCurrentIndex(max(0, self.audio_capture_source.findData(self.settings.value("audio/source", "output"))))
        self.audio_capture_source.currentIndexChanged.connect(self.change_audio_source)
        audio_layout.addWidget(self.audio_capture_source)
        self.audio_source_hint = QLabel()
        self.audio_source_hint.setWordWrap(True)
        self.update_audio_source_hint()
        audio_layout.addWidget(self.audio_source_hint)
        audio_layout.addWidget(self.audio_button)
        self.audio_status = QLabel("Audio stopped")
        self.audio_status.setWordWrap(True)
        audio_layout.addWidget(self.audio_status)
        controls.addWidget(audio_group)
        self.short_video_keys = QCheckBox("↑ / ↓ navigate short videos")
        self.short_video_keys.setChecked(self.settings.value("input/short_video_keys", True, type=bool))
        self.screen.short_video_navigation = self.short_video_keys.isChecked()
        self.short_video_keys.setToolTip("Click the phone preview, then Down for next video or Up for previous. Turn off for normal arrow-key editing.")
        self.short_video_keys.toggled.connect(self.set_short_video_keys)
        controls.addWidget(self.short_video_keys)
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
        phone_rotation_row = QHBoxLayout()
        rotate_phone = QPushButton("Rotate phone ↻")
        rotate_phone.setToolTip("Rotate the actual phone display 90° and lock its orientation. Apps may enforce their own orientation.")
        rotate_phone.clicked.connect(self.rotate_phone)
        auto_rotate = QPushButton("Auto rotate phone")
        auto_rotate.setToolTip("Let the phone's orientation sensor control screen rotation again.")
        auto_rotate.clicked.connect(self.auto_rotate_phone)
        phone_rotation_row.addWidget(rotate_phone)
        phone_rotation_row.addWidget(auto_rotate)
        controls.addLayout(phone_rotation_row)
        preview_help = QLabel("Click the phone to tap or type. Drag or use the scroll wheel. ↑/↓ switch short videos when enabled. Ctrl+V pastes text.")
        preview_help.setWordWrap(True)
        controls.addWidget(preview_help)
        camera_group = QGroupBox("Camera / webcam")
        camera_layout = QVBoxLayout(camera_group)
        self.video_source = QComboBox()
        self.video_source.addItem("Phone screen", "display")
        self.video_source.addItem("Back camera", "back")
        self.video_source.addItem("Front camera", "front")
        self.video_source.currentIndexChanged.connect(self.change_video_source)
        camera_layout.addWidget(self.video_source)
        camera_rotation_row = QHBoxLayout()
        camera_rotation_row.addWidget(QLabel("Camera rotation"))
        self.camera_rotation = QComboBox()
        for degrees in (0, 90, 180, 270):
            self.camera_rotation.addItem(f"{degrees}°", degrees)
        self.camera_rotation.setEnabled(False)
        self.camera_rotation.setToolTip("Rotate the camera preview and virtual webcam together. Saved separately for front and back cameras.")
        self.camera_rotation.currentIndexChanged.connect(self.change_camera_rotation)
        camera_rotation_row.addWidget(self.camera_rotation)
        camera_layout.addLayout(camera_rotation_row)
        self.webcam_device = QLineEdit()
        self.webcam_device.setPlaceholderText("Virtual camera device (automatic if empty)")
        camera_layout.addWidget(self.webcam_device)
        self.webcam_button = QPushButton("Start webcam")
        self.webcam_button.clicked.connect(self.toggle_webcam)
        camera_layout.addWidget(self.webcam_button)
        self.webcam_status = QLabel("Webcam stopped")
        self.webcam_status.setWordWrap(True)
        camera_layout.addWidget(self.webcam_status)
        setup = QPushButton("Webcam setup…")
        setup.clicked.connect(self.webcam_setup)
        camera_layout.addWidget(setup)
        controls.addWidget(camera_group)
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
        sidebar = QScrollArea()
        sidebar.setWidgetResizable(True)
        sidebar.setWidget(right)
        sidebar.setMinimumWidth(320)
        sidebar.setMaximumWidth(400)
        body.addWidget(sidebar)
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
        self.audio_process = QProcess(self)
        self.audio_process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.audio_process.started.connect(self.audio_started)
        self.audio_process.readyReadStandardOutput.connect(self.read_audio_output)
        self.audio_process.finished.connect(self.audio_finished)
        self.audio_process.errorOccurred.connect(self.audio_failed)
        self.audio_output = ""
        self.audio_stop_requested = False
        self.active_audio_source = "output"
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
        self.device_library.remember(records)
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
        if self.remembered_dialog and self.remembered_dialog.isVisible():
            self.remembered_dialog.refresh()

    def open_file_browser(self):
        if not self.serial:
            self.status.setText("Connect a phone first to browse its files.")
            return
        if self.file_dialog and self.file_dialog.serial != self.serial:
            if self.file_dialog.busy():
                self.status.setText("Finish or cancel the file transfer from the previous phone first.")
                self.file_dialog.raise_()
                return
            self.file_dialog.close()
            self.file_dialog.deleteLater()
            self.file_dialog = None
        if self.file_dialog is None:
            self.file_dialog = FileBrowserDialog(self.serial, self)
        self.file_dialog.show()
        self.file_dialog.raise_()
        self.file_dialog.activateWindow()

    def open_updates(self):
        if self.update_dialog is None:
            self.update_dialog = UpdateDialog(self)
        self.update_dialog.show()
        self.update_dialog.raise_()
        self.update_dialog.activateWindow()

    def open_remembered_devices(self):
        if self.remembered_dialog is None:
            self.remembered_dialog = RememberedDevicesDialog(self)
        self.remembered_dialog.refresh()
        self.remembered_dialog.show()
        self.remembered_dialog.raise_()
        self.remembered_dialog.activateWindow()

    def connect_remembered_device(self, entry):
        online = [r for r in getattr(self, "device_records", {}).values()
                  if self.device_library.matches(entry, r)]
        self.disconnect_phone()
        self.settings.setValue("device/identity", entry.get("identity", ""))
        self.settings.setValue("device/serial", entry.get("serial", ""))
        endpoint = entry.get("wifi", "")
        host, separator, port = endpoint.rpartition(":")
        self.settings.setValue("wifi/address", host if separator else "")
        self.settings.setValue("wifi/port", int(port) if separator and port.isdigit() else 0)
        self.settings.sync()
        self.connection_paused = False
        self.devices.setEnabled(True)
        self.disconnect_button.setEnabled(True)
        if online:
            target = min(online, key=lambda r: not r[3])[0]
            self.devices.setCurrentIndex(self.devices.findData(target))
            self.select_device()
        else:
            self.reconnect_phone()
            if not endpoint:
                self.status.setText(f"Connect {entry['name']} by USB, or save its Wi-Fi address in Your devices.")

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
        self.stop_audio()
        self.stop_webcam()
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
            if record and record[3]:
                saved = next((entry for entry in self.device_library.entries
                              if self.device_library.matches(entry, record)), None)
                endpoint = saved.get("wifi", "") if saved else ""
                host, separator, port = endpoint.rpartition(":")
                self.settings.setValue("wifi/address", host if separator else "")
                self.settings.setValue("wifi/port", int(port) if separator and port.isdigit() else 0)
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
        self.stop_webcam()
        if self.frame_worker:
            self.frame_worker.stop()
            if not self.frame_worker.wait(25000):
                self.status.setText("Previous video stream is still stopping")
                return
        self.latest_frame = None
        self.screen.clear()
        self.screen._image_size = None
        self.screen._source_pixmap = None
        self.frame_worker = VideoWorker(self.serial, self.video_source.currentData())
        # Poll the newest frame instead of queueing every decoded image.
        if not hasattr(self, "video_timer"):
            self.video_timer = QTimer(self)
            self.video_timer.timeout.connect(self.present_video_frame)
            self.video_timer.start(16)
        self.frame_worker.capture_error.connect(self.video_failed)
        self.frame_worker.start()

    def set_short_video_keys(self, enabled):
        self.screen.stop_scrolling()
        self.screen.short_video_navigation = enabled and self.video_source.currentData() == "display"
        self.settings.setValue("input/short_video_keys", enabled)
        self.settings.sync()

    def change_video_source(self):
        self.screen.short_video_navigation = self.short_video_keys.isChecked() and self.video_source.currentData() == "display"
        self.stop_webcam()
        if self.record_process and self.record_process.poll() is None:
            self.stop_recording()
        self.screen.stop_scrolling()
        source = self.video_source.currentData()
        camera = source != "display"
        rotation = int(self.settings.value(f"camera/{source}_rotation" if camera else "preview/rotation", 0)) % 360
        self.camera_rotation.blockSignals(True)
        self.camera_rotation.setCurrentIndex(max(0, self.camera_rotation.findData(rotation)))
        self.camera_rotation.blockSignals(False)
        self.camera_rotation.setEnabled(camera)
        self.screen.set_rotation(rotation)
        self.rotation_label.setText(f"{rotation}°")
        if self.serial:
            self.start_video_preview()

    def video_failed(self, message):
        if self.sender() is not self.frame_worker:
            return
        self.frame_worker.latest_video_frame = None
        self.stop_webcam()
        self.status.setText(message)

    def webcam_setup(self):
        QMessageBox.information(self, "Webcam setup",
            "Camera capture requires Android 12 or newer. Select Front camera or Back camera, "
            "then Start webcam. Choose the virtual camera in your calling app. Audio uses "
            "your computer microphone.\n\n"
            "Windows: install OBS Studio to register OBS Virtual Camera. Leave OBS's own "
            "virtual camera stopped while this app supplies it.\n\n"
            "Linux: install v4l2loopback for your kernel, then load it:\n"
            "sudo modprobe v4l2loopback devices=1 exclusive_caps=1 card_label=PhoneCamera\n"
            "Enter its /dev/videoN path above if automatic selection fails.\n\n"
            "Source/Linux installs also need pyvirtualcam and numpy in the app's Python "
            "environment (pip install pyvirtualcam numpy). See README for package setup.")

    def toggle_webcam(self):
        if self.webcam_worker:
            self.stop_webcam()
            return
        if self.video_source.currentData() == "display":
            self.video_source.setCurrentIndex(1)
        if not self.frame_worker or not self.frame_worker.isRunning():
            self.webcam_status.setText("Connect a phone and start its camera first.")
            return
        worker = WebcamWorker(self.frame_worker, self.webcam_device.text().strip())
        worker.rotation = self.screen.rotation
        self.webcam_worker = worker
        worker.output_ready.connect(self.webcam_status.setText)
        worker.output_error.connect(self.webcam_failed)
        self.webcam_button.setText("Stop webcam")
        self.webcam_status.setText("Starting virtual camera…")
        self.webcam_device.setEnabled(False)
        worker.start()

    def webcam_failed(self, message):
        if self.sender() is not self.webcam_worker:
            return
        self.stop_webcam()
        self.webcam_status.setText(f"Webcam unavailable: {message}. Open Webcam setup for instructions.")

    def stop_webcam(self):
        if self.webcam_worker:
            self.webcam_worker.stop()
            self.webcam_worker.wait()
            self.webcam_worker = None
        self.webcam_button.setText("Start webcam")
        self.webcam_device.setEnabled(True)
        self.webcam_status.setText("Webcam stopped")

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
        # Keep the current phone's endpoint until the new pairing succeeds.
        endpoint = f"{host}:{dialog.port.value()}"
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
                self.device_library.remember_endpoint(endpoint)
                if self.remembered_dialog:
                    self.remembered_dialog.refresh()
            result = adb(["connect", endpoint], timeout=30).decode(errors="replace").strip()
            self.status.setText(result or "ADB connect completed.")
            if "connected to" not in result.lower() and "already connected" not in result.lower():
                QMessageBox.warning(self, "Connection failed", result or "ADB did not confirm the connection.")
                return
            self.device_library.remember_endpoint(endpoint)
            try:
                identity = adb(["shell", "getprop", "ro.serialno"], serial=endpoint, timeout=3).decode().strip()
            except Exception:
                identity = ""
            if identity.lower() in ("unknown", "null", "none"):
                identity = ""
            self.device_library.remember([(endpoint, "", identity, False)], force=True)
            self.disconnect_phone()
            self.settings.setValue("wifi/address", host)
            self.settings.setValue("wifi/port", dialog.port.value())
            self.settings.setValue("wifi/pair_port", dialog.pair_port.value())
            self.settings.setValue("device/identity", identity)
            self.settings.setValue("device/serial", f"{host}:{dialog.port.value()}")
            self.settings.sync()
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
            self.status.setText(f"Live {self.video_source.currentText()} · {image.width()} × {image.height()} · rotation {self.screen.rotation}°")

    def rotate_phone(self):
        if not self.serial:
            self.status.setText("Connect a phone first.")
            return
        try:
            # Read the real display orientation when auto rotation is enabled;
            # user_rotation alone may contain an old locked orientation.
            output = adb(["shell", "dumpsys", "input"], serial=self.serial, timeout=3).decode(errors="replace")
            match = re.search(r"SurfaceOrientation:\s*([0-3])\b", output)
            if match:
                current = int(match.group(1))
            else:
                value = adb(["shell", "settings", "get", "system", "user_rotation"], serial=self.serial).decode().strip()
                current = int(value) if value in ("0", "1", "2", "3") else 0
            rotation = (current + 1) % 4
            # WindowManager rotates and locks atomically, without changing the
            # preview transform or rotating an already rotated feed twice.
            result = adb(["shell", "wm", "user-rotation", "lock", str(rotation)], serial=self.serial).decode(errors="replace").strip()
            if result:
                raise RuntimeError(result)
            self.status.setText(f"Phone rotation locked to {rotation * 90}°. Use Auto rotate phone to restore sensor rotation.")
        except Exception as exc:
            self.report_error(exc)

    def auto_rotate_phone(self):
        result = self.run_device(["shell", "wm", "user-rotation", "free"])
        if result is not None:
            message = result.decode(errors="replace").strip()
            self.status.setText(message or "Phone auto rotation enabled.")

    def change_camera_rotation(self):
        if self.video_source.currentData() != "display":
            self.apply_preview_rotation(self.camera_rotation.currentData())

    def rotate_preview(self, delta: int):
        self.apply_preview_rotation((self.screen.rotation + delta) % 360)

    def apply_preview_rotation(self, rotation: int):
        rotation %= 360
        self.screen.stop_scrolling()
        self.screen.set_rotation(rotation)
        if self.webcam_worker:
            self.webcam_worker.rotation = rotation
        self.rotation_label.setText(f"{rotation}°")
        source = self.video_source.currentData()
        camera = source != "display"
        self.settings.setValue(f"camera/{source}_rotation" if camera else "preview/rotation", rotation)
        if camera:
            self.camera_rotation.blockSignals(True)
            self.camera_rotation.setCurrentIndex(self.camera_rotation.findData(rotation))
            self.camera_rotation.blockSignals(False)
        self.settings.sync()

    def save_screenshot(self):
        image = self.latest_frame
        if (image is None or image.isNull()) and self.video_source.currentData() != "display":
            self.status.setText("Wait for a camera frame before taking a snapshot.")
            return
        if (image is None or image.isNull()) and self.serial:
            try:
                image = QImage.fromData(adb(["exec-out", "screencap", "-p"], serial=self.serial, timeout=8), "PNG")
            except Exception as exc:
                QMessageBox.warning(self, "Capture failed", str(exc))
                return
        if image is None or image.isNull():
            QMessageBox.information(self, "No screen image", "Connect to an authorized device first.")
            return
        if self.video_source.currentData() != "display":
            image = image.transformed(QTransform().rotate(self.screen.rotation))
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

    def update_audio_source_hint(self):
        source = self.audio_capture_source.currentData()
        hint = CALL_AUDIO_NOTE if source.startswith("voice-call") else (
            "Some apps block playback capture." if source == "playback" else
            "Captures the phone microphone." if source == "mic" else
            "Phone output plays here; the phone speaker may be muted.")
        self.audio_source_hint.setText(hint)

    def change_audio_source(self):
        running = self.audio_process.state() != QProcess.ProcessState.NotRunning
        if running:
            self.stop_audio()
        self.settings.setValue("audio/source", self.audio_capture_source.currentData())
        self.settings.sync()
        self.update_audio_source_hint()
        if running:
            self.open_audio()

    def open_audio(self):
        if self.audio_process.state() != QProcess.ProcessState.NotRunning:
            self.stop_audio()
            return
        if not self.serial:
            self.audio_status.setText("Connect and select a device first.")
            return
        executable = shutil.which("scrcpy")
        if not executable:
            self.audio_status.setText("Install scrcpy to play phone audio.")
            return
        self.audio_output = ""
        self.audio_stop_requested = False
        self.active_audio_source = self.audio_capture_source.currentData()
        self.audio_status.setText(f"Starting {self.audio_capture_source.currentText()}…")
        self.audio_button.setText("Stop phone audio")
        # QProcess owns the audio-only session; no SDL or console window.
        self.audio_process.start(executable, ["--serial", self.serial, "--no-video",
            "--no-window", "--no-control", "--require-audio", f"--audio-source={self.active_audio_source}"])

    def audio_started(self):
        self.audio_status.setText(f"Audio session running: {self.audio_capture_source.currentText()}")

    def read_audio_output(self):
        text = bytes(self.audio_process.readAllStandardOutput()).decode(errors="replace")
        self.audio_output = (self.audio_output + text)[-6000:]
        if "ERROR:" in text:
            self.audio_status.setText(next((line for line in reversed(text.splitlines()) if "ERROR:" in line), "Audio error"))

    def audio_finished(self, code, _status):
        self.read_audio_output()
        self.audio_button.setText("Play phone audio")
        if code != 0 and not self.audio_stop_requested:
            errors = [line for line in self.audio_output.splitlines() if "ERROR:" in line or "SecurityException" in line]
            detail = errors[-1] if errors else (self.audio_output.strip().splitlines()[-1] if self.audio_output.strip() else f"Exit code {code}")
            prefix = "Call capture failed or was rejected by the phone: " if self.active_audio_source.startswith("voice-call") else "Audio stopped: "
            self.audio_status.setText(prefix + detail)
        else:
            self.audio_status.setText("Audio stopped")

    def audio_failed(self, _error):
        self.audio_button.setText("Play phone audio")
        self.audio_status.setText("Audio unavailable: " + self.audio_process.errorString())

    def stop_audio(self):
        self.audio_stop_requested = True
        if self.audio_process.state() != QProcess.ProcessState.NotRunning:
            self.audio_process.terminate()
            if not self.audio_process.waitForFinished(1500):
                self.audio_process.kill()
                self.audio_process.waitForFinished(1000)
        self.audio_button.setText("Play phone audio")
        self.audio_status.setText("Audio stopped")

    def open_recording_settings(self):
        RecordingSettingsDialog(self.settings, self).exec()

    def start_recording(self):
        if self.video_source.currentData() != "display":
            self.status.setText("Switch to Phone screen to record. Camera snapshots are available with Screenshot.")
            return
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
        if self.file_dialog and self.file_dialog.busy():
            self.status.setText("Finish or cancel the file transfer before closing.")
            self.file_dialog.raise_()
            event.ignore()
            return
        if self.update_dialog:
            worker = self.update_dialog.worker
            installer = self.update_dialog.installer
            if (worker and worker.isRunning()) or (installer and installer.state().value != 0):
                self.status.setText("Wait for the update operation to finish before closing.")
                event.ignore()
                return
        self.screen.stop_scrolling()
        self.stop_audio()
        self.stop_webcam()
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
