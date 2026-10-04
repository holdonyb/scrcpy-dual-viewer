#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
双设备 scrcpy 内嵌镜像工具（Qt 版）

- 左右分栏同时镜像两台 Android 设备（手机 + AR 眼镜等）
- scrcpy 窗口直接嵌入程序内部，不弹独立窗口
- 依赖：adb、scrcpy（支持内置便携版）
"""

import ctypes
import shutil
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, QTimer, Signal, QObject
from PySide6.QtGui import QAction, QColor, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

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
        return f"{label}  [{self.serial}]"


class ScrcpyFinder:
    @staticmethod
    def find() -> Optional[str]:
        exe = shutil.which("scrcpy") or shutil.which("scrcpy.exe")
        if exe:
            return exe

        script_dir = Path(__file__).resolve().parent
        bundled = sorted((script_dir / "scrcpy").glob("scrcpy-win*/scrcpy.exe"))
        if not bundled:
            bundled = sorted((script_dir / "_internal" / "scrcpy").glob("scrcpy-win*/scrcpy.exe"))

        home = Path.home()
        candidates = list(bundled) + [
            home / "scoop" / "shims" / "scrcpy.exe",
            Path("C:/Program Files/scrcpy/scrcpy.exe"),
            Path("C:/ProgramData/scoop/apps/scrcpy/current/scrcpy.exe"),
            Path("C:/tools/scrcpy/scrcpy.exe"),
        ]
        for p in candidates:
            if p.exists():
                return str(p)
        return None


class AdbHelper:
    def __init__(self):
        self.adb_path = shutil.which("adb") or shutil.which("adb.exe")

    def list_devices(self) -> List[AdbDevice]:
        if not self.adb_path:
            return []
        result = subprocess.run(
            [self.adb_path, "devices", "-l"],
            capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace",
        )
        devices = []
        for line in result.stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 2:
                continue
            serial, status = parts[0], parts[1]
            if status != "device":
                continue
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
        x11 = ctypes.cdll.LoadLibrary("libX11.so.6")
    except OSError:
        return 0

    c_ulong_p = ctypes.POINTER(ctypes.c_ulong)
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XFetchName.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                               ctypes.POINTER(ctypes.c_char_p)]
    x11.XQueryTree.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                               ctypes.POINTER(ctypes.c_ulong),
                               ctypes.POINTER(ctypes.c_ulong),
                               ctypes.POINTER(c_ulong_p),
                               ctypes.POINTER(ctypes.c_uint)]

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
    style = user32.GetWindowLongPtrW(hwnd, _GWL_STYLE)
    style &= ~(_WS_CAPTION | _WS_THICKFRAME)
    user32.SetWindowLongPtrW(hwnd, _GWL_STYLE, style)
    user32.SetWindowPos(hwnd, None, 0, 0, 0, 0,
                        _SWP_FRAMECHANGED | _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOZORDER)


# ---------------------------------------------------------------------------
# scrcpy 会话：一个嵌入槽位的状态机
# ---------------------------------------------------------------------------


class ScrcpySession(QObject):
    """管理一次 scrcpy 进程：启动 -> 找窗口 -> 嵌入 -> 退出检测。"""

    embedded = Signal(int)          # hwnd
    failed = Signal(str)            # 错误信息
    exited = Signal(int)            # return code

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
        self._stderr_tail = ""

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, args: List[str], title_prefix: str, timeout_s: float = 20.0):
        self.stop()
        self.title_prefix = title_prefix
        self.hwnd = 0
        self._deadline = time.monotonic() + timeout_s
        kwargs = {}
        if _IS_WIN:
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            self.proc = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                **kwargs,
            )
        except Exception as e:
            self.proc = None
            self.failed.emit(f"启动 scrcpy 失败：{e}")
            return
        self._timer.start()

    def _poll(self):
        if self.proc is None:
            self._timer.stop()
            return

        code = self.proc.poll()
        if code is not None:
            self._timer.stop()
            try:
                err = (self.proc.stderr.read() or b"").decode("utf-8", errors="replace")
            except Exception:
                err = ""
            self.proc = None
            if self.hwnd == 0:
                self.failed.emit(err.strip()[-600:] or f"scrcpy 退出（代码 {code}）")
            else:
                self.exited.emit(code)
            return

        if self.hwnd == 0:
            hwnd = find_window_by_title_prefix(self.title_prefix)
            if hwnd:
                self.hwnd = hwnd
                self.embedded.emit(hwnd)
                # 窗口已嵌入：后台持续读 stderr 防止管道缓冲区塞满
                threading.Thread(target=self._drain_stderr, daemon=True).start()
                # 继续用较慢频率监视进程
                self._timer.setInterval(1000)
            elif time.monotonic() > self._deadline:
                self._timer.stop()
                self.stop()
                self.failed.emit("等待 scrcpy 窗口超时（20 秒），设备可能未连接或未授权。")

    def _drain_stderr(self):
        proc = self.proc
        if proc is None or proc.stderr is None:
            return
        try:
            while proc.poll() is None:
                line = proc.stderr.readline()
                if not line:
                    break
        except Exception:
            pass

    def stop(self):
        self._timer.stop()
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=3)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        self.proc = None
        self.hwnd = 0


# ---------------------------------------------------------------------------
# 设置弹窗
# ---------------------------------------------------------------------------


class SettingsDialog(QDialog):
    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("镜像参数")
        self.cfg = cfg

        form = QFormLayout(self)
        self.max_size = QLineEdit(str(cfg.get("max_size", "1280")))
        self.bitrate = QLineEdit(str(cfg.get("bitrate", "8")))
        self.fps = QLineEdit(str(cfg.get("fps", "0")))
        self.turn_off = QCheckBox("连接后关闭设备屏幕（画面不受影响）")
        self.turn_off.setChecked(bool(cfg.get("turn_off", False)))
        self.record = QCheckBox("同时录屏")
        self.record.setChecked(bool(cfg.get("record", False)))
        self.record_dir = QLineEdit(cfg.get("record_dir", str(Path.home() / "Videos")))
        browse = QPushButton("…")
        browse.setFixedWidth(32)
        browse.clicked.connect(self._browse)

        form.addRow("最大尺寸 px（0=原尺寸）", self.max_size)
        form.addRow("视频码率 Mbps（0=默认）", self.bitrate)
        form.addRow("最大帧率（0=不限制）", self.fps)
        form.addRow(self.turn_off)
        form.addRow(self.record)
        row = QHBoxLayout()
        row.addWidget(self.record_dir)
        row.addWidget(browse)
        form.addRow("录屏目录", row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "选择录屏目录", self.record_dir.text())
        if d:
            self.record_dir.setText(d)

    def apply(self):
        self.cfg.update({
            "max_size": self.max_size.text().strip() or "0",
            "bitrate": self.bitrate.text().strip() or "0",
            "fps": self.fps.text().strip() or "0",
            "turn_off": self.turn_off.isChecked(),
            "record": self.record.isChecked(),
            "record_dir": self.record_dir.text().strip(),
        })


# ---------------------------------------------------------------------------
# 单个设备面板
# ---------------------------------------------------------------------------


class DevicePanel(QFrame):
    log = Signal(str)

    def __init__(self, slot: str, cfg: dict, scrcpy_path_getter, parent=None):
        super().__init__(parent)
        self.slot = slot
        self.cfg = cfg
        self._get_scrcpy = scrcpy_path_getter
        self.session = ScrcpySession(slot, self)
        self._container: Optional[QWidget] = None

        self.setObjectName("devicePanel")
        self.setFrameShape(QFrame.StyledPanel)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        # 顶行：标题 + 设备选择 + 启停
        top = QHBoxLayout()
        self.title = QLabel(f"设备 {slot}")
        self.title.setObjectName("panelTitle")
        self.combo = QComboBox()
        self.combo.setMinimumWidth(200)
        self.combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.btn_toggle = QPushButton("启动")
        self.btn_toggle.setFixedWidth(70)
        self.btn_toggle.clicked.connect(self.toggle)
        top.addWidget(self.title)
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
        outer.addWidget(self.status)

        self.session.embedded.connect(self._on_embedded)
        self.session.failed.connect(self._on_failed)
        self.session.exited.connect(self._on_exited)

    # -- 设备列表 -----------------------------------------------------------

    def set_devices(self, devices: List[AdbDevice]):
        current_serial = self.current_serial()
        self.combo.blockSignals(True)
        self.combo.clear()
        for d in devices:
            self.combo.addItem(d.display(), d.serial)
        # 恢复选择
        if current_serial:
            idx = self.combo.findData(current_serial)
            if idx >= 0:
                self.combo.setCurrentIndex(idx)
        self.combo.blockSignals(False)

    def current_serial(self) -> Optional[str]:
        return self.combo.currentData()

    # -- 启停 ---------------------------------------------------------------

    def toggle(self):
        if self.session.running:
            self.stop()
        else:
            self.start()

    def start(self):
        serial = self.current_serial()
        if not serial:
            self.log.emit(f"[{self.slot}] 未选择设备")
            return
        scrcpy = self._get_scrcpy()
        if not scrcpy:
            QMessageBox.critical(self, "错误", "找不到 scrcpy.exe")
            return

        cfg = self.cfg
        title = f"DSVIEW-{self.slot}-{serial}-{int(time.time())}"
        args = [
            scrcpy,
            "--serial", serial,
            "--window-title", title,
            "--window-borderless",
            "--stay-awake",
        ]
        if str(cfg.get("max_size", "0")) not in ("", "0"):
            args += ["--max-size", str(cfg["max_size"])]
        if str(cfg.get("bitrate", "0")) not in ("", "0"):
            args += ["--video-bit-rate", f"{cfg['bitrate']}M"]
        if str(cfg.get("fps", "0")) not in ("", "0"):
            args += ["--max-fps", str(cfg["fps"])]
        if cfg.get("turn_off"):
            args.append("--turn-screen-off")
        if cfg.get("record"):
            out_dir = Path(cfg.get("record_dir") or str(Path.home() / "Videos"))
            out_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            out = out_dir / f"device_{self.slot}_{serial}_{ts}.mp4"
            args += ["--record", str(out)]
            self.log.emit(f"[{self.slot}] 录屏输出：{out}")

        self.log.emit(f"[{self.slot}] 连接 {serial} …")
        self.status.setText("连接中…")
        self.btn_toggle.setEnabled(False)
        self.combo.setEnabled(False)
        self.session.start(args, title)

    def stop(self):
        self.session.stop()
        self._teardown_container()
        self.status.setText("未连接")
        self.btn_toggle.setText("启动")
        self.btn_toggle.setEnabled(True)
        self.combo.setEnabled(True)

    # -- 会话回调 -----------------------------------------------------------

    def _on_embedded(self, hwnd: int):
        from PySide6.QtGui import QWindow
        strip_window_frame(hwnd)
        qwin = QWindow.fromWinId(hwnd)
        self._container = QWidget.createWindowContainer(qwin, self.video_area)
        layout = self.video_area.layout()
        layout.removeWidget(self.placeholder)
        self.placeholder.hide()
        layout.addWidget(self._container)
        self.status.setText(f"已连接  {self.current_serial()}")
        self.btn_toggle.setText("停止")
        self.btn_toggle.setEnabled(True)
        self.log.emit(f"[{self.slot}] 画面已嵌入")

    def _on_failed(self, msg: str):
        self.status.setText("连接失败")
        self.btn_toggle.setEnabled(True)
        self.combo.setEnabled(True)
        self.log.emit(f"[{self.slot}] 失败：{msg}")

    def _on_exited(self, code: int):
        self._teardown_container()
        self.status.setText("已断开")
        self.btn_toggle.setText("启动")
        self.btn_toggle.setEnabled(True)
        self.combo.setEnabled(True)
        self.log.emit(f"[{self.slot}] scrcpy 已退出（{code}）")

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
QStatusBar { background: #17181c; }
QSplitter::handle { background: #35373e; }
"""


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("双设备镜像")
        self.resize(1280, 760)

        self.adb = AdbHelper()
        self.scrcpy_path = ScrcpyFinder.find()
        self.devices: List[AdbDevice] = []
        self.cfg: Dict = {
            "max_size": "1280", "bitrate": "8", "fps": "0",
            "turn_off": False, "record": False,
            "record_dir": str(Path.home() / "Videos"),
        }

        self._build_ui()
        self.refresh_devices()

    def _build_ui(self):
        tb = self.addToolBar("main")
        tb.setMovable(False)

        act_refresh = QAction("刷新设备", self)
        act_refresh.triggered.connect(self.refresh_devices)
        tb.addAction(act_refresh)

        self.act_start_all = QAction("▶ 启动全部", self)
        self.act_start_all.triggered.connect(self.start_all)
        tb.addAction(self.act_start_all)

        act_stop_all = QAction("■ 停止全部", self)
        act_stop_all.triggered.connect(self.stop_all)
        tb.addAction(act_stop_all)

        act_settings = QAction("参数设置", self)
        act_settings.triggered.connect(self.open_settings)
        tb.addAction(act_settings)

        splitter = QSplitter(Qt.Horizontal)
        self.panel_a = DevicePanel("A", self.cfg, lambda: self.scrcpy_path)
        self.panel_b = DevicePanel("B", self.cfg, lambda: self.scrcpy_path)
        splitter.addWidget(self.panel_a)
        splitter.addWidget(self.panel_b)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)

        central = QWidget()
        v = QVBoxLayout(central)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(8)
        v.addWidget(splitter, 1)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(500)
        self.log_view.setFixedHeight(110)
        v.addWidget(self.log_view)

        self.setCentralWidget(central)

        for p in (self.panel_a, self.panel_b):
            p.log.connect(self.append_log)

        self.statusBar().showMessage(self._tool_status())

    def _tool_status(self) -> str:
        adb = self.adb.adb_path or "未找到 adb"
        scrcpy = self.scrcpy_path or "未找到 scrcpy"
        return f"adb: {adb}    |    scrcpy: {scrcpy}"

    # -- 操作 ---------------------------------------------------------------

    def refresh_devices(self):
        try:
            self.devices = self.adb.list_devices()
        except Exception as e:
            QMessageBox.critical(self, "adb 错误", str(e))
            return
        self.panel_a.set_devices(self.devices)
        self.panel_b.set_devices(self.devices)
        if len(self.devices) >= 2:
            self.panel_a.combo.setCurrentIndex(0)
            self.panel_b.combo.setCurrentIndex(1)
        elif len(self.devices) == 1:
            self.panel_a.combo.setCurrentIndex(0)
        self.append_log(f"检测到 {len(self.devices)} 台设备：" +
                        (", ".join(d.display() for d in self.devices) or "无"))

    def start_all(self):
        a, b = self.panel_a.current_serial(), self.panel_b.current_serial()
        if a and b and a == b:
            QMessageBox.warning(self, "设备冲突", "两个面板选择了同一台设备，请分别选择。")
            return
        if a and not self.panel_a.session.running:
            self.panel_a.start()
        if b and not self.panel_b.session.running:
            self.panel_b.start()

    def stop_all(self):
        self.panel_a.stop()
        self.panel_b.stop()

    def open_settings(self):
        dlg = SettingsDialog(self.cfg, self)
        if dlg.exec() == QDialog.Accepted:
            dlg.apply()
            self.append_log("参数已更新（对下次启动生效）")

    def append_log(self, msg: str):
        self.log_view.appendPlainText(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

    def closeEvent(self, event):
        self.panel_a.stop()
        self.panel_b.stop()
        event.accept()


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def main():
    if "--check" in sys.argv:
        print(f"adb:    {AdbHelper().adb_path or 'NOT FOUND'}")
        print(f"scrcpy: {ScrcpyFinder.find() or 'NOT FOUND'}")
        return

    app = QApplication(sys.argv)
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
