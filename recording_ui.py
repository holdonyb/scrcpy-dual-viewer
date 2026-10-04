"""Recording controls for one to three selected Android sources."""
from datetime import datetime
from pathlib import Path
import sys
import threading

from PySide6.QtCore import Signal, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QProgressBar, QPushButton, QVBoxLayout)
from recording_capture import Recorder, find_media_tools, list_microphones


class RecordingPanel(QFrame):
    microphones_found = Signal(object, str)

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.recorder = Recorder(self)
        self.setObjectName("devicePanel")
        self.output_folder = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        row = QHBoxLayout()
        heading = QLabel("独立录屏")
        heading.setObjectName("panelTitle")
        self.btn_start = QPushButton("开始录制")
        self.btn_start.setObjectName("primary")
        self.btn_pause = QPushButton("暂停")
        self.btn_finish = QPushButton("结束并保存")
        self.btn_background = QPushButton("转到后台")
        self.btn_open = QPushButton("打开输出")
        self.status = QLabel("待录制  ·  00:00")
        self.status.setMinimumWidth(165)
        row.addWidget(heading)
        for widget in (self.btn_start, self.btn_pause, self.btn_finish, self.btn_background, self.btn_open):
            row.addWidget(widget)
        row.addStretch()
        row.addWidget(self.status)
        outer.addLayout(row)

        self.options = QFrame()
        option_rows = QVBoxLayout(self.options)
        option_rows.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.addWidget(QLabel("输出"))
        self.mode = QComboBox()
        for label, value in (("分别 + 合成", "both"), ("分别保存", "separate"), ("合成一个视频", "combined")):
            self.mode.addItem(label, value)
        self.mode.setCurrentIndex(max(0, self.mode.findData(window.cfg.get("output_mode", "both"))))
        row.addWidget(self.mode)
        self.layout_choice = QComboBox()
        self.layout_choice.addItem("主次布局", "presentation")
        self.layout_choice.addItem("等宽布局", "equal")
        self.layout_choice.setCurrentIndex(max(0, self.layout_choice.findData(window.cfg.get("output_layout", "presentation"))))
        row.addWidget(self.layout_choice)
        self.title = QLineEdit(window.cfg.get("video_title", ""))
        self.title.setMaxLength(28)
        self.title.setPlaceholderText("视频标题（可选）")
        row.addWidget(self.title, 1)
        self.directory = QLineEdit(window.cfg["record_dir"])
        self.directory.setMinimumWidth(150)
        row.addWidget(self.directory, 1)
        browse = QPushButton("选择目录")
        browse.clicked.connect(self.browse)
        row.addWidget(browse)
        option_rows.addLayout(row)
        row = QHBoxLayout()
        self.mic = QCheckBox("电脑麦克风（讲解）")
        if sys.platform != "win32":
            self.mic.setEnabled(False)
            self.mic.setToolTip("电脑麦克风采集目前支持 Windows。设备播放声音仍可录制。")
        row.addWidget(self.mic)
        self.microphones = QComboBox()
        self.microphones.addItem("点击查找麦克风", None)
        self.microphones.setMinimumWidth(240)
        row.addWidget(self.microphones)
        self.btn_mics = QPushButton("查找麦克风")
        self.btn_mics.clicked.connect(self.refresh_microphones)
        row.addWidget(self.btn_mics)
        row.addStretch()
        self.hint = QLabel("设备播放声音在各面板勾选。最小化后继续录制；暂停时不采集声音。")
        self.hint.setWordWrap(True)
        row.addWidget(self.hint, 1)
        option_rows.addLayout(row)
        outer.addWidget(self.options)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setFixedHeight(15)
        self.progress.hide()
        outer.addWidget(self.progress)

        self.btn_start.clicked.connect(self.start)
        self.btn_pause.clicked.connect(self.pause_or_resume)
        self.btn_finish.clicked.connect(self.recorder.finish)
        self.btn_background.clicked.connect(window.hide_for_recording)
        self.btn_open.clicked.connect(self.open_output)
        self.recorder.changed.connect(self.on_state)
        self.recorder.elapsed.connect(self.on_elapsed)
        self.recorder.log.connect(window.append_log)
        self.recorder.progress.connect(self.on_progress)
        self.recorder.saved.connect(self.on_saved)
        self.recorder.failed.connect(self.on_failed)
        self.recorder.idle.connect(window._finish_close)
        self.microphones_found.connect(self.on_microphones)
        self._seconds = 0
        self._outcome = "待录制"
        self.on_state("idle")

    def browse(self):
        path = QFileDialog.getExistingDirectory(self, "选择录屏目录", self.directory.text())
        if path:
            self.directory.setText(path)

    def refresh_microphones(self):
        ffmpeg, _ = find_media_tools(self.window.cfg, self.window.resource_roots)
        if not ffmpeg:
            self.on_failed("请先在设置中选择 FFmpeg。")
            return
        self.btn_mics.setEnabled(False)
        def work():
            try:
                self.microphones_found.emit(list_microphones(ffmpeg), "")
            except Exception as error:
                self.microphones_found.emit([], str(error))
        threading.Thread(target=work, daemon=True).start()

    def on_microphones(self, names, error):
        self.btn_mics.setEnabled(True)
        selected = self.microphones.currentText()
        self.microphones.clear()
        for name in names:
            self.microphones.addItem(name, name)
        if not names:
            self.microphones.addItem("未找到麦克风", None)
            self.window.append_log(error or "未找到电脑麦克风，请检查 Windows 音频设备。")
        elif selected in names:
            self.microphones.setCurrentText(selected)

    def start(self):
        window = self.window
        if self.recorder.busy or window._closing:
            return
        devices = []
        seen = set()
        for panel in window.active_panels:
            serial = panel.current_serial()
            if not serial:
                continue
            device = panel._devices.get(serial)
            if not device or device.status != "device":
                self.on_failed(f"{panel.name.text()}：请连接并授权设备后再开始录制。")
                return
            if serial in seen:
                self.on_failed("每个面板请选择不同的设备。")
                return
            seen.add(serial)
            devices.append({"slot": panel.slot, "serial": serial, "title": panel.name.text().strip() or panel.slot,
                            "audio": panel.device_audio.isChecked()})
        if not devices:
            self.on_failed("请先选择至少一台已连接的设备。")
            return
        ffmpeg, ffprobe = find_media_tools(window.cfg, window.resource_roots)
        if not window.scrcpy_path or not window.adb.adb_path or not ffmpeg or not ffprobe:
            self.on_failed("录制工具尚未配置完整，请在设置中检查 adb、scrcpy 4.1+、FFmpeg 和 ffprobe。")
            return
        microphone = self.microphones.currentData() if self.mic.isChecked() else None
        if self.mic.isChecked() and not microphone:
            self.on_failed("请查找并选择电脑麦克风。")
            return
        if not self.directory.text().strip():
            self.on_failed("请选择录屏保存目录。")
            return
        folder = Path(self.directory.text().strip()).expanduser().resolve() / datetime.now().strftime("录屏_%Y%m%d_%H%M%S_%f")
        plan = {"folder": str(folder), "devices": devices, "microphone": microphone,
                "mode": self.mode.currentData(), "layout": self.layout_choice.currentData(),
                "title": self.title.text().strip(), "date": datetime.now().strftime("%Y.%m.%d  %H:%M")}
        try:
            config = window.validate_settings()
        except ValueError as error:
            self.on_failed(str(error))
            return
        window.cfg.update(record_dir=self.directory.text().strip(), output_mode=plan["mode"],
                          output_layout=plan["layout"], video_title=plan["title"])
        window.save_settings()
        self._seconds = 0
        self._outcome = "待录制"
        self.progress.hide()
        self.output_folder = str(folder)
        self.recorder.start(plan, config, window.scrcpy_path, window.adb.adb_path, ffmpeg, ffprobe, window.main_script)

    def pause_or_resume(self):
        if self.recorder.state == "paused":
            self.recorder.resume()
        else:
            self.recorder.pause()

    def on_state(self, state):
        busy = self.recorder.busy
        self.options.setEnabled(not busy)
        self.window.device_count.setEnabled(not busy and not self.window._closing)
        for panel in self.window.panels:
            panel.name.setEnabled(not busy)
            panel.device_audio.setEnabled(not busy)
            panel.combo.setEnabled(not busy and panel.session.proc is None)
        self.btn_start.setEnabled(not busy)
        self.btn_pause.setEnabled(state in ("recording", "paused"))
        self.btn_pause.setText("继续" if state == "paused" else "暂停")
        self.btn_finish.setEnabled(busy and state not in ("stopping", "exporting"))
        self.btn_background.setEnabled(busy)
        self.btn_open.setEnabled(bool(self.output_folder))
        labels = {"preparing": "准备中", "starting": "正在继续", "recording": "● 正在录制", "pausing": "正在暂停",
                  "paused": "已暂停", "stopping": "正在收尾", "exporting": "正在保存", "idle": self._outcome}
        self._label = labels.get(state, state)
        self.on_elapsed(self._seconds)
        if state == "exporting":
            self.progress.setValue(0)
            self.progress.show()

    def on_elapsed(self, seconds):
        self._seconds = seconds
        seconds = int(seconds)
        self.status.setText(f"{getattr(self, '_label', '待录制')}  ·  {seconds//60:02d}:{seconds%60:02d}")

    def on_progress(self, value, message):
        self.progress.setValue(value)
        self.progress.setFormat(message + "  %p%")

    def on_saved(self, result):
        self._outcome = "已保存"
        self.on_elapsed(result["duration"])
        self.output_folder = result["folder"]
        self.window.append_log("录制完成：" + "；".join(result["outputs"]))
        for note in result["notes"]:
            self.window.append_log(note)
        self.window.notify_saved(result)

    def on_failed(self, message):
        self._outcome = "请检查录制"
        self.window.append_log(message)
        self.status.setText("请检查录制，详情见运行记录")
        self.window.notify_failure(message)

    def open_output(self):
        if self.output_folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.output_folder))
