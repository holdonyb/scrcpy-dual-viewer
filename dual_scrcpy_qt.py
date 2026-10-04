#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
多屏录制（Qt 版）

- 分栏显示 1～3 台 Android 设备（手机 + AR 眼镜等）
- scrcpy 窗口直接嵌入程序内部，不弹独立窗口
- 独立录制、播放声音和电脑讲解、分别输出与演示合成
- 依赖：adb、scrcpy、FFmpeg / ffprobe（录制导出）
"""

import ctypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, QTimer, Signal, QObject, QSettings
from PySide6.QtGui import QAction, QColor, QPalette, QIntValidator
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStyle,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)
from recording_ui import RecordingPanel
from recording_capture import signal_private_console, find_media_tools
from app_runtime import VERSION, external_environment, initialize_desktop

# ---------------------------------------------------------------------------
# adb / scrcpy 查找
# ---------------------------------------------------------------------------


@dataclass
class AdbDevice:
    serial: str
    status: str
    model: str = ""
    product: str = ""

    def display(self) -> str:
        label = self.model or self.product or self.serial
        suffix = {"unauthorized": "未授权", "offline": "离线", "disconnected": "已断开"}.get(self.status)
        return f"{label}  [{self.serial}]" + (f" · {suffix or self.status}" if self.status != "device" else "")

    def guidance(self) -> str:
        return {
            "device": "就绪",
            "unauthorized": "请在设备上允许 USB 调试",
            "offline": "设备离线，请重新连接",
            "disconnected": "设备已断开，请重新连接",
        }.get(self.status, f"设备不可用：{self.status}")


def resource_dirs() -> List[Path]:
    roots = [Path(__file__).resolve().parent]
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).resolve().parent)
        if getattr(sys, "_MEIPASS", None):
            roots.append(Path(sys._MEIPASS))
    return list(dict.fromkeys(roots))


class ScrcpyFinder:
    @staticmethod
    def find() -> Optional[str]:
        exe = shutil.which("scrcpy") or shutil.which("scrcpy.exe")
        if exe and not getattr(sys, "frozen", False):
            return exe

        bundled = []
        for root in resource_dirs():
            for base in (root, root / "_internal"):
                bundled.extend([base / "scrcpy.exe", base / "scrcpy" / "scrcpy.exe",
                                base / "scrcpy" / "scrcpy", base / "scrcpy"])
                bundled.extend(sorted((base / "scrcpy").glob("scrcpy-*/scrcpy.exe"), reverse=True))
                bundled.extend(sorted((base / "scrcpy").glob("scrcpy-*/scrcpy"), reverse=True))

        home = Path.home()
        candidates = list(bundled) + [
            home / "scoop" / "shims" / "scrcpy.exe",
            Path("C:/Program Files/scrcpy/scrcpy.exe"),
            Path("C:/ProgramData/scoop/apps/scrcpy/current/scrcpy.exe"),
            Path("C:/tools/scrcpy/scrcpy.exe"),
        ]
        for p in candidates:
            if p.is_file():
                return str(p)
        return exe


def find_adb(scrcpy_path: Optional[str] = None) -> Optional[str]:
    # Prefer the matching portable adb over another version on PATH.
    scrcpy_path = scrcpy_path or ScrcpyFinder.find()
    if scrcpy_path:
        for name in ("adb.exe", "adb"):
            candidate = Path(scrcpy_path).resolve().parent / name
            if candidate.is_file():
                return str(candidate)
    exe = shutil.which("adb") or shutil.which("adb.exe")
    if exe:
        return exe
    for root in resource_dirs():
        for candidate in (root / "adb.exe", root / "adb", root / "platform-tools" / "adb.exe",
                          root / "platform-tools" / "adb"):
            if candidate.is_file():
                return str(candidate)
    return None


class AdbHelper:
    def __init__(self, adb_path: Optional[str] = None, scrcpy_path: Optional[str] = None):
        self.adb_path = adb_path or find_adb(scrcpy_path)

    def list_devices(self) -> List[AdbDevice]:
        if not self.adb_path:
            raise RuntimeError("找不到 adb，请在「设置」中选择 adb，或使用包含 adb 的 scrcpy 便携包。")
        kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
        result = subprocess.run(
            [self.adb_path, "devices", "-l"],
            capture_output=True, text=True, timeout=10, env=external_environment(),
            encoding="utf-8", errors="replace",
            **kwargs,
        )
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or f"adb 运行失败（代码 {result.returncode}）")
        devices = []
        for line in result.stdout.splitlines():
            if not line.strip() or line.startswith(("List of devices", "*")):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            serial, status = parts[0], parts[1]
            if status == "no" and len(parts) > 2 and parts[2] == "permissions":
                status = "no permissions"
            info = {}
            for item in parts[2:]:
                if ":" in item:
                    k, v = item.split(":", 1)
                    info[k] = v
            devices.append(AdbDevice(
                serial=serial,
                status=status,
                model=info.get("model", "").replace("_", " "),
                product=info.get("product", ""),
            ))
        return devices


# ---------------------------------------------------------------------------
# 窗口查找（Windows / Linux X11）
# ---------------------------------------------------------------------------

_IS_WIN = sys.platform == "win32"

if _IS_WIN:
    user32 = ctypes.windll.user32

    _GWL_STYLE = -16
    _WS_CAPTION = 0x00C00000
    _WS_THICKFRAME = 0x00040000
    _SWP_FRAMECHANGED = 0x0020
    _SWP_NOMOVE = 0x0002
    _SWP_NOSIZE = 0x0001
    _SWP_NOZORDER = 0x0004

    _WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    _get_style = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
    _get_style.argtypes = [wintypes.HWND, ctypes.c_int]
    _get_style.restype = ctypes.c_ssize_t
    _set_style = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
    _set_style.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    _set_style.restype = ctypes.c_ssize_t
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.PostMessageW.argtypes = [wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL


class _XMessageData(ctypes.Union):
    _fields_ = [("b", ctypes.c_char * 20), ("s", ctypes.c_short * 10), ("l", ctypes.c_long * 5)]


class _XClientMessage(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong), ("send_event", ctypes.c_int),
                ("display", ctypes.c_void_p), ("window", ctypes.c_ulong),
                ("message_type", ctypes.c_ulong), ("format", ctypes.c_int), ("data", _XMessageData)]


class _XEvent(ctypes.Union):
    _fields_ = [("type", ctypes.c_int), ("xclient", _XClientMessage), ("pad", ctypes.c_long * 24)]


def _load_x11():
    x11 = ctypes.cdll.LoadLibrary("libX11.so.6")
    c_ulong_p = ctypes.POINTER(ctypes.c_ulong)
    signatures = {
        "XOpenDisplay": ([ctypes.c_char_p], ctypes.c_void_p),
        "XDefaultRootWindow": ([ctypes.c_void_p], ctypes.c_ulong),
        "XCloseDisplay": ([ctypes.c_void_p], ctypes.c_int),
        "XFree": ([ctypes.c_void_p], ctypes.c_int),
        "XFetchName": ([ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_char_p)], ctypes.c_int),
        "XQueryTree": ([ctypes.c_void_p, ctypes.c_ulong, c_ulong_p, c_ulong_p,
                        ctypes.POINTER(c_ulong_p), ctypes.POINTER(ctypes.c_uint)], ctypes.c_int),
        "XInternAtom": ([ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int], ctypes.c_ulong),
        "XSendEvent": ([ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_long,
                        ctypes.POINTER(_XEvent)], ctypes.c_int),
        "XFlush": ([ctypes.c_void_p], ctypes.c_int),
    }
    for name, (argtypes, restype) in signatures.items():
        function = getattr(x11, name)
        function.argtypes = argtypes
        function.restype = restype
    return x11


def _find_window_win(prefix: str) -> int:
    """枚举顶层窗口，返回标题以 prefix 开头的窗口句柄，找不到返回 0。"""
    found: List[int] = []

    def _cb(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                if buf.value.startswith(prefix):
                    found.append(hwnd)
                    return False  # 停止枚举
        return True

    cb = _WNDENUMPROC(_cb)  # 保持引用，防止 GC
    user32.EnumWindows(cb, 0)
    return found[0] if found else 0


def _find_window_x11(prefix: str) -> int:
    """通过 libX11 遍历窗口树，按标题前缀找窗口。找不到或 X11 不可用返回 0。"""
    try:
        x11 = _load_x11()
    except OSError:
        return 0

    c_ulong_p = ctypes.POINTER(ctypes.c_ulong)
    display = x11.XOpenDisplay(None)
    if not display:
        return 0

    prefix_bytes = prefix.encode("utf-8", errors="replace")
    found: List[int] = []

    def _walk(win: int) -> bool:
        name = ctypes.c_char_p()
        try:
            if x11.XFetchName(display, win, ctypes.byref(name)) and name.value:
                if name.value.startswith(prefix_bytes):
                    found.append(win)
                    return True
        finally:
            if name:
                x11.XFree(name)
        root = ctypes.c_ulong()
        parent = ctypes.c_ulong()
        children = c_ulong_p()
        n = ctypes.c_uint()
        if x11.XQueryTree(display, win, ctypes.byref(root), ctypes.byref(parent),
                          ctypes.byref(children), ctypes.byref(n)):
            try:
                for i in range(n.value):
                    if _walk(children[i]):
                        return True
            finally:
                if children:
                    x11.XFree(children)
        return False

    try:
        _walk(x11.XDefaultRootWindow(display))
    finally:
        x11.XCloseDisplay(display)
    return found[0] if found else 0


def find_window_by_title_prefix(prefix: str) -> int:
    if _IS_WIN:
        return _find_window_win(prefix)
    return _find_window_x11(prefix)


def strip_window_frame(hwnd: int):
    """去掉窗口标题栏和边框。仅 Windows 需要（Linux 嵌入后由容器接管）。"""
    if not _IS_WIN:
        return
    style = _get_style(hwnd, _GWL_STYLE)
    style &= ~(_WS_CAPTION | _WS_THICKFRAME)
    _set_style(hwnd, _GWL_STYLE, style)
    user32.SetWindowPos(hwnd, None, 0, 0, 0, 0,
                        _SWP_FRAMECHANGED | _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOZORDER)


def request_window_close(hwnd: int) -> bool:
    """Send the same close request as the window's close button; never kill here."""
    if not hwnd:
        return False
    if _IS_WIN:
        return bool(user32.IsWindow(hwnd) and user32.PostMessageW(hwnd, 0x0010, 0, 0))  # WM_CLOSE
    try:
        x11 = _load_x11()
        display = x11.XOpenDisplay(None)
        if not display:
            return False
        try:
            event = _XEvent()
            event.xclient.type = 33  # ClientMessage
            event.xclient.display = display
            event.xclient.window = hwnd
            event.xclient.message_type = x11.XInternAtom(display, b"WM_PROTOCOLS", 0)
            event.xclient.format = 32
            event.xclient.data.l[0] = x11.XInternAtom(display, b"WM_DELETE_WINDOW", 0)
            sent = x11.XSendEvent(display, hwnd, 0, 0, ctypes.byref(event))
            x11.XFlush(display)
            return bool(sent)
        finally:
            x11.XCloseDisplay(display)
    except OSError:
        return False


# ---------------------------------------------------------------------------
# scrcpy 会话：一个嵌入槽位的状态机
# ---------------------------------------------------------------------------


class ScrcpySession(QObject):
    """管理一次 scrcpy 进程：启动 -> 找窗口 -> 嵌入 -> 退出检测。"""

    embedded = Signal(object)       # native handle (pointer-sized)
    failed = Signal(str)            # 错误信息
    exited = Signal(int)            # return code
    stopped = Signal(bool)          # whether forced termination was required

    def __init__(self, slot: str, parent=None):
        super().__init__(parent)
        self.slot = slot            # "A" / "B"
        self.proc: Optional[subprocess.Popen] = None
        self.hwnd = 0
        self.title_prefix = ""
        self._deadline = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(300)
        self._timer.timeout.connect(self._poll)
        self._stderr_lines = deque(maxlen=20)
        self._reader = None
        self.stopping = False
        self._stop_deadline = 0.0
        self._close_sent = False
        self._forced = False
        self._pending_failure = ""

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, args: List[str], title_prefix: str, timeout_s: float = 20.0, env=None):
        if self.proc is not None:
            return
        self.title_prefix = title_prefix
        self.hwnd = 0
        self._deadline = time.monotonic() + timeout_s
        self.stopping = False
        self._forced = False
        self._close_sent = False
        self._pending_failure = ""
        self._stderr_lines = deque(maxlen=20)
        kwargs = {}
        if _IS_WIN:
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            self.proc = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                env=external_environment(env),
                **kwargs,
            )
        except Exception as e:
            self.proc = None
            self.failed.emit(f"启动 scrcpy 失败：{e}")
            return
        # Drain from launch, including before a window appears, to avoid a full pipe.
        self._reader = threading.Thread(target=self._drain_stderr,
                                        args=(self.proc, self._stderr_lines), daemon=True)
        self._reader.start()
        self._timer.setInterval(100)
        self._timer.start()

    def _poll(self):
        if self.proc is None:
            self._timer.stop()
            return

        code = self.proc.poll()
        if code is not None:
            self._timer.stop()
            if self._reader is not None:
                self._reader.join(timeout=0.05)
            err = b"".join(self._stderr_lines).decode("utf-8", errors="replace")
            was_stopping, forced, had_window = self.stopping, self._forced, bool(self.hwnd)
            self.proc = None
            self.hwnd = 0
            self.stopping = False
            if self._pending_failure:
                self.failed.emit(self._pending_failure + (" 录屏可能不完整。" if forced else ""))
            elif was_stopping:
                if code and not forced:
                    self.failed.emit(f"镜像未能正常关闭（代码 {code}）。" + (f"\n{err.strip()[-600:]}" if err else ""))
                else:
                    self.stopped.emit(forced)
            elif not had_window:
                self.failed.emit(err.strip()[-600:] or f"scrcpy 退出（代码 {code}）")
            else:
                self.exited.emit(code)
            return

        if self.stopping:
            if not self._close_sent:
                hwnd = self.hwnd or find_window_by_title_prefix(self.title_prefix)
                self._close_sent = request_window_close(hwnd)
            if time.monotonic() > self._stop_deadline and not self._forced:
                self.proc.kill()
                self._forced = True
            return

        if self.hwnd == 0:
            hwnd = find_window_by_title_prefix(self.title_prefix)
            if hwnd:
                self.hwnd = hwnd
                self.embedded.emit(hwnd)
                # 继续用较慢频率监视进程
                self._timer.setInterval(1000)
            elif time.monotonic() > self._deadline:
                self._pending_failure = "等待镜像画面超时，请检查设备连接与 USB 调试授权。"
                self.stop()

    @staticmethod
    def _drain_stderr(proc, lines):
        if proc is None or proc.stderr is None:
            return
        try:
            while True:
                chunk = proc.stderr.read(1024)
                if not chunk:
                    break
                lines.append(chunk)
        except Exception:
            pass
        finally:
            proc.stderr.close()

    def stop(self, timeout_s: float = 5.0):
        if self.proc is None or self.stopping:
            return
        self.stopping = True
        self._stop_deadline = time.monotonic() + timeout_s
        hwnd = self.hwnd or find_window_by_title_prefix(self.title_prefix)
        self._close_sent = request_window_close(hwnd)
        self._timer.setInterval(100)
        self._timer.start()


# ---------------------------------------------------------------------------
# 设置弹窗
# ---------------------------------------------------------------------------


def default_config() -> dict:
    return {"max_size": "1280", "bitrate": "8", "fps": "0", "turn_off": False,
            "record": False, "record_dir": str(Path.home() / "Videos"),
            "adb_path": "", "scrcpy_path": "", "ffmpeg_path": "", "device_count": "2",
            "output_mode": "both", "output_layout": "presentation", "video_title": ""}


def validated_config(cfg: dict) -> dict:
    updated = dict(cfg)
    for key, label, maximum in (("max_size", "最大尺寸", 32768),
                                ("bitrate", "视频码率", 1000), ("fps", "最大帧率", 1000)):
        value = str(cfg.get(key, "0")).strip() or "0"
        if not re.fullmatch(r"[0-9]+", value) or int(value) > maximum:
            raise ValueError(f"{label}请输入 0 到 {maximum} 的整数。")
        updated[key] = str(int(value))
    for key, label in (("adb_path", "adb"), ("scrcpy_path", "scrcpy"), ("ffmpeg_path", "FFmpeg")):
        value = str(cfg.get(key, "")).strip()
        if value and not Path(value).expanduser().is_file():
            raise ValueError(f"找不到指定的 {label} 文件，请重新选择。")
        updated[key] = str(Path(value).expanduser().resolve()) if value else ""
    return updated


def load_config(settings: QSettings) -> dict:
    cfg = {key: settings.value(key, value, type=bool if isinstance(value, bool) else str)
           for key, value in default_config().items()}
    # A removed tool should fall back to automatic discovery rather than prevent launch.
    for key in ("adb_path", "scrcpy_path", "ffmpeg_path"):
        if cfg[key] and not Path(cfg[key]).is_file():
            cfg[key] = ""
    try:
        cfg["record"] = False  # Migrate legacy automatic recording to independent controls.
        if cfg["video_title"] == "hicool 多设备演示":
            cfg["video_title"] = ""
        if cfg["device_count"] not in ("1", "2", "3"):
            cfg["device_count"] = "2"
        return validated_config(cfg)
    except ValueError:
        return default_config()


def safe_filename(value: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")[:80] or "device"


class SettingsDialog(QDialog):
    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("录制与镜像设置")
        self.cfg = cfg

        form = QFormLayout(self)
        self.max_size = QLineEdit(str(cfg.get("max_size", "1280")))
        self.bitrate = QLineEdit(str(cfg.get("bitrate", "8")))
        self.fps = QLineEdit(str(cfg.get("fps", "0")))
        for field, maximum in ((self.max_size, 32768), (self.bitrate, 1000), (self.fps, 1000)):
            field.setValidator(QIntValidator(0, maximum, field))
        self.turn_off = QCheckBox("连接后关闭设备屏幕（画面不受影响）")
        self.turn_off.setChecked(bool(cfg.get("turn_off", False)))
        self.record_dir = QLineEdit(cfg.get("record_dir", str(Path.home() / "Videos")))
        browse = QPushButton("…")
        browse.setFixedWidth(32)
        browse.clicked.connect(self._browse)

        form.addRow("最大尺寸 px（0=原尺寸）", self.max_size)
        form.addRow("视频码率 Mbps（0=默认）", self.bitrate)
        form.addRow("最大帧率（0=不限制）", self.fps)
        form.addRow(self.turn_off)
        row = QHBoxLayout()
        row.addWidget(self.record_dir)
        row.addWidget(browse)
        form.addRow("录屏目录", row)

        self.adb_path = QLineEdit(cfg.get("adb_path", ""))
        self.scrcpy_path = QLineEdit(cfg.get("scrcpy_path", ""))
        self.ffmpeg_path = QLineEdit(cfg.get("ffmpeg_path", ""))
        for label, field in (("adb 路径", self.adb_path), ("scrcpy 路径", self.scrcpy_path),
                             ("FFmpeg 路径", self.ffmpeg_path)):
            field.setPlaceholderText("自动查找；也可手动选择")
            button = QPushButton("选择")
            button.clicked.connect(lambda _checked=False, target=field: self._browse_tool(target))
            tool_row = QHBoxLayout()
            tool_row.addWidget(field)
            tool_row.addWidget(button)
            form.addRow(label, tool_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "选择录屏目录", self.record_dir.text())
        if d:
            self.record_dir.setText(d)

    def apply(self):
        updated = validated_config({
            "max_size": self.max_size.text().strip() or "0",
            "bitrate": self.bitrate.text().strip() or "0",
            "fps": self.fps.text().strip() or "0",
            "turn_off": self.turn_off.isChecked(),
            "record": False,
            "record_dir": self.record_dir.text().strip(),
            "adb_path": self.adb_path.text().strip(),
            "scrcpy_path": self.scrcpy_path.text().strip(),
            "ffmpeg_path": self.ffmpeg_path.text().strip(),
        })
        self.cfg.update(updated)

    def accept(self):
        try:
            self.apply()
        except ValueError as error:
            QMessageBox.warning(self, "请检查参数", str(error))
            return
        super().accept()

    def _browse_tool(self, field):
        path, _ = QFileDialog.getOpenFileName(self, "选择工具文件", field.text(), "所有文件 (*)")
        if path:
            field.setText(path)


# ---------------------------------------------------------------------------
# 单个设备面板
# ---------------------------------------------------------------------------


class DevicePanel(QFrame):
    log = Signal(str)

    def __init__(self, slot: str, cfg: dict, scrcpy_path_getter, parent=None,
                 adb_path_getter=None, can_start=None):
        super().__init__(parent)
        self.slot = slot
        self.cfg = cfg
        self._get_scrcpy = scrcpy_path_getter
        self._get_adb = adb_path_getter or (lambda: None)
        self._can_start = can_start or (lambda serial: True)
        self.session = ScrcpySession(slot, self)
        self._container: Optional[QWidget] = None
        self._devices = {}
        self._active_serial = None
        self._record_path = None

        self.setObjectName("devicePanel")
        self.setFrameShape(QFrame.StyledPanel)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        # 顶行：标题 + 设备选择 + 启停
        top = QHBoxLayout()
        self.title = QLabel(f"设备 {slot}")
        self.title.setObjectName("panelTitle")
        self.name = QLineEdit({"A": "手机", "B": "眼镜", "C": "设备 3"}.get(slot, slot))
        self.name.setMaxLength(16)
        self.name.setFixedWidth(85)
        self.name.setToolTip("录制视频中的设备名称")
        self.combo = QComboBox()
        self.combo.setMinimumWidth(120)
        self.combo.setMinimumContentsLength(12)
        self.combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.btn_toggle = QPushButton("启动")
        self.btn_toggle.setFixedWidth(70)
        self.btn_toggle.clicked.connect(self.toggle)
        top.addWidget(self.name)
        top.addSpacing(8)
        top.addWidget(self.combo, 1)
        top.addWidget(self.btn_toggle)
        outer.addLayout(top)

        # 画面区
        self.video_area = QFrame()
        self.video_area.setObjectName("videoArea")
        self.video_area.setMinimumSize(200, 240)
        va_layout = QVBoxLayout(self.video_area)
        va_layout.setContentsMargins(0, 0, 0, 0)
        self.placeholder = QLabel("选择设备后点击「启动」")
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.placeholder.setObjectName("placeholder")
        va_layout.addWidget(self.placeholder)
        outer.addWidget(self.video_area, 1)

        # 状态行
        self.status = QLabel("未连接")
        self.status.setObjectName("statusLabel")
        status_row = QHBoxLayout()
        status_row.addWidget(self.status, 1)
        self.device_audio = QCheckBox("设备声音")
        self.device_audio.setChecked(True)
        self.device_audio.setToolTip("录手机 / 眼镜播放的声音。部分应用或固件可能不允许采集。")
        status_row.addWidget(self.device_audio)
        outer.addLayout(status_row)

        self.session.embedded.connect(self._on_embedded)
        self.session.failed.connect(self._on_failed)
        self.session.exited.connect(self._on_exited)
        self.session.stopped.connect(self._on_stopped)
        self.combo.currentIndexChanged.connect(self._update_selection_state)
        self.set_devices([])

    # -- 设备列表 -----------------------------------------------------------

    def set_devices(self, devices: List[AdbDevice]):
        current_serial = self._active_serial or self.current_serial()
        self._devices = {device.serial: device for device in devices}
        if self._active_serial and self._active_serial not in self._devices:
            self._devices[self._active_serial] = AdbDevice(self._active_serial, "disconnected")
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItem("请选择设备", None)
        for d in self._devices.values():
            self.combo.addItem(d.display(), d.serial)
        # 恢复选择
        if current_serial:
            idx = self.combo.findData(current_serial)
            if idx >= 0:
                self.combo.setCurrentIndex(idx)
        self.combo.blockSignals(False)
        self._update_selection_state()

    def current_serial(self) -> Optional[str]:
        return self.combo.currentData()

    def select_serial(self, serial: str):
        self.combo.setCurrentIndex(self.combo.findData(serial))

    def _update_selection_state(self):
        if self.session.proc is not None:
            return
        device = self._devices.get(self.current_serial())
        self.status.setText(device.guidance() if device else "未选择设备")
        self.btn_toggle.setEnabled(bool(device and device.status == "device"))

    # -- 启停 ---------------------------------------------------------------

    def toggle(self):
        if self.session.proc is not None:
            self.stop()
        else:
            self.start()

    def start(self):
        if self.session.proc is not None:
            return
        serial = self.current_serial()
        if not serial:
            self.log.emit(f"[{self.slot}] 未选择设备")
            return
        device = self._devices.get(serial)
        if not device or device.status != "device":
            self.log.emit(f"[{self.slot}] {device.guidance() if device else '请选择可用设备'}")
            return
        if not self._can_start(serial):
            self.log.emit(f"[{self.slot}] 该设备已在另一面板运行，请选择其他设备。")
            return
        scrcpy = self._get_scrcpy()
        if not scrcpy:
            QMessageBox.critical(self, "找不到 scrcpy", "请在「设置」中选择 scrcpy 文件。")
            return

        try:
            cfg = validated_config(self.cfg)
        except ValueError as error:
            self.log.emit(f"[{self.slot}] {error}")
            return
        title = f"DSVIEW-{self.slot}-{serial}-{time.time_ns()}"
        args = [
            scrcpy,
            "--serial", serial,
            "--window-title", title,
            "--window-borderless",
            "--stay-awake",
            "--no-audio",
        ]
        if str(cfg.get("max_size", "0")) not in ("", "0"):
            args += ["--max-size", str(cfg["max_size"])]
        if str(cfg.get("bitrate", "0")) not in ("", "0"):
            args += ["--video-bit-rate", f"{cfg['bitrate']}M"]
        if str(cfg.get("fps", "0")) not in ("", "0"):
            args += ["--max-fps", str(cfg["fps"])]
        if cfg.get("turn_off"):
            args.append("--turn-screen-off")
        self._record_path = None
        if cfg.get("record"):
            try:
                out_dir = Path(cfg.get("record_dir") or str(Path.home() / "Videos")).expanduser().resolve()
                out_dir.mkdir(parents=True, exist_ok=True)
                ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                self._record_path = out_dir / f"device_{self.slot}_{safe_filename(serial)}_{ts}.mp4"
            except OSError as error:
                self.status.setText("无法使用录屏目录")
                self.log.emit(f"[{self.slot}] 无法创建录屏目录：{error}")
                return
            args += ["--record", str(self._record_path)]
            self.log.emit(f"[{self.slot}] 录屏输出：{self._record_path}")

        self._active_serial = serial
        self.log.emit(f"[{self.slot}] 连接 {serial} …")
        self.status.setText("连接中…")
        self.btn_toggle.setText("停止")
        self.btn_toggle.setEnabled(True)
        self.combo.setEnabled(False)
        env = os.environ.copy()
        if self._get_adb():
            env["ADB"] = self._get_adb()
        self.session.start(args, title, env=env)

    def stop(self):
        if self.session.proc is not None:
            self.status.setText("正在停止，请稍候…")
            self.btn_toggle.setEnabled(False)
            self.combo.setEnabled(False)
            self.session.stop()
            return
        self._reset_panel("未连接")

    def _reset_panel(self, status: str):
        self._active_serial = None
        self._teardown_container()
        self.btn_toggle.setText("启动")
        recorder = getattr(getattr(self.window(), "recording", None), "recorder", None)
        self.combo.setEnabled(not (recorder and recorder.busy))
        self._update_selection_state()
        self.status.setText(status)

    # -- 会话回调 -----------------------------------------------------------

    def _on_embedded(self, hwnd: int):
        from PySide6.QtGui import QWindow
        strip_window_frame(hwnd)
        qwin = QWindow.fromWinId(hwnd)
        if qwin is None:
            self.log.emit(f"[{self.slot}] 无法嵌入画面，请检查桌面窗口系统。")
            self.stop()
            return
        self._container = QWidget.createWindowContainer(qwin, self.video_area)
        layout = self.video_area.layout()
        layout.removeWidget(self.placeholder)
        self.placeholder.hide()
        layout.addWidget(self._container)
        self.status.setText(f"已连接  {self._active_serial or self.current_serial()}")
        self.btn_toggle.setText("停止")
        self.btn_toggle.setEnabled(True)
        self.log.emit(f"[{self.slot}] 画面已嵌入")

    def _on_failed(self, msg: str):
        self._reset_panel("镜像失败")
        self.log.emit(f"[{self.slot}] 失败：{msg}")
        if self._record_path:
            self.log.emit(f"[{self.slot}] 录屏可能不完整，请检查：{self._record_path}")

    def _on_exited(self, code: int):
        self._reset_panel("已断开")
        self.log.emit(f"[{self.slot}] scrcpy 已退出（{code}）")
        if code and self._record_path:
            self.log.emit(f"[{self.slot}] 录屏可能不完整，请检查：{self._record_path}")

    def _on_stopped(self, forced: bool):
        self._reset_panel("已停止")
        if forced:
            self.status.setText("已停止，请检查录屏文件" if self._record_path else "已强制停止")
            self.log.emit(f"[{self.slot}] 正常关闭超时，已强制结束。" +
                          (f"录屏可能不完整：{self._record_path}" if self._record_path else ""))
        else:
            self.log.emit(f"[{self.slot}] 已正常停止" +
                          (f"，录屏文件：{self._record_path}" if self._record_path else ""))

    def _teardown_container(self):
        if self._container is not None:
            layout = self.video_area.layout()
            layout.removeWidget(self._container)
            self._container.setParent(None)
            self._container.deleteLater()
            self._container = None
        if self.placeholder.isHidden():
            layout = self.video_area.layout()
            layout.addWidget(self.placeholder)
            self.placeholder.show()


# ---------------------------------------------------------------------------
# 主窗口
# ---------------------------------------------------------------------------

DARK_QSS = """
QMainWindow, QDialog { background: #1e1f24; }
QWidget { color: #d7dae0; font-size: 13px; }
#devicePanel { background: #26282e; border: 1px solid #35373e; border-radius: 8px; }
#panelTitle { font-weight: 700; font-size: 14px; color: #f0f2f5; }
#videoArea { background: #101114; border: 1px solid #35373e; border-radius: 6px; }
#placeholder { color: #6b6f78; }
#statusLabel { color: #8f949e; }
QComboBox, QLineEdit, QPlainTextEdit {
    background: #17181c; border: 1px solid #3a3c44; border-radius: 4px; padding: 4px 8px;
}
QComboBox QAbstractItemView { background: #26282e; selection-background-color: #3d6fb4; }
QPushButton {
    background: #3a3d45; border: 1px solid #4a4d56; border-radius: 4px; padding: 5px 14px;
}
QPushButton:hover { background: #474a54; }
QPushButton:pressed { background: #2f323a; }
QPushButton:disabled { color: #6b6f78; }
QPushButton#primary { background: #2f6d3a; border-color: #3f8a4c; }
QPushButton#primary:hover { background: #3a8447; }
QToolBar { background: #1e1f24; border: none; spacing: 8px; padding: 6px; }
QMenuBar, QMenu { background: #1e1f24; color: #d7dae0; }
QMenuBar::item:selected, QMenu::item:selected { background: #3a3d45; }
QDockWidget::title { background: #26282e; padding: 5px; }
QStatusBar { background: #17181c; }
QSplitter::handle { background: #35373e; }
"""


class MainWindow(QMainWindow):
    def __init__(self, settings=None):
        super().__init__()
        self.setWindowTitle("多屏录制")
        self.resize(1380, 860)

        self.settings = settings if settings is not None else QSettings(
            QSettings.IniFormat, QSettings.UserScope, "Hicool", "ScrcpyDualViewer")
        self.cfg: Dict = load_config(self.settings)
        self.scrcpy_path = self.cfg["scrcpy_path"] or ScrcpyFinder.find()
        self.adb = AdbHelper(self.cfg["adb_path"] or None, self.scrcpy_path)
        self.devices: List[AdbDevice] = []
        self._closing = False
        self.resource_roots = resource_dirs()
        self.main_script = Path(__file__).resolve()
        self.tray = None

        self._build_ui()
        self._build_tray()
        self.refresh_devices()

    @property
    def active_panels(self):
        return self.panels[:int(self.device_count.currentData() or 2)]

    def validate_settings(self):
        return validated_config(self.cfg)

    def save_settings(self):
        for key, value in self.cfg.items():
            self.settings.setValue(key, value)
        self.settings.sync()
        if self.settings.status() != QSettings.NoError:
            self.append_log("参数已更新，但无法保存，下次打开会使用原参数。")

    def _build_ui(self):
        tb = self.addToolBar("main")
        self._toolbar = tb
        tb.setMovable(False)

        act_refresh = QAction("刷新设备", self)
        act_refresh.triggered.connect(self.refresh_devices)
        tb.addAction(act_refresh)

        self.act_start_all = QAction("▶ 启动镜像", self)
        self.act_start_all.triggered.connect(self.start_all)
        tb.addAction(self.act_start_all)

        act_stop_all = QAction("■ 停止镜像", self)
        act_stop_all.triggered.connect(self.stop_all)
        tb.addAction(act_stop_all)

        act_settings = QAction("设置", self)
        act_settings.triggered.connect(self.open_settings)
        tb.addAction(act_settings)
        tb.addSeparator()
        tb.addWidget(QLabel("设备数量  "))
        self.device_count = QComboBox()
        for count in range(1, 4):
            self.device_count.addItem(f"{count} 台", count)
        self.device_count.setCurrentIndex(int(self.cfg["device_count"]) - 1)
        tb.addWidget(self.device_count)

        splitter = QSplitter(Qt.Horizontal)
        self.panels = []
        for slot in "ABC":
            panel = DevicePanel(slot, self.cfg, lambda: self.scrcpy_path,
                                adb_path_getter=lambda: self.adb.adb_path)
            panel._can_start = lambda serial, p=panel: self._can_start(p, serial)
            self.panels.append(panel)
            splitter.addWidget(panel)
            splitter.setStretchFactor(len(self.panels)-1, 1)
        self.panel_a, self.panel_b, self.panel_c = self.panels
        for panel in self.panels:
            panel.setVisible(panel in self.active_panels)
        self.device_count.currentIndexChanged.connect(self._change_device_count)

        central = QWidget()
        v = QVBoxLayout(central)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(8)
        v.addWidget(splitter, 1)
        self.recording = RecordingPanel(self)
        v.addWidget(self.recording)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(500)
        self.log_view.setMinimumHeight(80)
        self.log_dock = QDockWidget("运行记录", self)
        self.log_dock.setWidget(self.log_view)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.log_dock)
        self.log_dock.hide()

        self.setCentralWidget(central)
        file_menu = self.menuBar().addMenu("文件")
        file_menu.addAction("退出", self.quit_after_recording)
        view_menu = self.menuBar().addMenu("视图")
        view_menu.addAction(self.log_dock.toggleViewAction())
        help_menu = self.menuBar().addMenu("帮助")
        help_menu.addAction("关于多屏录制", lambda: QMessageBox.about(
            self, "关于多屏录制", f"<b>多屏录制</b><br>版本 {VERSION}<br><br>连接 Android 设备，预览与录制屏幕。"
            "<br>支持设备播放声音、电脑讲解，以及分别和合成保存。"))

        for p in self.panels:
            p.log.connect(self.append_log)
            p.session.stopped.connect(self._finish_close)
            p.session.exited.connect(self._finish_close)
            p.session.failed.connect(self._finish_close)

        self.statusBar().showMessage(self._tool_status())

    def _tool_status(self) -> str:
        if not self.adb.adb_path or not self.scrcpy_path:
            return "请在设置中配置设备连接工具"
        ffmpeg, ffprobe = find_media_tools(self.cfg, self.resource_roots)
        if not ffmpeg or not ffprobe:
            return "镜像可用；录制需要在设置中配置 FFmpeg"
        ready = sum(d.status == "device" for d in self.devices)
        return f"{ready} 台设备可用" if ready else "请连接设备并点击刷新"

    # -- 操作 ---------------------------------------------------------------

    def refresh_devices(self):
        if self._closing:
            return
        self.scrcpy_path = self.cfg["scrcpy_path"] or ScrcpyFinder.find()
        self.adb.adb_path = self.cfg["adb_path"] or find_adb(self.scrcpy_path)
        self.statusBar().showMessage(self._tool_status())
        try:
            self.devices = self.adb.list_devices()
        except Exception as e:
            self.append_log(f"无法刷新设备：{e}")
            for panel in self.panels:
                if panel.session.proc is None:
                    panel.status.setText("请检查 adb 设置与连接")
            return
        for panel in self.panels:
            panel.set_devices(self.devices)
        panels = self.active_panels
        assigned = {p.current_serial() for p in panels if p.current_serial()}
        available = [device for device in self.devices if device.status == "device"]
        for panel in panels:
            if not panel.current_serial() and panel.session.proc is None:
                device = next((d for d in available if d.serial not in assigned), None)
                if device:
                    panel.select_serial(device.serial)
                    assigned.add(device.serial)
        self.append_log(f"检测到 {len(self.devices)} 台设备：" +
                        (", ".join(d.display() for d in self.devices) or "无"))
        for device in self.devices:
            if device.status != "device":
                self.append_log(f"{device.display()}：{device.guidance()}")
        self.statusBar().showMessage(self._tool_status())

    def _change_device_count(self):
        if self._closing:
            return
        for panel in self.panels:
            if panel not in self.active_panels:
                panel.stop()
            panel.setVisible(panel in self.active_panels)
        self.cfg["device_count"] = str(self.device_count.currentData())
        self.save_settings()
        self.refresh_devices()

    def _build_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(self.style().standardIcon(QStyle.SP_ComputerIcon), self)
        self.tray.setToolTip("多屏录制")
        menu = QMenu(self)
        menu.addAction("显示窗口", self.show_from_tray)
        menu.addAction("结束录制并退出", self.quit_after_recording)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.show_from_tray()
                                    if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
        self.tray.show()

    def show_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def hide_for_recording(self):
        if self.tray:
            self.hide()
            self.tray.showMessage("录屏继续进行", "双击托盘图标可回到窗口。右键可结束录制并退出。",
                                  QSystemTrayIcon.Information, 3500)
        else:
            self.showMinimized()

    def notify_saved(self, result):
        if self.tray and not self.isVisible():
            self.tray.showMessage("视频已保存", result["folder"], QSystemTrayIcon.Information, 5000)

    def notify_failure(self, message):
        if self.tray and not self.isVisible():
            self.tray.showMessage("请检查录制", message[:250], QSystemTrayIcon.Warning, 6000)

    def quit_after_recording(self):
        self._closing = True
        self._toolbar.setEnabled(False)
        self.recording.recorder.finish()
        self.stop_all()
        self._finish_close()

    def _can_start(self, panel, serial: str) -> bool:
        return not any(p is not panel and p.session.proc is not None and p._active_serial == serial
                       for p in self.panels)

    def start_all(self):
        if self._closing:
            return
        selected = [p.current_serial() for p in self.active_panels if p.current_serial()]
        if len(selected) != len(set(selected)):
            QMessageBox.warning(self, "设备冲突", "多个面板选择了同一台设备，请分别选择。")
            return
        for panel in self.active_panels:
            if panel.current_serial() and not panel.session.running:
                panel.start()

    def stop_all(self):
        for panel in self.panels:
            panel.stop()

    def open_settings(self):
        dlg = SettingsDialog(self.cfg, self)
        if dlg.exec() == QDialog.Accepted:
            self.save_settings()
            self.recording.directory.setText(self.cfg["record_dir"])
            self.append_log("参数已更新（对下次启动生效）")
            self.refresh_devices()

    def append_log(self, msg: str):
        self.log_view.appendPlainText(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
        if any(word in msg for word in ("失败", "无法", "超时", "不完整", "请检查")):
            self.log_dock.show()

    def closeEvent(self, event):
        recorder = self.recording.recorder
        if recorder.busy and not self._closing and self.tray:
            event.ignore()
            self.hide_for_recording()
            return
        if recorder.busy or any(p.session.proc is not None for p in self.panels):
            event.ignore()
            if not self._closing:
                self._closing = True
                self._toolbar.setEnabled(False)
                self.statusBar().showMessage("正在保存视频，请稍候…")
                recorder.finish()
                self.stop_all()
        else:
            if self.tray:
                self.tray.hide()
            event.accept()

    def _finish_close(self, *_args):
        if self._closing and not self.recording.recorder.busy and all(p.session.proc is None for p in self.panels):
            QTimer.singleShot(0, self.close)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def main():
    if "--signal-recording" in sys.argv:
        sys.exit(signal_private_console(int(sys.argv[sys.argv.index("--signal-recording") + 1])))
    if "--self-test" in sys.argv:
        from release_smoke import self_test
        sys.exit(self_test(Path(sys.argv[sys.argv.index("--self-test") + 1])))
    if "--test-window" in sys.argv:
        from release_smoke import window_child
        sys.exit(window_child(sys.argv[sys.argv.index("--test-window") + 1:]))
    if "--check" in sys.argv:
        print(f"adb:    {AdbHelper().adb_path or 'NOT FOUND'}")
        print(f"scrcpy: {ScrcpyFinder.find() or 'NOT FOUND'}")
        return

    app = QApplication(sys.argv)
    app.setApplicationName("多屏录制")
    app.setApplicationVersion(VERSION)
    initialize_desktop(app)
    app.setStyle("Fusion")
    app.setStyleSheet(DARK_QSS)

    pal = QPalette()
    pal.setColor(QPalette.Window, QColor("#1e1f24"))
    pal.setColor(QPalette.WindowText, QColor("#d7dae0"))
    pal.setColor(QPalette.Base, QColor("#17181c"))
    pal.setColor(QPalette.Text, QColor("#d7dae0"))
    pal.setColor(QPalette.Button, QColor("#3a3d45"))
    pal.setColor(QPalette.ButtonText, QColor("#d7dae0"))
    pal.setColor(QPalette.Highlight, QColor("#3d6fb4"))
    app.setPalette(pal)

    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
