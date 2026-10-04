"""Generate clearly labelled synthetic media and Qt layout evidence, never use devices."""
from datetime import datetime
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QLabel
import dual_scrcpy_qt as m
from recording_capture import hidden_kwargs
from recording_export import export_recording

HERE = Path(__file__).resolve().parent
FOLDER = HERE / "recording-demo"
PARTS = FOLDER / ".parts"
PARTS.mkdir(parents=True, exist_ok=True)
APP = QApplication([])
APP.setStyle("Fusion")
APP.setStyleSheet(m.DARK_QSS)
FFMPEG, FFPROBE = shutil.which("ffmpeg"), shutil.which("ffprobe")


def source_image(slot, title, width, height):
    image = QImage(width, height, QImage.Format_RGB32)
    image.fill(QColor("#101925"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.fillRect(0, 0, width, 8, QColor("#51cdc0"))
    painter.setPen(QColor("#edf7ff"))
    painter.setFont(QFont("Microsoft YaHei", 26))
    painter.drawText(28, 65, title)
    painter.setFont(QFont("Microsoft YaHei", 13))
    painter.setPen(QColor("#8fa7bf"))
    painter.drawText(28, 105, "模拟画面 · 用于演示排版")
    labels = ["设备已连接", "画面与声音", "录制演示"]
    for i, label in enumerate(labels):
        y = 150 + i * ((height-240)//3)
        painter.setBrush(QColor("#223348"))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(24, y, width-48, (height-280)//3, 14, 14)
        painter.setPen(QColor("#dceafa"))
        painter.setFont(QFont("Microsoft YaHei", 17))
        painter.drawText(44, y+40, label)
        painter.setPen(QColor("#55c9c1"))
        painter.drawLine(44, y+58, width-54, y+58)
    painter.setFont(QFont("Microsoft YaHei", 12))
    painter.setPen(QColor("#8fa7bf"))
    painter.drawText(28, height-28, f"SCREEN  /  {slot}  /  SYNTHETIC")
    painter.end()
    path = PARTS / f"{slot}.png"
    image.save(str(path))
    return path


devices, segment, images = [], {}, []
for i, (slot, title, width, height) in enumerate((("A", "手机", 420, 840), ("B", "眼镜", 1280, 720), ("C", "设备 3", 960, 720))):
    path = source_image(slot, title, width, height)
    images.append(path)
    output = PARTS / f"000_{slot}.mp4"
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-loop", "1", "-i", str(path),
                    "-f", "lavfi", "-i", f"sine=frequency={440+i*220}:sample_rate=48000", "-t", "4",
                    "-vf", "drawbox=x=28:y=120:w=iw-56:h=4:color=0x55c9c1:t=fill:enable='lt(mod(t,1),0.5)'",
                    "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-r", "30",
                    "-c:a", "aac", str(output)], check=True, capture_output=True, **hidden_kwargs())
    devices.append({"slot": slot, "title": title, "audio": True})
    segment[slot] = str(output)
result = export_recording({"folder": str(FOLDER), "devices": devices, "segments": [segment], "mode": "both",
                           "layout": "presentation", "title": "屏幕录制 · 模拟画面", "date": "2026.10.04"}, FFMPEG, FFPROBE)
subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", result["outputs"][-1],
                "-ss", "1", "-frames:v", "1", str(HERE / "07-composite-preview.png")], check=True, **hidden_kwargs())
settings = QSettings(str(PARTS / "preview-settings.ini"), QSettings.IniFormat)
settings.setValue("device_count", "2")
settings.setValue("video_title", "")
settings.setValue("record_dir", "D:/Recordings")
with patch.object(m.AdbHelper, "list_devices", return_value=[]):
    win = m.MainWindow(settings)
win.resize(1280, 760)
win.show()
APP.processEvents()
win.grab().save(str(HERE / "05-independent-recording-ui.png"))
with patch.object(m.AdbHelper, "list_devices", return_value=[m.AdbDevice(f"SYNTHETIC_{slot}", "device", f"模拟设备 {slot}") for slot in "ABC"]):
    win.device_count.setCurrentIndex(2)
for panel, path in zip(win.panels, images):
    panel.placeholder.hide()
    label = QLabel()
    label.setAlignment(Qt.AlignCenter)
    label.setPixmap(QPixmap(str(path)).scaled(380, 350, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    panel.video_area.layout().addWidget(label)
    panel.status.setText("模拟画面")
APP.processEvents()
win.grab().save(str(HERE / "06-three-device-layout.png"))
win.quit_after_recording()
APP.processEvents()
print("Synthetic demo:", result["outputs"][-1])
